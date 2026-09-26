from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Product


class ProductSitemap(Sitemap):
    """
    Auto-generates <url> entries for every active product, e.g.
    /products/<slug>/  — pulled directly from the Product table so
    new/removed products show up in the sitemap with zero manual edits.
    """
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Product.objects.filter(is_active=True)

    def location(self, obj):
        return reverse("product_detail", args=[obj.slug])

    def lastmod(self, obj):
        # Adjust the field name if your Product model uses something
        # else (e.g. `created_at`, `date_updated`). Returning None is
        # fine too — Django just omits <lastmod>.
        return getattr(obj, "updated_at", None)


class StaticViewSitemap(Sitemap):
    """
    Everything else worth indexing: pages with no dynamic data and no
    login/cart/checkout state. Add a name here any time you add a new
    public page to urls.py.
    """
    priority = 0.5
    changefreq = "monthly"

    def items(self):
        return [
            "home",
            "about",
            "product_list",
            "contact",
            "store_reviews",
            "privacy_policy",
            "terms_of_service",
        ]

    def location(self, item):
        return reverse(item)