import base64
import json
import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .authentication import JengaAuthentication
from .cart import Cart
from .wishlist import Wishlist
from .forms import CheckoutForm, ContactForm, RegisterForm, LoginForm
from .models import (
    Product, ProductVariant, Category, Order, OrderItem, Payment, ContactMessage,
    ProductReview, StoreReview,
)
from .services import JengaClient, generate_reference
from .whatsapp import build_contact_whatsapp_link

logger = logging.getLogger(__name__)


def _parse_review_submission(request):
    """
    Shared name/rating/comment parsing for both product and store
    review forms - anyone can submit (no account needed), so this is
    plain manual validation rather than a ModelForm.
    Returns (name, email, rating, comment, errors).
    """
    name = (request.POST.get("name") or "").strip()
    email = (request.POST.get("email") or "").strip()
    comment = (request.POST.get("comment") or "").strip()

    errors = []
    if not name:
        errors.append("Please tell us your name.")

    rating = None
    raw_rating = request.POST.get("rating")
    try:
        rating = int(raw_rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Please choose a star rating.")
        rating = None

    return name, email, rating, comment, errors


# ---- storefront pages -------------------------------------------------

def home(request):
    featured_products = list(Product.objects.filter(is_active=True)[:6])

    # The hero has 3 image slots (1 large + 2 small) that each cycle
    # through the same pool of products, offset so they don't all show
    # the same photo at once. Pad to 3 with None so the template/CSS
    # always has exactly 3 slides to animate, even with 0-2 products.
    hero_pool = (featured_products[:3] + [None, None, None])[:3]
    hero_main = hero_pool
    hero_thumb_a = [hero_pool[1], hero_pool[2], hero_pool[0]]
    hero_thumb_b = [hero_pool[2], hero_pool[0], hero_pool[1]]

    wishlist_ids = set(Wishlist(request).product_ids)
    store_reviews = list(StoreReview.approved()[:6])

    # Customer stories slideshow: highest-rated approved product reviews
    # (ties broken by most recent). Falls back to the hardcoded defaults
    # baked into the template when there aren't any yet.
    top_product_reviews = list(
        ProductReview.objects.filter(is_approved=True)
        .select_related("product")
        .order_by("-rating", "-created_at")[:6]
    )

    return render(request, "Bella/home.html", {
        "featured_products": featured_products,
        "hero_main": hero_main,
        "hero_thumb_a": hero_thumb_a,
        "hero_thumb_b": hero_thumb_b,
        "wishlist_ids": wishlist_ids,
        "store_reviews": store_reviews,
        "store_rating": StoreReview.average_rating(),
        "store_rating_percent": StoreReview.average_rating_percent(),
        "store_review_count": StoreReview.review_count(),
        "top_product_reviews": top_product_reviews,
    })


def about(request):
    return render(request, "Bella/about.html")


def product_list(request):
    products = Product.objects.filter(is_active=True)
    categories = Category.objects.all()

    category_slug = request.GET.get("category")
    if category_slug:
        products = products.filter(category__slug=category_slug)

    query = request.GET.get("q")
    if query:
        products = products.filter(name__icontains=query)

    wishlist_ids = set(Wishlist(request).product_ids)

    return render(request, "Bella/products_list.html", {
        "products": products,
        "categories": categories,
        "active_category": category_slug,
        "query": query or "",
        "wishlist_ids": wishlist_ids,
    })


def product_search_suggest(request):
    """
    JSON typeahead for the products-page search box. Returns a short list
    of name/image/price/url matches for whatever's been typed so far -
    powers the autosuggest dropdown as the person types, without a full
    page reload. Wire this up in urls.py, e.g.:

        path("products/search-suggest/", views.product_search_suggest, name="product_search_suggest"),
    """
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    products = (
        Product.objects.filter(is_active=True, name__icontains=query)
        .order_by("name")[:8]
    )

    results = [
        {
            "name": product.name,
            "url": reverse("product_detail", args=[product.slug]),
            "image": product.image.url if product.image else "",
            "price": str(product.price),
        }
        for product in products
    ]
    return JsonResponse({"results": results})


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)

    if request.method == "POST":
        name, email, rating, comment, errors = _parse_review_submission(request)
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            ProductReview.objects.create(
                product=product, name=name, email=email, rating=rating, comment=comment
            )
            messages.success(request, "Thanks for your review!")
        return redirect("product_detail", slug=product.slug)

    variants = product.ordered_variants() if product.has_variants() else None

    recommended = Product.objects.filter(is_active=True).exclude(id=product.id)
    if product.category:
        recommended = recommended.filter(category=product.category)
    recommended = list(recommended[:4])
    if len(recommended) < 4:
        # Not enough in the same category - top up with other active products.
        seen_ids = {product.id, *(p.id for p in recommended)}
        extra = Product.objects.filter(is_active=True).exclude(id__in=seen_ids)[:4 - len(recommended)]
        recommended += list(extra)

    wishlist_ids = set(Wishlist(request).product_ids)

    return render(request, "Bella/product_detail.html", {
        "product": product,
        "variants": variants,
        "default_variant": product.default_variant(),
        "recommended_products": recommended,
        "wishlist_ids": wishlist_ids,
        "in_wishlist": product.id in wishlist_ids,
        "reviews": product.approved_reviews(),
    })


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact_message = ContactMessage.objects.create(**form.cleaned_data)
            # Stash the WhatsApp link in the session rather than passing it
            # straight to a template - we redirect after POST (so a page
            # refresh doesn't resubmit the form), and the link needs to
            # survive that redirect. Popped below so it only shows once,
            # right after the submit that generated it.
            request.session["last_contact_whatsapp_link"] = build_contact_whatsapp_link(contact_message)
            messages.success(request, "Thanks for reaching out - we'll reply soon.")
            return redirect("contact")
        else:
            # Field-level errors already render inline under each input,
            # but without this the page just re-renders with no visible
            # sign that the submit even happened - easy to miss, especially
            # if all the invalid fields are scrolled out of view.
            messages.error(request, "Please fix the errors below and try again.")
    else:
        form = ContactForm()

    whatsapp_link = request.session.pop("last_contact_whatsapp_link", None)
    return render(request, "Bella/contact.html", {"form": form, "whatsapp_link": whatsapp_link})


