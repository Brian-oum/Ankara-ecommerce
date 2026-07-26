from decimal import Decimal

from .models import Product

CART_SESSION_KEY = "cart"


class Cart:
    """
    A simple session-backed cart. No login required to shop - the cart
    lives in the visitor's session and is turned into an Order only at
    checkout time.
    """

    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(CART_SESSION_KEY)
        if cart is None:
            cart = self.session[CART_SESSION_KEY] = {}
        self.cart = cart

    def add(self, product, quantity=1, update_quantity=False):
        product_id = str(product.id)

        if product_id not in self.cart:
            self.cart[product_id] = {"quantity": 0, "price": str(product.price)}

        if update_quantity:
            self.cart[product_id]["quantity"] = quantity
        else:
            self.cart[product_id]["quantity"] += quantity

        self.save()

    def remove(self, product):
        product_id = str(product.id)
        if product_id in self.cart:
            del self.cart[product_id]
            self.save()

    def save(self):
        self.session.modified = True

    def clear(self):
        self.session[CART_SESSION_KEY] = {}
        self.save()

    def __iter__(self):
        product_ids = self.cart.keys()
        products = Product.objects.filter(id__in=product_ids)
        products_by_id = {str(p.id): p for p in products}

        for product_id, item in self.cart.items():
            product = products_by_id.get(product_id)
            if not product:
                continue
            price = Decimal(item["price"])
            quantity = item["quantity"]
            yield {
                "product": product,
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