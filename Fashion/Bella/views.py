import base64
import calendar
import csv
import io
import json
import logging
from datetime import date, datetime, timedelta

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .authentication import JengaAuthentication
from .cart import Cart
from .wishlist import Wishlist
from .forms import *
from .models import (
    Product, ProductVariant, ProductColour, Category, Order, OrderItem, Payment, ContactMessage,
    ProductReview, StoreReview,
)
from .services import JengaClient, generate_reference
from . import shipping
from .whatsapp import build_contact_whatsapp_link
from .notifications import notify_new_order, notify_new_review, notify_contact_message
from .emails import (
    send_order_placed_email,
    send_payment_confirmed_email,
    send_payment_failed_email,
    send_order_shipped_email,
    send_order_delivered_email,
)
from django.http import HttpResponse
from django.contrib import messages
from django.db.models import Count, F, Min, ProtectedError, Q, Sum
from django.db.models.functions import TruncDate, TruncHour, TruncMonth
 
from .decorators import manager_required
from .models import Category, Order, Payment, Product, ManagerProfile
 
# Every variant formset in this module shares this prefix, both when
# rendering (product_create/product_edit) and in the add-row JS in
# product_form.html (id_variants-TOTAL_FORMS etc). Keep them in sync if
# this ever changes.
VARIANT_FORMSET_PREFIX = "variants"

# Same idea, for the colours formset (id_colours-TOTAL_FORMS etc).
COLOUR_FORMSET_PREFIX = "colours"
 
logger = logging.getLogger(__name__)


def _paginate(request, queryset, per_page=10):
    """
    Shared pager for the manager list screens. Reads ?page= from the
    request, clamps out-of-range values to the nearest valid page
    (rather than 404ing - a stale bookmarked/back-button page number
    shouldn't dead-end the manager), and also hands back the current
    filters as a query string with `page` stripped out, so pagination
    links can append their own page number without dropping search/
    filter params.
    """
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get("page")
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    params = request.GET.copy()
    params.pop("page", None)
    querystring = params.urlencode()

    # Elided page list, e.g. [1, ellipsis, 4, 5, 6, 7, 8, ellipsis, 20] -
    # lets the template just loop over numbers/ellipsis markers instead
    # of re-deriving the "show a window around the current page" logic
    # itself.
    page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)

    return page_obj, querystring, page_range


def _get_manager_profile(user):
    """
    Get-or-create so existing managers (created before ManagerProfile
    existed) don't 404 or 500 the first time they hit Settings or the
    sidebar's avatar picker - a row is lazily created on first touch.
    """
    profile, _ = ManagerProfile.objects.get_or_create(user=user)
    return profile


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /cart/",
        "Disallow: /checkout/",
        "Disallow: /account/",
        "Disallow: /callback/",
        "Disallow: /authenticate/",
        "Disallow: /wishlist/",
        "",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
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
            notify_new_review("product", name, rating, comment)
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
        "colours": product.ordered_colours() if product.has_colours() else None,
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
            notify_contact_message(contact_message)
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
            notify_new_review("store", name, rating, comment)
            messages.success(request, "Thanks for the feedback! It'll appear once we've approved it.")
        return redirect("store_reviews")

    return render(request, "Bella/reviews.html", {
        "reviews": StoreReview.approved(),
        "average_rating": StoreReview.average_rating(),
        "average_rating_percent": StoreReview.average_rating_percent(),
        "review_count": StoreReview.review_count(),
    })


# ---- accounts -------------------------------------------------------------

def _post_login_redirect(user):
    """Where to send someone right after signing in, with no explicit `next`."""
    return "manager_dashboard" if user.is_staff else "account_orders"