def store_reviews(request):
    """
    Overall store/service rating - not tied to any one product. Same
    open-submission, admin-approval flow as product reviews.
    """
    if request.method == "POST":
        name, email, rating, comment, errors = _parse_review_submission(request)
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            StoreReview.objects.create(name=name, email=email, rating=rating, comment=comment)
            messages.success(request, "Thanks for the feedback! It'll appear once we've approved it.")
        return redirect("store_reviews")

    return render(request, "Bella/reviews.html", {
        "reviews": StoreReview.approved(),
        "average_rating": StoreReview.average_rating(),
        "average_rating_percent": StoreReview.average_rating_percent(),
        "review_count": StoreReview.review_count(),
    })


# ---- accounts -------------------------------------------------------------

def register(request):
    if request.user.is_authenticated:
        return redirect("account_orders")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            # login() needs to know which backend authenticated this user -
            # we created it directly rather than via authenticate(), so it
            # has to be told explicitly. Update the path below if your app
            # label isn't "Bella".
            login(request, user, backend="Bella.backends.EmailBackend")
            messages.success(request, f"Welcome, {user.first_name or 'there'}! Your account is ready.")
            return redirect(request.POST.get("next") or "account_orders")
    else:
        form = RegisterForm()

    return render(request, "Bella/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("account_orders")

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                messages.success(request, "Signed in successfully.")
                return redirect(request.POST.get("next") or "account_orders")
            form.add_error(None, "That email and password don't match.")
    else:
        form = LoginForm()

    return render(request, "Bella/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You've been signed out.")
    return redirect("home")


@login_required(login_url="login")
def account_orders(request):
    """
    Order history for the signed-in customer. Only orders placed while
    logged in are linked to the account (see Order.user) - guest
    checkouts made before signing up aren't retroactively attached.
    """
    orders = (
        request.user.orders
        .select_related("payment")
        .prefetch_related("items__product")
    )
    return render(request, "Bella/account.html", {"orders": orders})


# ---- cart ---------------------------------------------------------------

def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _cart_payload(cart):
    """Serialize the cart for the header drawer / any JS consumer."""
    items = []
    for item in cart:
        product = item["product"]
        variant = item.get("variant")
        price = item["price"]
        quantity = item["quantity"]
        items.append({
            "product_id": product.id,
            "variant_id": variant.id if variant else None,
            "name": f"{product.name} — {variant.name}" if variant else product.name,
            "price": str(price),
            "quantity": quantity,
            "subtotal": str(price * quantity),
            "image": product.image.url if product.image else "",
            "url": reverse("product_detail", args=[product.slug]),
            "remove_url": reverse("cart_remove", args=[product.id]),
            "update_url": reverse("cart_update", args=[product.id]),
        })
    return {
        "count": len(cart),
        "total": str(cart.get_total_price()),
        "items": items,
    }


def cart_summary(request):
    """JSON snapshot of the current bag, used to populate the header drawer."""
    cart = Cart(request)
    return JsonResponse(_cart_payload(cart))


@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)

    variant = None
    if product.has_variants():
        variant_id = request.POST.get("variant_id")
        variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None
        if variant is None:
            error = "Please choose an option before adding to your bag."
            if _is_ajax(request):
                return JsonResponse({"error": error}, status=400)
            messages.error(request, error)
            return redirect(request.POST.get("next") or reverse("product_detail", args=[product.slug]))

    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    cart.add(product=product, quantity=quantity, variant=variant)
    label = f"{product.name} ({variant.name})" if variant else product.name
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.success(request, f"{label} added to your bag.")
    return redirect(request.POST.get("next") or "cart_detail")


