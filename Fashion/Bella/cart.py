from decimal import Decimal

from .models import Product, ProductVariant

CART_SESSION_KEY = "cart"


class Cart:
    """
    A simple session-backed cart. No login required to shop - the cart
    lives in the visitor's session and is turned into an Order only at
    checkout time.

    Items are keyed by "<product_id>" for simple products, or
    "<product_id>:<variant_id>" once a variant is chosen - so the same
    product can sit in the bag more than once with different variants
    (e.g. two wig sizes) as separate lines, each with its own snapshotted
    price.
    """

    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(CART_SESSION_KEY)
        if cart is None:
            cart = self.session[CART_SESSION_KEY] = {}
        self.cart = cart

    @staticmethod
    def _key(product, variant=None):
        return f"{product.id}:{variant.id}" if variant else str(product.id)

    def add(self, product, quantity=1, update_quantity=False, variant=None):
        key = self._key(product, variant)
        price = variant.price if variant else product.price

        if key not in self.cart:
            self.cart[key] = {
                "product_id": product.id,
                "variant_id": variant.id if variant else None,
                "quantity": 0,
                "price": str(price),
            }

        if update_quantity:
            self.cart[key]["quantity"] = quantity
        else:
            self.cart[key]["quantity"] += quantity

        self.save()

    def remove(self, product, variant=None):
        key = self._key(product, variant)
        if key in self.cart:
            del self.cart[key]
            self.save()

    def save(self):
        self.session.modified = True

    def clear(self):
        self.session[CART_SESSION_KEY] = {}
        self.save()

    def _resolve_ids(self, key, item):
        """
        Supports both the current {product_id, variant_id, ...} item
        shape and older session data saved before variants existed
        (plain "<product_id>": {"quantity", "price"} with no
        product_id/variant_id fields), so carts started before this
        change don't crash - they just get read the old way.
        """
        if "product_id" in item:
            return item["product_id"], item.get("variant_id")
        raw_product_id, _, raw_variant_id = key.partition(":")
        return int(raw_product_id), (int(raw_variant_id) if raw_variant_id else None)

    def __iter__(self):
        resolved = [(*self._resolve_ids(key, item), item) for key, item in self.cart.items()]

        product_ids = {product_id for product_id, _, _ in resolved}
        variant_ids = {variant_id for _, variant_id, _ in resolved if variant_id}

        products_by_id = {p.id: p for p in Product.objects.filter(id__in=product_ids)}
        variants_by_id = {v.id: v for v in ProductVariant.objects.filter(id__in=variant_ids)}

        for product_id, variant_id, item in resolved:
            product = products_by_id.get(product_id)
            if not product:
                continue
            variant = variants_by_id.get(variant_id) if variant_id else None

            price = Decimal(item["price"])
            quantity = item["quantity"]
            yield {
                "product": product,
                "variant": variant,
                "quantity": quantity,
                "price": price,
                "subtotal": price * quantity,
            }

    def __len__(self):
        return sum(item["quantity"] for item in self.cart.values())

    def get_total_price(self):
        return sum(
            (Decimal(item["price"]) * item["quantity"] for item in self.cart.values()),
            Decimal("0.00"),
        )