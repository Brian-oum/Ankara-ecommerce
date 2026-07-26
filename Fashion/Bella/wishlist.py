from .models import Product

WISHLIST_SESSION_KEY = "wishlist"


class Wishlist:
    """
    A lightweight, session-based wishlist. Unlike the cart it only needs to
    remember *which* products the visitor is interested in - no quantities
    or price snapshots - so this just tracks a list of product ids.
    """

    def __init__(self, request):
        self.session = request.session
        product_ids = self.session.get(WISHLIST_SESSION_KEY)
        if product_ids is None:
            product_ids = []
            self.session[WISHLIST_SESSION_KEY] = product_ids
        self.product_ids = product_ids

    def save(self):
        self.session.modified = True

    def add(self, product):
        if product.id not in self.product_ids:
            self.product_ids.append(product.id)
            self.save()

    def remove(self, product):
        if product.id in self.product_ids:
            self.product_ids.remove(product.id)
            self.save()

    def toggle(self, product):
        """Add the product if it isn't already on the wishlist, otherwise
        remove it. Returns True if it ended up added, False if removed."""
        if product.id in self.product_ids:
            self.remove(product)
            return False
        self.add(product)
        return True

    def clear(self):
        self.product_ids = []
        self.session[WISHLIST_SESSION_KEY] = []
        self.save()

    def __contains__(self, product):
        return product.id in self.product_ids

    def __iter__(self):
        products = Product.objects.filter(id__in=self.product_ids)
        products_by_id = {p.id: p for p in products}
        for product_id in self.product_ids:
            product = products_by_id.get(product_id)
            if product:
                yield product

    def __len__(self):
        return len(self.product_ids)