@require_POST
def cart_remove(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get("variant_id")
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None

    cart = Cart(request)
    cart.remove(product, variant=variant)
    label = f"{product.name} ({variant.name})" if variant else product.name
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.info(request, f"{label} removed from your bag.")
    return redirect("cart_detail")


@require_POST
def cart_update(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get("variant_id")
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None

    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    if quantity <= 0:
        cart.remove(product, variant=variant)
    else:
        cart.add(product=product, quantity=quantity, update_quantity=True, variant=variant)
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    return redirect("cart_detail")


def cart_detail(request):
    cart = Cart(request)
    return render(request, "Bella/cart.html", {"cart": cart})


# ---- wishlist -------------------------------------------------------------

def _wishlist_payload(wishlist):
    items = []
    for product in wishlist:
        items.append({
            "product_id": product.id,
            "name": product.name,
            "price": str(product.price),
            "image": product.image.url if product.image else "",
            "url": reverse("product_detail", args=[product.slug]),
            "remove_url": reverse("wishlist_remove", args=[product.id]),
            "add_to_cart_url": reverse("cart_add", args=[product.id]),
        })
    return {
        "count": len(wishlist),
        "items": items,
    }


def wishlist_summary(request):
    """JSON snapshot of the wishlist, used to populate the header drawer."""
    return JsonResponse(_wishlist_payload(Wishlist(request)))


@require_POST
def wishlist_toggle(request, product_id):
    """Add the product to the wishlist, or remove it if it's already there."""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    wishlist = Wishlist(request)
    added = wishlist.toggle(product)

    if _is_ajax(request):
        payload = _wishlist_payload(wishlist)
        payload["added"] = added
        payload["product_id"] = product.id
        return JsonResponse(payload)

    if added:
        messages.success(request, f"{product.name} added to your wishlist.")
    else:
        messages.info(request, f"{product.name} removed from your wishlist.")
    return redirect(request.POST.get("next") or "product_list")


@require_POST
def wishlist_remove(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    wishlist = Wishlist(request)
    wishlist.remove(product)
    if _is_ajax(request):
        return JsonResponse(_wishlist_payload(wishlist))
    messages.info(request, f"{product.name} removed from your wishlist.")
    return redirect(request.POST.get("next") or "product_list")


# ---- checkout / payment --------------------------------------------------

def checkout(request):
    cart = Cart(request)

    if len(cart) == 0:
        messages.warning(request, "Your bag is empty.")
        return redirect("product_list")

    if request.method == "POST":
        form = CheckoutForm(request.POST)

        if form.is_valid():
            name = form.cleaned_data["name"]
            email = form.cleaned_data["email"]
            phone = form.cleaned_data["phone"]

            order_reference = generate_reference("OR")
            payment_reference = generate_reference("PR")
            total_amount = cart.get_total_price()

            order = Order.objects.create(
                order_reference=order_reference,
                user=request.user if request.user.is_authenticated else None,
                name=name,
                email=email,
                phone=phone,
                total_amount=total_amount,
                status="Pending",
            )

            OrderItem.objects.bulk_create([
                OrderItem(
                    order=order,
                    product=item["product"],
                    quantity=item["quantity"],
                    price=item["price"],
                )
                for item in cart
            ])

            payment = Payment.objects.create(
                order=order,
                order_reference=order_reference,
                payment_reference=payment_reference,
                name=name,
                email=email,
                phone=phone,
                amount=total_amount,
                status="Pending",
            )

            if settings.JENGA_LIVE_ENABLED:
                client = JengaClient()

                try:
                    result = client.initiate_checkout(
                        order_reference=order_reference,
                        payment_reference=payment_reference,
                        customer_name=name,
                        customer_email=email,
                        phone_number=phone,
                        amount=total_amount,
                        description=f"Fashion order {order_reference}",
                    )
                except requests.HTTPError:
                    logger.exception(
                        "Jenga checkout STK push failed for %s", payment_reference
                    )
                    payment.status = "Failed"
                    payment.save(update_fields=["status", "updated_at"])
                    order.status = "Failed"
                    order.save(update_fields=["status", "updated_at"])
                    form.add_error(
                        None, "Could not reach the payment gateway. Please try again."
                    )
                    return render(
                        request,
                        "Bella/checkout.html",
                        {"form": form, "cart": cart, "jenga_live": settings.JENGA_LIVE_ENABLED},
                    )

                if not result.get("status"):
                    # Jenga responded but rejected the request (e.g. bad params).
                    payment.status = "Failed"
                    payment.save(update_fields=["status", "updated_at"])
                    order.status = "Failed"
                    order.save(update_fields=["status", "updated_at"])
                    form.add_error(
                        None, result.get("message", "Failed to initiate payment.")
                    )
                    return render(
                        request,
                        "Bella/checkout.html",
                        {"form": form, "cart": cart, "jenga_live": settings.JENGA_LIVE_ENABLED},
                    )

                payment.invoice_number = result.get("data", {}).get("invoiceNumber", "")
                payment.save(update_fields=["invoice_number", "updated_at"])

                # Push was accepted - the customer now has an M-Pesa prompt on
                # their phone. Final status arrives via payment_callback below.

            # else: JENGA_LIVE_ENABLED is off (e.g. live account not yet
            # approved). We still record the order/payment as normal, we
            # just don't touch JengaClient at all - no STK push is
            # attempted, and payment stays "Pending" until it's confirmed
            # over WhatsApp and updated by hand in admin. The moment the
            # live account is approved, flip JENGA_LIVE_ENABLED back to
            # True in settings and this whole branch starts firing again
            # with zero other code changes.

            request.session["last_order_reference"] = order_reference
            cart.clear()

            return redirect("success")
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {
                "name": request.user.get_full_name() or request.user.first_name,
                "email": request.user.email,
            }
        form = CheckoutForm(initial=initial)

    return render(
        request,
        "Bella/checkout.html",
        {"form": form, "cart": cart, "jenga_live": settings.JENGA_LIVE_ENABLED},
    )


def success(request):
    """
    Right after checkout, we only know the order via the session. Hand
    off immediately to order_confirmation(), which is keyed by
    order_reference instead - that's the URL that's actually safe to
    bookmark, reload, or share, and it keeps working no matter when or
    how the order's status changes later (Jenga callback or a manual
    edit in admin).
    """
    order_reference = request.session.get("last_order_reference")
    if not order_reference:
        messages.info(request, "We couldn't find a recent order for this session.")
        return redirect("product_list")
    return redirect("order_confirmation", order_reference=order_reference)


def order_confirmation(request, order_reference):
    """
    Durable, session-independent confirmation page. Anyone with the
    order_reference (the customer revisiting/bookmarking this URL, or
    an admin sharing it) always gets an up-to-date WhatsApp link built
    fresh from the order's current data - it doesn't matter whether the
    order is Pending or was later marked Completed by the Jenga callback
    or by hand in Django admin.
    """
    order = get_object_or_404(
        Order.objects.prefetch_related("items__product"), order_reference=order_reference
    )
    whatsapp_link = order.get_whatsapp_link()
    return render(request, "Bella/success.html", {
        "order": order,
        "whatsapp_link": whatsapp_link,
        "jenga_live": settings.JENGA_LIVE_ENABLED,
    })


def test_authentication(request):
    auth = JengaAuthentication()
    token = auth.get_access_token()
    return JsonResponse(token)


def _verify_callback_auth(request):
    """
    Jenga's IPN callback (Settings > IPNs on the portal) is sent with an
    `Authorization: Basic ...` header, where the username/password are
    whatever you configured when you registered the callback URL on
    JengaHQ - these should match JENGA_CALLBACK_USERNAME /
    JENGA_CALLBACK_PASSWORD in settings. This stops anyone who discovers
    /callback/ from POSTing a fake "payment succeeded" and flipping an
    order to Completed.

    Sandbox note: while testing, some sandbox setups don't send this
    header on the STK-push callbackUrl payload (Shape 1 below) - only
    on the IPN payload (Shape 2). We only hard-require it for Shape 2;
    Shape 1 is logged either way so you can see what your sandbox
    account actually sends and tighten this once you've confirmed it.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False

    return (
        username == settings.JENGA_CALLBACK_USERNAME
        and password == settings.JENGA_CALLBACK_PASSWORD
    )


@csrf_exempt
def payment_callback(request):
    if request.method != "POST":
        return JsonResponse({
            "message": "Only POST requests are allowed."
        }, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError) as e:
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=400)

    authorized = _verify_callback_auth(request)
    logger.info(
        "Jenga callback received (authorized=%s): %s", authorized, json.dumps(data)
    )

    # Shape 2 (IPN) is the one Jenga docs confirm carries the Basic Auth
    # header you configure yourself, so it's safe to reject outright.
    # Shape 1 (the raw STK-push callbackUrl payload) isn't documented the
    # same way, so during sandbox testing we log-but-allow it rather than
    # silently dropping real test callbacks while you confirm the header.
    if data.get("callbackType") == "IPN" and not authorized:
        logger.warning("Rejected unauthorized IPN callback: %s", data)
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    def sync_payment(lookup_field, lookup_value, status, telco_reference):
        payment = Payment.objects.filter(**{lookup_field: lookup_value}).first()
        if not payment:
            logger.warning("Callback for unknown %s: %s", lookup_field, lookup_value)
            return
        payment.status = status
        payment.telco_reference = telco_reference
        payment.save(update_fields=["status", "telco_reference", "updated_at"])
        if payment.order_id:
            Order.objects.filter(id=payment.order_id).update(status=status)

    # Shape 1: the "Mpesa Confirmation Callback" sent to payment.callbackUrl
    # { "transactionReference": "<paymentReference>", "data": { "Body": {
    #     "stkCallback": { "ResultCode": 0, "CallbackMetadata": {...} } } } }
    if "transactionReference" in data:
        reference = data["transactionReference"]
        stk_callback = data.get("data", {}).get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        status = "Completed" if result_code == 0 else "Failed"

        receipt = ""
        for item in stk_callback.get("CallbackMetadata", {}).get("Item", []):
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = item.get("Value", "")

        sync_payment("payment_reference", reference, status, receipt)

    # Shape 2: the IPN callback, if you register one under Settings > IPNs
    # on the Jenga portal (recommended by Jenga as the source of truth).
    elif data.get("callbackType") == "IPN":
        order_reference = data.get("customer", {}).get("reference")
        txn = data.get("transaction", {})
        status = "Completed" if txn.get("status") == "SUCCESS" else "Failed"

        sync_payment("order_reference", order_reference, status, txn.get("reference", ""))

    return JsonResponse({
        "status": "success",
        "message": "Callback received"
    })

def privacy_policy(request):
    return render(request, "Bella/privacy.html")


def terms_of_service(request):
    return render(request, "Bella/terms.html")