def register(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request.user))

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
            return redirect(request.POST.get("next") or _post_login_redirect(user))
    else:
        form = RegisterForm()

    return render(request, "Bella/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.GET.get("next") or _post_login_redirect(request.user))

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
                return redirect(request.POST.get("next") or _post_login_redirect(user))
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


@login_required(login_url="login")
def account_order_detail(request, order_reference):
    """
    JSON order detail for the "View Details" modal on the account page.
    Deliberately scoped to `request.user` - an order_reference alone
    isn't authorization to see someone else's order (unlike
    order_confirmation, which is the public, bookmarkable page reached
    right after checkout and is keyed by the reference on purpose).
    """
    order = get_object_or_404(
        Order.objects.select_related("payment").prefetch_related("items__product"),
        order_reference=order_reference, user=request.user,
    )

    # Products the customer has already left a review for, so the
    # modal can show "Update your rating" instead of a blank form for
    # anything they've rated before (matched on this account's email -
    # ProductReview has no FK back to the account since anyone, signed
    # in or not, can leave one).
    reviewed = {
        r.product_id: {"rating": r.rating, "comment": r.comment}
        for r in ProductReview.objects.filter(
            product__in=[item.product_id for item in order.items.all()],
            email__iexact=request.user.email,
        )
    }

    items = [
        {
            "product_id": item.product_id,
            "product_name": item.product.name,
            "product_url": reverse("product_detail", args=[item.product.slug]) if item.product.is_active else None,
            "image": item.product.image.url if item.product.image else "",
            "quantity": item.quantity,
            "price": str(item.price),
            "subtotal": str(item.subtotal()),
            "colour_name": item.colour_name,
            "colour_hex": item.colour_hex,
            "custom_request": item.custom_request,
            "existing_review": reviewed.get(item.product_id),
        }
        for item in order.items.all()
    ]

    delivery_timeline = order.delivery_timeline() if order.status == "Completed" else []

    return JsonResponse({
        "order_reference": order.order_reference,
        "status": order.status,
        "delivery_status": order.get_delivery_status_display(),
        "delivery_timeline": [
            {"key": s["key"], "label": s["label"], "is_done": s["is_done"]} for s in delivery_timeline
        ],
        "created_at": date_format(timezone.localtime(order.created_at), "j M Y, g:i A"),
        "subtotal_amount": str(order.subtotal_amount),
        "shipping_fee": str(order.shipping_fee),
        "vat_amount": str(order.vat_amount),
        "total_amount": str(order.total_amount),
        "delivery_area": order.delivery_area,
        "delivery_zone": order.delivery_zone,
        "whatsapp_link": order.get_whatsapp_link(),
        "confirmation_url": reverse("order_confirmation", args=[order.order_reference]),
        "items": items,
    })


@login_required(login_url="login")
@require_POST
def account_submit_review(request, product_id):
    """
    Star-rating + optional comment submitted from the order-details
    modal on the account page. Only lets a customer rate a product
    they've actually been sent in a completed order of theirs - this
    is the one review entry point that's gated that way, since the
    open one on the product page itself has no such requirement.
    """
    product = get_object_or_404(Product, id=product_id)

    has_ordered = OrderItem.objects.filter(
        order__user=request.user, product_id=product_id
    ).exists()
    if not has_ordered:
        return JsonResponse({"error": "You can only rate products from your own orders."}, status=403)

    raw_rating = request.POST.get("rating")
    try:
        rating = int(raw_rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({"error": "Please choose a star rating."}, status=400)

    comment = (request.POST.get("comment") or "").strip()
    name = request.user.get_full_name().strip() or request.user.email

    # One review per (product, account) - re-submitting from the modal
    # updates it in place rather than piling up duplicates, and resets
    # it to unapproved so the edited version gets checked before it
    # goes back on the storefront.
    review, _ = ProductReview.objects.update_or_create(
        product=product, email__iexact=request.user.email,
        defaults={"name": name, "email": request.user.email, "rating": rating, "comment": comment, "is_approved": False},
    )
    notify_new_review("product", name, rating, comment)

    return JsonResponse({
        "success": True,
        "rating": review.rating,
        "comment": review.comment,
    })


# ---- cart ---------------------------------------------------------------

def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _cart_payload(cart):
    """Serialize the cart for the header drawer / any JS consumer."""
    items = []
    for item in cart:
        product = item["product"]
        variant = item.get("variant")
        colour = item.get("colour")
        price = item["price"]
        quantity = item["quantity"]
        custom_request = item.get("custom_request") or ""
        name = product.name
        if variant:
            name += f" — {variant.name}"
        if colour:
            name += f" ({colour.name})"
        items.append({
            "product_id": product.id,
            "variant_id": variant.id if variant else None,
            "colour_id": colour.id if colour else None,
            "custom_request": custom_request,
            "name": name,
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

    # Free-text customisation from the "Need something specific?" modal -
    # e.g. a colour that isn't in the swatch list, a sizing tweak, a
    # monogram, etc. It's an alternative to picking a swatch, not an
    # addition to it, so a customer describing a custom colour doesn't
    # also have to tick one of the listed ones.
    custom_request = (request.POST.get("custom_request") or "").strip()

    colour = None
    if product.has_colours():
        colour_id = request.POST.get("colour_id")
        colour = get_object_or_404(ProductColour, id=colour_id, product=product) if colour_id else None
        if colour is None and not custom_request:
            error = "Please choose a colour, or tell us what you need, before adding to your bag."
            if _is_ajax(request):
                return JsonResponse({"error": error}, status=400)
            messages.error(request, error)
            return redirect(request.POST.get("next") or reverse("product_detail", args=[product.slug]))

    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    cart.add(product=product, quantity=quantity, variant=variant, colour=colour, custom_request=custom_request)
    label = f"{product.name} ({variant.name})" if variant else product.name
    if colour:
        label += f" — {colour.name}"
    elif custom_request:
        label += " — custom request"
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.success(request, f"{label} added to your bag.")
    return redirect(request.POST.get("next") or "cart_detail")


@require_POST
def cart_remove(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get("variant_id")
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None
    colour_id = request.POST.get("colour_id")
    colour = get_object_or_404(ProductColour, id=colour_id, product=product) if colour_id else None
    # Round-tripped from the hidden field the cart page renders for this
    # line, so the same hashed key that was used to add it can be
    # rebuilt here to remove it.
    custom_request = (request.POST.get("custom_request") or "").strip()

    cart = Cart(request)
    cart.remove(product, variant=variant, colour=colour, custom_request=custom_request)
    label = f"{product.name} ({variant.name})" if variant else product.name
    if colour:
        label += f" — {colour.name}"
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.info(request, f"{label} removed from your bag.")
    return redirect("cart_detail")


@require_POST
def cart_update(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get("variant_id")
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None
    colour_id = request.POST.get("colour_id")
    colour = get_object_or_404(ProductColour, id=colour_id, product=product) if colour_id else None
    custom_request = (request.POST.get("custom_request") or "").strip()

    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    if quantity <= 0:
        cart.remove(product, variant=variant, colour=colour, custom_request=custom_request)
    else:
        cart.add(
            product=product, quantity=quantity, update_quantity=True,
            variant=variant, colour=colour, custom_request=custom_request,
        )
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

def _checkout_context(form, cart):
    """
    Shared context for every checkout.html render. `area_data_json` is
    the {area_name: {zone, fee, lat, lng}} map the page's JS uses both
    to update the summary box the instant an area is picked/changed,
    and to match a dropped map pin to its nearest priced area in the
    location modal - purely client-side convenience, since the real
    fee is always (re)looked-up server-side in checkout() itself.
    """
    return {
        "form": form,
        "cart": cart,
        "jenga_live": settings.JENGA_LIVE_ENABLED,
        "area_data_json": json.dumps(shipping.area_data_for_js()),
        "vat_rate_percent": int(shipping.VAT_RATE * 100),
    }


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

            # Fee is looked up server-side from the area name - a client
            # could tamper with any price shown in the page, but they
            # can't tamper with what shipping.fee_for_area() returns for
            # a given area, since that table lives on the server.
            delivery_area = form.cleaned_data["delivery_area"]
            shipping_fee = shipping.fee_for_area(delivery_area)
            if shipping_fee is None:
                # Belt-and-braces - clean_delivery_area() already rejects
                # unknown areas, so this shouldn't be reachable.
                form.add_error("delivery_area", "Please choose a delivery area from the list.")
                return render(
                    request,
                    "Bella/checkout.html",
                    _checkout_context(form, cart),
                )

            subtotal_amount = cart.get_total_price()
            vat_amount = shipping.calculate_vat(subtotal_amount)
            total_amount = subtotal_amount + shipping_fee + vat_amount

            order_reference = generate_reference("OR")
            payment_reference = generate_reference("PR")

            order = Order.objects.create(
                order_reference=order_reference,
                user=request.user if request.user.is_authenticated else None,
                name=name,
                email=email,
                phone=phone,
                subtotal_amount=subtotal_amount,
                shipping_fee=shipping_fee,
                vat_amount=vat_amount,
                delivery_area=delivery_area,
                delivery_zone=shipping.zone_for_area(delivery_area) or "",
                delivery_address=form.cleaned_data.get("delivery_address", ""),
                delivery_notes=form.cleaned_data.get("delivery_notes", ""),
                delivery_lat=form.cleaned_data.get("delivery_lat"),
                delivery_lng=form.cleaned_data.get("delivery_lng"),
                total_amount=total_amount,
                status="Pending",
            )

            order_items = OrderItem.objects.bulk_create([
                OrderItem(
                    order=order,
                    product=item["product"],
                    quantity=item["quantity"],
                    price=item["price"],
                    colour_name=item["colour"].name if item["colour"] else "",
                    colour_hex=item["colour"].hex_code if item["colour"] else "",
                    custom_request=item.get("custom_request") or "",
                )
                for item in cart
            ])

            # Customer always hears from us the instant the order is
            # placed, even before we know whether payment will succeed -
            # the email says as much. WhatsApp for this first touchpoint
            # stays customer-initiated (the tap-to-send link on the
            # confirmation page), since we don't have their "yes, message
            # me" consent for an unprompted business-initiated WhatsApp
            # message yet at this exact moment.
            send_order_placed_email(order, order_items)
            notify_new_order(order)

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
                        _checkout_context(form, cart),
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
                        _checkout_context(form, cart),
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
        _checkout_context(form, cart),
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


def _verify_automation_auth(request):
    """
    Shared-secret HTTP Basic Auth for internal automation callers (e.g.
    n8n scheduled workflows) - same shape as _verify_callback_auth just
    above, for the same reason: anyone who finds one of these URLs
    without the configured AUTOMATION_USERNAME/AUTOMATION_PASSWORD gets
    a 401, not order/payment data.
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
        username == settings.AUTOMATION_USERNAME
        and password == settings.AUTOMATION_PASSWORD
    )


@csrf_exempt
def automation_failed_payments_report(request):
    """
    Read-only JSON report of payments that moved to "Failed" within the
    last `window_minutes` (default 60) - built for a scheduled n8n
    workflow (Schedule Trigger -> HTTP Request -> IF -> notify manager).

    GET only, no side effects, never writes anything - this only reads
    Payment/Order rows that already exist from the normal checkout /
    payment_callback flow.

    Pass ?window_minutes=N matching your n8n Schedule Trigger's
    interval, so each failure is reported once, right after it happens,
    without needing a separate "already alerted" flag/table.
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    if not _verify_automation_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        window_minutes = int(request.GET.get("window_minutes", 60))
    except ValueError:
        return JsonResponse({"error": "window_minutes must be an integer"}, status=400)

    since = timezone.now() - timedelta(minutes=window_minutes)

    failed_payments = (
        Payment.objects
        .filter(status="Failed", updated_at__gte=since)
        .select_related("order")
        .order_by("-updated_at")
    )

    results = []
    for payment in failed_payments:
        order = payment.order
        results.append({
            "payment_reference": payment.payment_reference,
            "order_reference": payment.order_reference,
            "amount": str(payment.amount),
            "phone": payment.phone,
            "name": payment.name or (order.name if order else ""),
            "email": payment.email or (order.email if order else ""),
            "failed_at": payment.updated_at.isoformat(),
            "telco_reference": payment.telco_reference,
        })

    return JsonResponse({
        "generated_at": timezone.now().isoformat(),
        "window_minutes": window_minutes,
        "failed_count": len(results),
        "failed_payments": results,
    })


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
        payment = Payment.objects.filter(**{lookup_field: lookup_value}).select_related("order").first()
        if not payment:
            logger.warning("Callback for unknown %s: %s", lookup_field, lookup_value)
            return
        payment.status = status
        payment.telco_reference = telco_reference
        payment.save(update_fields=["status", "telco_reference", "updated_at"])
        if payment.order_id and payment.order:
            order = payment.order
            order.status = status
            order.save(update_fields=["status", "updated_at"])

            # Customer always gets an email the moment Jenga confirms or
            # rejects the charge. The matching WhatsApp nudge
            # (order.get_payment_failed_whatsapp_link()) is tap-to-send,
            # surfaced to the admin in the order list/detail page.
            if status == "Completed":
                send_payment_confirmed_email(order, order.items.select_related("product").all())
            elif status == "Failed":
                send_payment_failed_email(order)

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

 
@manager_required
def manager_dashboard(request):
    orders = Order.objects.all()
    order_counts = {
        "pending": orders.filter(status="Pending").count(),
        "completed": orders.filter(status="Completed").count(),
        "failed": orders.filter(status="Failed").count(),
    }
    revenue = orders.filter(status="Completed").aggregate(total=Sum("total_amount"))["total"] or 0

    recent_orders = (
        orders.select_related("payment").prefetch_related("items__product")[:5]
    )

    products = Product.objects.all()
    product_counts = {
        "total": products.count(),
        "active": products.filter(is_active=True).count(),
    }

    # "Customers" here means anyone who's checked out, guest or not -
    # counting distinct emails rather than registered accounts, since
    # guest checkout is fully supported (see Order.user being optional).
    total_customers = orders.values("email").distinct().count()

    # Sales overview: completed revenue for each of the last 6 months,
    # oldest first, zero-filled for months with no completed orders.
    today = date.today()
    month_starts = []
    year, month = today.year, today.month
    for _ in range(6):
        month_starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    month_starts.reverse()

    monthly_totals = {}
    for row in (
        orders.filter(status="Completed", created_at__date__gte=month_starts[0])
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Sum("total_amount"))
    ):
        monthly_totals[(row["month"].year, row["month"].month)] = float(row["total"] or 0)

    sales_overview = [
        {"label": calendar.month_abbr[m.month], "value": monthly_totals.get((m.year, m.month), 0)}
        for m in month_starts
    ]

    # Most ordered products: total units sold across order items from
    # completed orders only - grouped by product then joined back to
    # the actual Product rows for name/image/thumbnail.
    top_rows = list(
        OrderItem.objects.filter(order__status="Completed")
        .values("product_id")
        .annotate(units_sold=Sum("quantity"), revenue=Sum(F("price") * F("quantity")))
        .order_by("-units_sold")[:5]
    )
    products_by_id = Product.objects.in_bulk([row["product_id"] for row in top_rows])
    most_ordered_products = [
        {
            "product": products_by_id[row["product_id"]],
            "units_sold": row["units_sold"],
            "revenue": row["revenue"],
        }
        for row in top_rows
        if row["product_id"] in products_by_id
    ]

    return render(request, "Bella/dashboard.html", {
        "active_nav": "dashboard",
        "total_orders": orders.count(),
        "order_counts": order_counts,
        "revenue": revenue,
        "recent_orders": recent_orders,
        "product_counts": product_counts,
        "total_customers": total_customers,
        "sales_overview": sales_overview,
        "most_ordered_products": most_ordered_products,
    })


# ---- reports --------------------------------------------------------------
# A single filterable/exportable report screen, separate from the always-
# on-"now" dashboard above. `period` + `offset` (how many periods back from
# "current") pick a date range; `manager_reports` then aggregates orders,
# revenue, products and customers for that range and its equivalent prior
# range (for the %-change badges), and hands it all to report.html.

def _add_months(year, month, delta):
    """(year, month) shifted by `delta` months, wrapping across years."""
    m = month - 1 + delta
    return year + m // 12, m % 12 + 1


def _report_period(period, offset, start_str, end_str):
    """
    Resolves the reports filter into a concrete inclusive date range: the
    range itself, the matching prior range to compare against, a human
    label, and the bucket size the trend chart should group by (hour/
    day/month - whichever keeps the chart readable for that span).
    """
    today = timezone.localdate()

    if period == "custom":
        try:
            range_start = datetime.strptime(start_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            range_start = today - timedelta(days=29)
        try:
            range_end = datetime.strptime(end_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            range_end = today
        if range_end < range_start:
            range_start, range_end = range_end, range_start
        range_end = min(range_end, today)

        span_days = (range_end - range_start).days + 1
        prev_end = range_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days - 1)
        label = f"{range_start.strftime('%b %d, %Y')} – {range_end.strftime('%b %d, %Y')}"
        granularity = "day" if span_days <= 62 else "month"
        can_go_next = False
        compare_label = "vs the same length of time before"

    elif period == "weekly":
        base_monday = today - timedelta(days=today.weekday())
        range_start = base_monday + timedelta(weeks=offset)
        range_end = range_start + timedelta(days=6)
        prev_start = range_start - timedelta(weeks=1)
        prev_end = range_end - timedelta(weeks=1)
        label = f"{range_start.strftime('%b %d')} – {range_end.strftime('%b %d, %Y')}"
        granularity = "day"
        can_go_next = offset < 0
        compare_label = "vs previous week"

    elif period == "monthly":
        year, month = _add_months(today.year, today.month, offset)
        range_start = date(year, month, 1)
        range_end = date(year, month, calendar.monthrange(year, month)[1])
        py, pm = _add_months(year, month, -1)
        prev_start = date(py, pm, 1)
        prev_end = date(py, pm, calendar.monthrange(py, pm)[1])
        label = range_start.strftime("%B %Y")
        granularity = "day"
        can_go_next = offset < 0
        compare_label = "vs previous month"

    elif period == "yearly":
        year = today.year + offset
        range_start = date(year, 1, 1)
        range_end = date(year, 12, 31)
        prev_start = date(year - 1, 1, 1)
        prev_end = date(year - 1, 12, 31)
        label = str(year)
        granularity = "month"
        can_go_next = offset < 0
        compare_label = "vs previous year"

    else:  # "daily" (also the fallback for an unrecognised period)
        period = "daily"
        range_start = today + timedelta(days=offset)
        range_end = range_start
        prev_start = range_start - timedelta(days=1)
        prev_end = prev_start
        label = range_start.strftime("%A, %b %d, %Y")
        granularity = "hour"
        can_go_next = offset < 0
        compare_label = "vs previous day"

    return {
        "period": period,
        "offset": offset,
        "range_start": range_start,
        "range_end": range_end,
        "prev_start": prev_start,
        "prev_end": prev_end,
        "label": label,
        "compare_label": compare_label,
        "granularity": granularity,
        "can_go_next": can_go_next,
    }


def _report_totals(orders_in_range):
    completed = orders_in_range.filter(status="Completed")
    revenue = completed.aggregate(total=Sum("total_amount"))["total"] or 0
    completed_count = completed.count()
    return {
        "orders": orders_in_range.count(),
        "completed_orders": completed_count,
        "revenue": revenue,
        "avg_order_value": (revenue / completed_count) if completed_count else 0,
    }


def _pct_change(current, previous):
    """
    {direction, value} pair the template uses to badge a KPI - "up"/
    "down"/"flat" with a percentage, or "new" when the prior period had
    nothing to compare against (a flat % there would be misleading).
    """
    current, previous = float(current or 0), float(previous or 0)
    if not previous:
        return {"direction": "new", "value": None} if current else {"direction": "flat", "value": 0}
    change = ((current - previous) / previous) * 100
    if change > 0.05:
        direction = "up"
    elif change < -0.05:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "value": round(abs(change), 1)}


def _report_trend(completed_in_range, bounds):
    """Zero-filled [{label, revenue, orders}, ...] across the period, bucketed per bounds['granularity']."""
    granularity = bounds["granularity"]

    if granularity == "hour":
        rows = (
            completed_in_range.annotate(bucket=TruncHour("created_at"))
            .values("bucket").annotate(revenue=Sum("total_amount"), orders=Count("id"))
        )
        totals = {}
        for row in rows:
            hour = timezone.localtime(row["bucket"]).hour
            totals[hour] = (float(row["revenue"] or 0), row["orders"])
        return [
            {"label": f"{h:02d}:00", "revenue": totals.get(h, (0, 0))[0], "orders": totals.get(h, (0, 0))[1]}
            for h in range(24)
        ]

    if granularity == "day":
        rows = (
            completed_in_range.annotate(bucket=TruncDate("created_at"))
            .values("bucket").annotate(revenue=Sum("total_amount"), orders=Count("id"))
        )
        totals = {row["bucket"]: (float(row["revenue"] or 0), row["orders"]) for row in rows}
        days, cursor = [], bounds["range_start"]
        while cursor <= bounds["range_end"]:
            revenue, orders = totals.get(cursor, (0, 0))
            days.append({"label": cursor.strftime("%b %d"), "revenue": revenue, "orders": orders})
            cursor += timedelta(days=1)
        return days

    # granularity == "month"
    rows = (
        completed_in_range.annotate(bucket=TruncMonth("created_at"))
        .values("bucket").annotate(revenue=Sum("total_amount"), orders=Count("id"))
    )
    totals = {(row["bucket"].year, row["bucket"].month): (float(row["revenue"] or 0), row["orders"]) for row in rows}
    months = []
    year, month = bounds["range_start"].year, bounds["range_start"].month
    end_year, end_month = bounds["range_end"].year, bounds["range_end"].month
    while (year, month) <= (end_year, end_month):
        revenue, orders = totals.get((year, month), (0, 0))
        months.append({"label": f"{calendar.month_abbr[month]} {year}", "revenue": revenue, "orders": orders})
        year, month = _add_months(year, month, 1)
    return months


def _export_orders_csv(orders, bounds):
    response = HttpResponse(content_type="text/csv")
    filename = f"orders-report-{bounds['range_start']}-to-{bounds['range_end']}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Order Reference", "Date", "Customer", "Email", "Phone",
        "Payment Status", "Delivery Status", "Total Amount (KES)",
    ])
    for order in orders:
        writer.writerow([
            order.order_reference,
            timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
            order.name,
            order.email,
            order.phone,
            order.status,
            order.get_delivery_status_display(),
            order.total_amount,
        ])
    return response


# Brand colours pulled from style.css so the PDF doesn't look like a
# generic reportlab default - keeps it visually in line with the manager UI.
_PDF_INK = colors.HexColor("#1C1B17")
_PDF_STONE = colors.HexColor("#8A8371")
_PDF_HAIRLINE = colors.HexColor("#D8D2C2")
_PDF_IVORY = colors.HexColor("#F6F3EC")
_PDF_BRAMBLE = colors.HexColor("#0F80C4")
_PDF_GOLD = colors.HexColor("#B8935A")
_PDF_GREEN = colors.HexColor("#3E6B2E")
_PDF_DANGER = colors.HexColor("#C0392B")


def _pdf_section_table(title, header_row, body_rows, styles, col_widths=None):
    """A titled table block: heading paragraph + a styled reportlab Table."""
    elements = [Paragraph(title, styles["ReportH2"]), Spacer(1, 6)]
    if not body_rows:
        elements.append(Paragraph("No data for this period.", styles["ReportEmpty"]))
        elements.append(Spacer(1, 18))
        return elements

    table = Table([header_row] + body_rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _PDF_IVORY),
        ("TEXTCOLOR", (0, 0), (-1, 0), _PDF_INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _PDF_HAIRLINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, _PDF_HAIRLINE),
        ("TEXTCOLOR", (0, 1), (-1, -1), _PDF_INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements += [table, Spacer(1, 18)]
    return elements


def _export_report_pdf(bounds, kpis, status_counts, top_products, category_revenue, delivery_stats):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=20 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )

    base_styles = getSampleStyleSheet()
    styles = {
        "ReportTitle": ParagraphStyle(
            "ReportTitle", parent=base_styles["Title"], textColor=_PDF_INK,
            fontName="Helvetica-Bold", fontSize=20, spaceAfter=2,
        ),
        "ReportSubtitle": ParagraphStyle(
            "ReportSubtitle", parent=base_styles["Normal"], textColor=_PDF_STONE, fontSize=10,
        ),
        "ReportH2": ParagraphStyle(
            "ReportH2", parent=base_styles["Heading2"], textColor=_PDF_INK,
            fontName="Helvetica-Bold", fontSize=12, spaceBefore=4, spaceAfter=2,
        ),
        "ReportEmpty": ParagraphStyle(
            "ReportEmpty", parent=base_styles["Normal"], textColor=_PDF_STONE,
            fontSize=9, spaceAfter=18,
        ),
    }

    elements = [
        Paragraph("Etsirbella Designs — Sales Report", styles["ReportTitle"]),
        Paragraph(f"{bounds['label']} &middot; {bounds['compare_label']}", styles["ReportSubtitle"]),
        Spacer(1, 16),
    ]

    # KPI summary
    kpi_header = ["Metric", "Value", "Change"]
    kpi_rows = []
    for kpi in kpis:
        value = f"KES {kpi['value']:,.0f}" if kpi["format"] == "kes" else f"{kpi['value']}"
        delta = kpi["delta"]
        if delta["direction"] == "new":
            change = "New"
        elif delta["direction"] == "flat":
            change = "No change"
        else:
            arrow = "up" if delta["direction"] == "up" else "down"
            change = f"{arrow} {delta['value']}%"
        kpi_rows.append([kpi["label"], value, change])
    elements += _pdf_section_table(
        "Key Metrics", kpi_header, kpi_rows, styles,
        col_widths=[70 * mm, 50 * mm, 50 * mm],
    )

    # Orders by payment status
    status_rows = [
        ["Pending", str(status_counts.get("pending", 0))],
        ["Completed", str(status_counts.get("completed", 0))],
        ["Failed", str(status_counts.get("failed", 0))],
    ]
    elements += _pdf_section_table(
        "Orders by Payment Status", ["Status", "Orders"], status_rows, styles,
        col_widths=[85 * mm, 85 * mm],
    )

    # Top products
    product_rows = [
        [row["product"].name, str(row["units_sold"]), f"KES {row['revenue']:,.0f}"]
        for row in top_products
    ]
    elements += _pdf_section_table(
        "Top Products", ["Product", "Units Sold", "Revenue"], product_rows, styles,
        col_widths=[90 * mm, 35 * mm, 45 * mm],
    )

    # Revenue by category
    category_rows = [
        [row["name"], f"KES {row['revenue']:,.0f}"] for row in category_revenue
    ]
    elements += _pdf_section_table(
        "Revenue by Category", ["Category", "Revenue"], category_rows, styles,
        col_widths=[85 * mm, 85 * mm],
    )

    # Delivery status (completed orders only)
    delivery_rows = [[row["label"], str(row["count"])] for row in delivery_stats]
    elements += _pdf_section_table(
        "Delivery Status (completed orders)", ["Status", "Orders"], delivery_rows, styles,
        col_widths=[85 * mm, 85 * mm],
    )

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"report-{bounds['range_start']}-to-{bounds['range_end']}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@manager_required
def manager_reports(request):
    period = request.GET.get("period", "monthly")
    if period not in ("daily", "weekly", "monthly", "yearly", "custom"):
        period = "monthly"

    try:
        offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = min(offset, 0)  # a stale/tampered link can't push the range into the future
    if period == "custom":
        offset = 0

    start_param = request.GET.get("start")
    end_param = request.GET.get("end")

    bounds = _report_period(period, offset, start_param, end_param)
    range_start, range_end = bounds["range_start"], bounds["range_end"]

    export_format = request.GET.get("export")

    orders_in_range = Order.objects.filter(created_at__date__gte=range_start, created_at__date__lte=range_end)

    if export_format == "csv":
        # Raw order-level export - doesn't need the aggregates below, so
        # it can return immediately without the extra queries.
        return _export_orders_csv(
            orders_in_range.select_related("payment").order_by("created_at"), bounds
        )

    prev_orders_in_range = Order.objects.filter(
        created_at__date__gte=bounds["prev_start"], created_at__date__lte=bounds["prev_end"]
    )

    totals = _report_totals(orders_in_range)
    prev_totals = _report_totals(prev_orders_in_range)

    # "New customers" = distinct emails whose very first order (ever)
    # landed inside this range - not just anyone who ordered in it.
    new_customers = (
        Order.objects.values("email").annotate(first_order=Min("created_at"))
        .filter(first_order__date__gte=range_start, first_order__date__lte=range_end)
        .count()
    )
    prev_new_customers = (
        Order.objects.values("email").annotate(first_order=Min("created_at"))
        .filter(first_order__date__gte=bounds["prev_start"], first_order__date__lte=bounds["prev_end"])
        .count()
    )

    kpis = [
        {
            "label": "Orders", "value": totals["orders"], "format": "int", "modifier": "blue",
            "delta": _pct_change(totals["orders"], prev_totals["orders"]),
        },
        {
            "label": "Revenue", "value": totals["revenue"], "format": "kes", "modifier": "pink",
            "delta": _pct_change(totals["revenue"], prev_totals["revenue"]),
        },
        {
            "label": "Avg. Order Value", "value": totals["avg_order_value"], "format": "kes", "modifier": "yellow",
            "delta": _pct_change(totals["avg_order_value"], prev_totals["avg_order_value"]),
        },
        {
            "label": "New Customers", "value": new_customers, "format": "int", "modifier": "orange",
            "delta": _pct_change(new_customers, prev_new_customers),
        },
    ]

    completed_in_range = orders_in_range.filter(status="Completed")
    trend = _report_trend(completed_in_range, bounds)

    status_counts = {"pending": 0, "completed": 0, "failed": 0}
    for row in orders_in_range.values("status").annotate(count=Count("id")):
        key = row["status"].lower()
        if key in status_counts:
            status_counts[key] = row["count"]

    delivery_counts = {key: 0 for key, _ in Order.DELIVERY_STATUS_CHOICES}
    for row in completed_in_range.values("delivery_status").annotate(count=Count("id")):
        if row["delivery_status"] in delivery_counts:
            delivery_counts[row["delivery_status"]] = row["count"]
    delivery_stats = [
        {"label": display, "count": delivery_counts[key]}
        for key, display in Order.DELIVERY_STATUS_CHOICES
    ]

    item_filters = dict(
        order__status="Completed",
        order__created_at__date__gte=range_start,
        order__created_at__date__lte=range_end,
    )

    top_rows = list(
        OrderItem.objects.filter(**item_filters)
        .values("product_id")
        .annotate(units_sold=Sum("quantity"), revenue=Sum(F("price") * F("quantity")))
        .order_by("-revenue")[:8]
    )
    products_by_id = Product.objects.in_bulk([row["product_id"] for row in top_rows])
    top_products = [
        {"product": products_by_id[row["product_id"]], "units_sold": row["units_sold"], "revenue": row["revenue"]}
        for row in top_rows
        if row["product_id"] in products_by_id
    ]

    category_rows = (
        OrderItem.objects.filter(**item_filters)
        .values(name=F("product__category__name"))
        .annotate(revenue=Sum(F("price") * F("quantity")), units=Sum("quantity"))
        .order_by("-revenue")[:8]
    )
    category_revenue = [
        {"name": row["name"] or "Uncategorised", "revenue": row["revenue"], "units": row["units"]}
        for row in category_rows
    ]

    if export_format == "pdf":
        # Summary export - built from the same aggregates as the on-screen
        # report, so it needs everything computed above first.
        return _export_report_pdf(bounds, kpis, status_counts, top_products, category_revenue, delivery_stats)

    csv_export_params = request.GET.copy()
    csv_export_params["period"] = period
    csv_export_params["offset"] = offset
    csv_export_params["export"] = "csv"

    pdf_export_params = request.GET.copy()
    pdf_export_params["period"] = period
    pdf_export_params["offset"] = offset
    pdf_export_params["export"] = "pdf"

    return render(request, "Bella/report.html", {
        "active_nav": "reports",
        "period": period,
        "offset": offset,
        "bounds": bounds,
        "start_param": start_param or bounds["range_start"].isoformat(),
        "end_param": end_param or bounds["range_end"].isoformat(),
        "today": timezone.localdate().isoformat(),
        "kpis": kpis,
        "trend": trend,
        "status_counts": status_counts,
        "delivery_stats": delivery_stats,
        "top_products": top_products,
        "category_revenue": category_revenue,
        "csv_export_querystring": csv_export_params.urlencode(),
        "pdf_export_querystring": pdf_export_params.urlencode(),
    })


# ---- orders -------------------------------------------------------------
 
@manager_required
def manager_order_list(request):
    orders = Order.objects.select_related("payment").prefetch_related("items__product")
 
    status = request.GET.get("status")
    if status in dict(Order.STATUS_CHOICES):
        orders = orders.filter(status=status)
 
    query = (request.GET.get("q") or "").strip()
    if query:
        orders = orders.filter(
            Q(order_reference__icontains=query)
            | Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )

    page_obj, querystring, page_range = _paginate(request, orders)
 
    return render(request, "Bella/order_list.html", {
        "active_nav": "orders",
        "orders": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "page_range": page_range,
        "active_status": status or "",
        "query": query,
    })
 
 
@manager_required
def manager_order_detail(request, order_reference):
    order = get_object_or_404(
        Order.objects.select_related("payment").prefetch_related("items__product"),
        order_reference=order_reference,
    )
    return render(request, "Bella/order_detail.html", {
        "active_nav": "orders",
        "order": order,
    })
 
 
@manager_required
@require_POST
def manager_order_update_status(request, order_reference):
    order = get_object_or_404(Order, order_reference=order_reference)
    new_status = request.POST.get("status")
 
    if new_status not in dict(Order.STATUS_CHOICES):
        messages.error(request, "Not a valid order status.")
        return redirect("manager_order_detail", order_reference=order.order_reference)
 
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
 
    # Keep the linked payment record in step with a manual status change,
    # the same way payment_callback() keeps them in sync automatically
    # when Jenga confirms a charge.
    try:
        payment = order.payment
    except Payment.DoesNotExist:
        payment = None
    if payment:
        payment.status = new_status
        payment.save(update_fields=["status", "updated_at"])

    # A manual edit right here should notify the customer exactly the
    # same way an automatic Jenga callback would - see payment_callback().
    if new_status == "Completed":
        send_payment_confirmed_email(order, order.items.select_related("product").all())
    elif new_status == "Failed":
        send_payment_failed_email(order)
 
    messages.success(request, f"Order {order.order_reference} marked as {new_status}.")
    return redirect("manager_order_detail", order_reference=order.order_reference)


@manager_required
@require_POST
def manager_order_update_delivery_status(request, order_reference):
    """
    Moves an order along Processing -> Shipped -> Out for Delivery ->
    Delivered. Only allowed once payment is Completed - shipping
    something that hasn't been paid for isn't a state this button
    should ever put you in. Wire this up in urls.py, e.g.:

        path("manager/orders/<str:order_reference>/delivery/",
             views.manager_order_update_delivery_status,
             name="manager_order_update_delivery_status"),

    and add a small status-select form to order_detail.html posting to
    it, the same shape as the existing payment-status form there.
    """
    order = get_object_or_404(Order, order_reference=order_reference)
    new_status = request.POST.get("delivery_status")

    if new_status not in dict(Order.DELIVERY_STATUS_CHOICES):
        messages.error(request, "Not a valid delivery status.")
        return redirect("manager_order_detail", order_reference=order.order_reference)

    if order.status != "Completed":
        messages.error(request, "Payment needs to be confirmed before updating delivery status.")
        return redirect("manager_order_detail", order_reference=order.order_reference)

    order.delivery_status = new_status
    update_fields = ["delivery_status", "updated_at"]

    if new_status == "shipped" and not order.shipped_at:
        order.shipped_at = timezone.now()
        update_fields.append("shipped_at")
    elif new_status == "delivered" and not order.delivered_at:
        order.delivered_at = timezone.now()
        update_fields.append("delivered_at")

    order.save(update_fields=update_fields)

    # Email fires automatically; the WhatsApp side is one tap away via
    # order.get_shipped_whatsapp_link() / get_delivered_whatsapp_link(),
    # surfaced as a button on the order detail page.
    if new_status == "shipped":
        send_order_shipped_email(order)
    elif new_status == "delivered":
        send_order_delivered_email(order)

    messages.success(
        request,
        f"Order {order.order_reference} marked as {order.get_delivery_status_display()} "
        f"and the customer's been emailed. Tap the WhatsApp button on this page too.",
    )
    return redirect("manager_order_detail", order_reference=order.order_reference)
 
 
# ---- products -------------------------------------------------------------
 
@manager_required
def manager_product_list(request):
    products = Product.objects.select_related("category").prefetch_related("variants", "colours")
 
    query = (request.GET.get("q") or "").strip()
    if query:
        products = products.filter(name__icontains=query)
 
    category_slug = request.GET.get("category")
    if category_slug:
        products = products.filter(category__slug=category_slug)

    page_obj, querystring, page_range = _paginate(request, products)
 
    return render(request, "Bella/product_list.html", {
        "active_nav": "products",
        "products": page_obj,
        "page_obj": page_obj,
        "querystring": querystring,
        "page_range": page_range,
        "categories": Category.objects.all(),
        "query": query,
        "active_category": category_slug or "",
    })
 
 
@manager_required
def manager_product_create(request):
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
 
        if form.is_valid():
            product = form.save()
            formset = ProductVariantFormSet(request.POST, instance=product, prefix=VARIANT_FORMSET_PREFIX)
            colour_formset = ProductColourFormSet(request.POST, instance=product, prefix=COLOUR_FORMSET_PREFIX)
            if formset.is_valid() and colour_formset.is_valid():
                formset.save()
                colour_formset.save()
                messages.success(request, f"{product.name} was added.")
                return redirect("manager_product_list")
            # The product row is already saved at this point - rather than
            # leaving it orphaned, send the manager straight to editing it
            # so they can fix the variant/colour rows without losing the product.
            messages.warning(request, f"{product.name} was saved, but the variants/colours need fixing.")
            return redirect("manager_product_edit", pk=product.pk)
 
        formset = ProductVariantFormSet(request.POST, instance=Product(), prefix=VARIANT_FORMSET_PREFIX)
        colour_formset = ProductColourFormSet(request.POST, instance=Product(), prefix=COLOUR_FORMSET_PREFIX)
    else:
        form = ProductForm()
        formset = ProductVariantFormSet(instance=Product(), prefix=VARIANT_FORMSET_PREFIX)
        colour_formset = ProductColourFormSet(instance=Product(), prefix=COLOUR_FORMSET_PREFIX)
 
    return render(request, "Bella/product_form.html", {
        "active_nav": "products",
        "form": form,
        "formset": formset,
        "colour_formset": colour_formset,
        "is_new": True,
    })
 
 
@manager_required
def manager_product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
 
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        formset = ProductVariantFormSet(request.POST, instance=product, prefix=VARIANT_FORMSET_PREFIX)
        colour_formset = ProductColourFormSet(request.POST, instance=product, prefix=COLOUR_FORMSET_PREFIX)
 
        if form.is_valid() and formset.is_valid() and colour_formset.is_valid():
            form.save()
            formset.save()
            colour_formset.save()
            messages.success(request, f"{product.name} was updated.")
            return redirect("manager_product_list")
    else:
        form = ProductForm(instance=product)
        formset = ProductVariantFormSet(instance=product, prefix=VARIANT_FORMSET_PREFIX)
        colour_formset = ProductColourFormSet(instance=product, prefix=COLOUR_FORMSET_PREFIX)
 
    return render(request, "Bella/product_form.html", {
        "active_nav": "products",
        "form": form,
        "formset": formset,
        "colour_formset": colour_formset,
        "product": product,
        "is_new": False,
    })
 
 
@manager_required
@require_POST
def manager_product_toggle_active(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active", "updated_at"])
    state = "activated" if product.is_active else "deactivated"
    messages.success(request, f"{product.name} {state}.")
    return redirect("manager_product_list")
 
 
@manager_required
@require_POST
def manager_product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    name = product.name
    try:
        product.delete()
        messages.success(request, f"{name} was deleted.")
    except ProtectedError:
        # OrderItem.product is on_delete=PROTECT, so a product that's part
        # of any past order can't be deleted without corrupting order
        # history. Deactivating it instead keeps history intact while
        # pulling it off the storefront.
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
        messages.warning(
            request,
            f"{name} is part of past orders and can't be deleted, so it was deactivated instead.",
        )
    return redirect("manager_product_list")
 
 
# ---- categories -----------------------------------------------------------
 
@manager_required
def manager_category_list(request):
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added.")
            return redirect("manager_category_list")
    else:
        form = CategoryForm()
 
    return render(request, "Bella/category_list.html", {
        "active_nav": "categories",
        "categories": Category.objects.all(),
        "form": form,
    })


# ---- manager settings -------------------------------------------------

@manager_required
def manager_settings(request):
    """
    One page, five independent forms (photo / account details /
    password / theme / notifications), each posted separately -
    identified by a `section` hidden field so submitting one doesn't
    require the others to also validate.
    """
    profile = _get_manager_profile(request.user)

    account_form = ManagerAccountForm(instance=request.user)
    password_form = PasswordChangeForm(user=request.user)
    avatar_form = ManagerAvatarForm(instance=profile)
    theme_form = ManagerThemeForm(instance=profile)
    notifications_form = ManagerNotificationsForm(instance=profile)

    if request.method == "POST":
        section = request.POST.get("section")

        if section == "account":
            account_form = ManagerAccountForm(request.POST, instance=request.user)
            if account_form.is_valid():
                account_form.save()
                messages.success(request, "Your account details were updated.")
                return redirect("manager_settings")

        elif section == "password":
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                # Changing the password normally invalidates every
                # existing session (including this one) - this keeps
                # the manager signed in instead of bouncing them to
                # the login page mid-edit.
                update_session_auth_hash(request, user)
                messages.success(request, "Your password was changed.")
                return redirect("manager_settings")

        elif section == "avatar":
            avatar_form = ManagerAvatarForm(request.POST, request.FILES, instance=profile)
            if avatar_form.is_valid():
                avatar_form.save()
                messages.success(request, "Profile photo updated.")
                return redirect("manager_settings")

        elif section == "theme":
            theme_form = ManagerThemeForm(request.POST, instance=profile)
            if theme_form.is_valid():
                theme_form.save()
                messages.success(request, "Appearance updated.")
                return redirect("manager_settings")

        elif section == "notifications":
            notifications_form = ManagerNotificationsForm(request.POST, instance=profile)
            if notifications_form.is_valid():
                notifications_form.save()
                messages.success(request, "Notification preferences updated.")
                return redirect("manager_settings")

    return render(request, "Bella/manager_settings.html", {
        "active_nav": "settings",
        "profile": profile,
        "account_form": account_form,
        "password_form": password_form,
        "avatar_form": avatar_form,
        "theme_form": theme_form,
        "notifications_form": notifications_form,
    })


@manager_required
@require_POST
def manager_update_avatar(request):
    """
    AJAX endpoint for the quick photo picker in the sidebar (see the
    managerAvatarInput handler in manager_base.html) - saves the file
    the instant it's chosen, from any manager page, and hands back the
    persisted URL so the preview can be swapped from the local blob:
    URL to the real one.
    """
    profile = _get_manager_profile(request.user)
    form = ManagerAvatarForm(request.POST, request.FILES, instance=profile)
    if form.is_valid():
        form.save()
        return JsonResponse({"success": True, "url": profile.photo.url})
    return JsonResponse({"success": False, "errors": form.errors}, status=400)


@manager_required
@require_POST
def manager_update_theme(request):
    """
    AJAX endpoint for the quick sun/moon toggle in the sidebar (see the
    managerThemeToggle handler in manager_base.html) - lets a manager
    flip Light -> Dark -> Match system from any manager page, without
    a full page reload, and persists it so it follows them to their
    next device/session too.
    """
    profile = _get_manager_profile(request.user)
    theme = request.POST.get("theme")
    if theme not in dict(ManagerProfile.THEME_CHOICES):
        return JsonResponse({"success": False, "error": "Unknown theme."}, status=400)

    profile.theme_preference = theme
    profile.save(update_fields=["theme_preference", "updated_at"])
    return JsonResponse({"success": True, "theme": theme})