from django.db import models
from django.db.models import Avg
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Product(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    # ---- variants -------------------------------------------------------
    # A product can either be "simple" (uses price/stock above directly -
    # most existing products) or have one or more ProductVariant rows
    # (e.g. scrunchie sizes, wig type/size, bonnet style). When variants
    # exist they take over price/stock display and cart behaviour.

    def has_variants(self):
        return self.variants.exists()

    def ordered_variants(self):
        return self.variants.all()

    def default_variant(self):
        variants = list(self.variants.all())
        if not variants:
            return None
        for variant in variants:
            if variant.is_default:
                return variant
        return variants[0]

    def price_range(self):
        """(low, high) tuple - across variants if any, else the flat price twice."""
        variants = list(self.variants.all())
        if not variants:
            return self.price, self.price
        prices = [v.price for v in variants]
        return min(prices), max(prices)

    def total_stock(self):
        if self.has_variants():
            return sum(v.stock for v in self.variants.all())
        return self.stock

    def in_stock(self):
        if self.has_variants():
            return any(v.stock > 0 for v in self.variants.all())
        return self.stock > 0

    # ---- ratings ----------------------------------------------------
    # Star ratings customers leave on this specific product. Only
    # is_approved=True reviews ever count toward the average or show up
    # on the storefront - new submissions default to unapproved.

    def approved_reviews(self):
        return self.reviews.filter(is_approved=True)

    def average_rating(self):
        return self.approved_reviews().aggregate(avg=Avg("rating"))["avg"] or 0

    def average_rating_percent(self):
        """0-100, for filling in a CSS star-bar width."""
        return round((self.average_rating() / 5) * 100, 1)

    def review_count(self):
        return self.approved_reviews().count()


class ProductVariant(models.Model):
    """
    A purchasable option of a Product - e.g. a size (Extra Large, Large,
    Medium, Small, Mini), a material (Human Hair, Synthetic), a style
    (Satin, Ankara/Satin-lined), or an option like "With Notebook" /
    "Cover Only". Each variant has its own price and stock, so a single
    product page can offer several options at different prices.
    """

    product = models.ForeignKey(Product, related_name="variants", on_delete=models.CASCADE)

    name = models.CharField(
        max_length=100,
        help_text="e.g. Extra Large, Human Hair, Waterproof, Cover Only",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)

    is_default = models.BooleanField(
        default=False, help_text="Pre-selected option when the product page loads."
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} — {self.name}"

    def in_stock(self):
        return self.stock > 0


class Order(models.Model):
    """
    One purchase, possibly containing several products from the cart.
    Created the moment checkout is submitted, before Jenga is even
    contacted, so we always have a record even if the STK push fails.
    """

    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Completed", "Completed"),
        ("Failed", "Failed"),
    ]

    order_reference = models.CharField(max_length=30, unique=True)

    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_reference

    def get_whatsapp_link(self, to_number=None):
        """
        Customer -> Business. The "I've placed this order" message,
        shown to the customer on their confirmation page. Built fresh
        from the order's own data - doesn't depend on session/request
        state, so it's safe to bookmark or revisit any time.
        """
        from .whatsapp import build_order_whatsapp_link  # local import avoids any import-order issues

        return build_order_whatsapp_link(
            self, self.items.select_related("product").all(), to_number=to_number
        )

    def get_admin_reply_whatsapp_link(self):
        """
        Business -> Customer. The "we've received your order" reply,
        sent to the customer's own number (self.phone). Meant for the
        admin to tap - works the same whether `status` got to Completed
        via the Jenga callback or a manual edit in admin.
        """
        from .whatsapp import build_order_admin_reply_link  # local import avoids any import-order issues

        return build_order_admin_reply_link(self, self.items.select_related("product").all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    # Snapshot of the price at purchase time, so later price changes on
    # the product don't rewrite history for past orders.
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def subtotal(self):
        return self.price * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"


class Payment(models.Model):
    # Jenga's Wallet-Based Checkout API needs both - order_reference
    # identifies the "order", payment_reference the specific charge.
    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="payment", null=True, blank=True
    )

    order_reference = models.CharField(max_length=30, unique=True)
    payment_reference = models.CharField(max_length=30, unique=True)

    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20)

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=30, default="Pending")

    # Filled in from the Jenga callback once M-Pesa responds.
    telco_reference = models.CharField(max_length=50, blank=True, null=True)
    invoice_number = models.CharField(max_length=50, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.payment_reference


class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=150, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.subject or 'No subject'}"


class ProductReview(models.Model):
    """
    A star rating + optional comment left by a site visitor on a
    specific product. Anyone can submit one (no account needed) - it
    just doesn't count toward the average or show on the storefront
    until an admin approves it.
    """

    product = models.ForeignKey(Product, related_name="reviews", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)

    is_approved = models.BooleanField(
        default=False, help_text="Only approved reviews count toward the average or show on the storefront."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.name} - {self.rating}\u2605 by {self.name}"

    @property
    def rating_percent(self):
        return (self.rating / 5) * 100


class StoreReview(models.Model):
    """
    An overall rating + optional comment about the store/service as a
    whole - not tied to any one product (e.g. shown in the homepage
    testimonials section). Same public-submission, admin-approval flow
    as ProductReview.
    """

    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)

    is_approved = models.BooleanField(
        default=False, help_text="Only approved reviews count toward the average or show on the storefront."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}\u2605 by {self.name}"

    @property
    def rating_percent(self):
        return (self.rating / 5) * 100

    @classmethod
    def approved(cls):
        return cls.objects.filter(is_approved=True)

    @classmethod
    def average_rating(cls):
        return cls.approved().aggregate(avg=Avg("rating"))["avg"] or 0

    @classmethod
    def average_rating_percent(cls):
        return round((cls.average_rating() / 5) * 100, 1)

    @classmethod
    def review_count(cls):
        return cls.approved().count()