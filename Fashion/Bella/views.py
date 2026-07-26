import json
import logging

import requests
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .authentication import JengaAuthentication
from .cart import Cart
from .wishlist import Wishlist
from .forms import CheckoutForm, ContactForm
from .models import Product, Category, Order, OrderItem, Payment, ContactMessage
from .services import JengaClient, generate_reference

logger = logging.getLogger(__name__)


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

    return render(request, "Bella/home.html", {
        "featured_products": featured_products,
        "hero_main": hero_main,
        "hero_thumb_a": hero_thumb_a,
        "hero_thumb_b": hero_thumb_b,
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

    return render(request, "Bella/products_list.html", {
        "products": products,
        "categories": categories,
        "active_category": category_slug,
        "query": query or "",
    })


def product_detail(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    return render(request, "Bella/product_detail.html", {"product": product})


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            ContactMessage.objects.create(**form.cleaned_data)
            messages.success(request, "Thanks for reaching out - we'll reply soon.")
            return redirect("contact")
    else:
        form = ContactForm()
    return render(request, "Bella/contact.html", {"form": form})


# ---- cart ---------------------------------------------------------------

def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _cart_payload(cart):
    """Serialize the cart for the header drawer / any JS consumer."""
    items = []
    for item in cart:
        product = item["product"]
        price = item["price"]
        quantity = item["quantity"]
        items.append({
            "product_id": product.id,
            "name": product.name,
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
    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    cart.add(product=product, quantity=quantity)
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.success(request, f"{product.name} added to your bag.")
    return redirect(request.POST.get("next") or "cart_detail")


@require_POST
def cart_remove(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    cart = Cart(request)
    cart.remove(product)
    if _is_ajax(request):
        return JsonResponse(_cart_payload(cart))
    messages.info(request, f"{product.name} removed from your bag.")
    return redirect("cart_detail")


@require_POST
def cart_update(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    cart = Cart(request)
    quantity = int(request.POST.get("quantity", 1))
    if quantity <= 0:
        cart.remove(product)
    else:
        cart.add(product=product, quantity=quantity, update_quantity=True)
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
            "in_stock": product.in_stock(),
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
                return render(request, "Bella/checkout.html", {"form": form, "cart": cart})

            if not result.get("status"):
                # Jenga responded but rejected the request (e.g. bad params).
                payment.status = "Failed"
                payment.save(update_fields=["status", "updated_at"])
                order.status = "Failed"
                order.save(update_fields=["status", "updated_at"])
                form.add_error(
                    None, result.get("message", "Failed to initiate payment.")
                )
                return render(request, "Bella/checkout.html", {"form": form, "cart": cart})

            payment.invoice_number = result.get("data", {}).get("invoiceNumber", "")
            payment.save(update_fields=["invoice_number", "updated_at"])

            # Push was accepted - the customer now has an M-Pesa prompt on
            # their phone. Final status arrives via payment_callback below.
            request.session["last_order_reference"] = order_reference
            cart.clear()

            return redirect("success")
    else:
        form = CheckoutForm()

    return render(request, "Bella/checkout.html", {"form": form, "cart": cart})


def success(request):
    order_reference = request.session.get("last_order_reference")
    order = Order.objects.filter(order_reference=order_reference).first() if order_reference else None
    return render(request, "Bella/success.html", {"order": order})


def test_authentication(request):
    auth = JengaAuthentication()
    token = auth.get_access_token()
    return JsonResponse(token)


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

    logger.info("Jenga callback received: %s", json.dumps(data))

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