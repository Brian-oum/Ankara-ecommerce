from django.conf import settings
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
    # A product can either be "simple" (uses price above directly -
    # most existing products) or have one or more ProductVariant rows
    # (e.g. scrunchie sizes, wig type/size, bonnet style). When variants
    # exist they take over price display and cart behaviour.

    def has_variants(self):
        return self.variants.exists()

    def ordered_variants(self):
        return self.variants.all()

    def ordered_colours(self):
        return self.colours.all()

    def has_colours(self):
        return self.colours.exists()

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
    "Cover Only". Each variant has its own price , so a single
    product page can offer several options at different prices.
    """

    product = models.ForeignKey(Product, related_name="variants", on_delete=models.CASCADE)

    name = models.CharField(
        max_length=100,
        help_text="e.g. Extra Large, Human Hair, Waterproof, Cover Only",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)

    is_default = models.BooleanField(
        default=False, help_text="Pre-selected option when the product page loads."
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} — {self.name}"


class ProductColour(models.Model):
    """
    An available colour option for a Product - e.g. Black, Burgundy,
    Honey Blonde. Purely descriptive (no price impact); shown on the
    product page as swatches using `hex_code`.
    """

    product = models.ForeignKey(Product, related_name="colours", on_delete=models.CASCADE)

    name = models.CharField(max_length=50, help_text="e.g. Black, Burgundy, Honey Blonde")
    hex_code = models.CharField(
        max_length=7,
        help_text="Swatch colour, e.g. #1A1A1A",
    )

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} — {self.name}"



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

    # Fulfillment, kept deliberately separate from `status` above. `status`
    # tracks the PAYMENT and is driven by Jenga (see payment_callback in
    # views.py) - conflating delivery into those same three values would
    # mean a manual "Shipped" edit could get clobbered by the next Jenga
    # callback. Only meaningful once status == "Completed"; a manager
    # can't move it until payment is actually confirmed (enforced in
    # manager_order_update_delivery_status).
    DELIVERY_STATUS_CHOICES = [
        ("processing", "Processing"),
        ("shipped", "Shipped"),
        ("out_for_delivery", "Out for Delivery"),
        ("delivered", "Delivered"),
    ]
    delivery_notes = models.CharField(max_length=255, blank=True)
    order_reference = models.CharField(max_length=30, unique=True)

    # Optional - only set when the order was placed while signed in, so
    # it shows up in that customer's order history. Checkout still works
    # without an account (name/email/phone below cover that case either
    # way), and SET_NULL keeps the order around even if the account is
    # later deleted.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20)

    # ---- delivery location & price breakdown -------------------------
    # `total_amount` stays what it's always been - the grand total
    # actually charged (goods + shipping + VAT) - so nothing that
    # already reads total_amount (Payment.amount, WhatsApp/email
    # messages, admin, templates) needs to change. The three fields
    # below just make that total auditable after the fact.
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Area name is the source of truth for shipping_fee (looked up from
    # shipping.AREA_LOOKUP server-side at checkout - never trust a
    # client-submitted fee). Lat/lng/address are just the pin the
    # customer dropped on the OSM map, kept for the rider's benefit -
    # they don't drive pricing.
    delivery_area = models.CharField(max_length=100, blank=True)
    delivery_zone = models.CharField(max_length=100, blank=True)
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")

    delivery_status = models.CharField(
        max_length=20, choices=DELIVERY_STATUS_CHOICES, default="processing"
    )
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_reference

    # ---- delivery tracking ------------------------------------------
    def delivery_timeline(self):
        """
        Ordered [{key, label, is_done, timestamp}, ...] for a simple
        step tracker on the customer's account page. Only call this once
        status == "Completed" - before payment is confirmed there's
        nothing to track yet.
        """
        stage_order = [key for key, _ in self.DELIVERY_STATUS_CHOICES]
        current_index = stage_order.index(self.delivery_status) if self.delivery_status in stage_order else 0
        timestamps = {"shipped": self.shipped_at, "delivered": self.delivered_at}

        return [
            {
                "key": key,
                "label": label,
                "is_done": index <= current_index,
                "timestamp": timestamps.get(key),
            }
            for index, (key, label) in enumerate(self.DELIVERY_STATUS_CHOICES)
        ]

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

    def get_payment_failed_whatsapp_link(self):
        """Business -> Customer. Tap-to-send nudge when payment didn't go through."""
        from .whatsapp import build_payment_failed_link

        return build_payment_failed_link(self)

    def get_shipped_whatsapp_link(self):
        """Business -> Customer. Tap-to-send heads-up that the order is on its way."""
        from .whatsapp import build_order_shipped_link

        return build_order_shipped_link(self)

    def get_delivered_whatsapp_link(self):
        """Business -> Customer. Tap-to-send confirmation once it's arrived."""
        from .whatsapp import build_order_delivered_link

        return build_order_delivered_link(self)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    # Snapshot of the price at purchase time, so later price changes on
    # the product don't rewrite history for past orders.
    price = models.DecimalField(max_digits=10, decimal_places=2)

    # Snapshot of the colour the customer picked (if the product has
    # any) - stored as plain values rather than a FK to ProductColour so
    # the order still shows what was bought even if that colour is later
    # renamed or removed from the product.
    colour_name = models.CharField(max_length=50, blank=True)
    colour_hex = models.CharField(max_length=7, blank=True)

    # Free-text customisation the customer typed in the "Need something
    # specific?" modal on the product page - e.g. a colour that isn't in
    # the swatch list, a sizing tweak, a monogram, etc. Purely
    # informational (never affects price); carried from the cart line
    # straight through to the order so it survives even if the customer
    # never comes back to this page.
    custom_request = models.TextField(blank=True)

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


class ManagerProfile(models.Model):
    """
    Manager-only account extras that don't belong on the auth User model
    itself - currently just the sidebar avatar shown in manager_base.html.
    Created on demand the first time a manager visits Settings or updates
    their photo (see _get_manager_profile in views.py), so existing
    managers don't need a data migration to get one.
    """

    THEME_LIGHT = "light"
    THEME_DARK = "dark"
    THEME_SYSTEM = "system"
    THEME_CHOICES = [
        (THEME_LIGHT, "Light"),
        (THEME_DARK, "Dark"),
        (THEME_SYSTEM, "Match system"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manager_profile"
    )
    photo = models.ImageField(upload_to="manager_avatars/", blank=True, null=True)

    # ---- appearance -------------------------------------------------
    theme_preference = models.CharField(
        max_length=10, choices=THEME_CHOICES, default=THEME_LIGHT,
        help_text="Applied across the whole manager area, on every device you sign into.",
    )

    # ---- notifications ------------------------------------------------
    # One master switch plus a per-event switch each, so a manager can
    # go fully quiet without losing their individual picks, or flip a
    # single event on/off without touching the others.
    email_notifications_enabled = models.BooleanField(
        default=True, help_text="Master switch - turn off to stop all manager emails.",
    )
    notify_new_orders = models.BooleanField(
        default=True, help_text="Email me when a customer places a new order.",
    )
    notify_new_reviews = models.BooleanField(
        default=True, help_text="Email me when a new product or store review is submitted for approval.",
    )
    notify_contact_messages = models.BooleanField(
        default=True, help_text="Email me when someone submits the contact form.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user}"

    def wants(self, event_flag_name):
        """
        True if this manager should be emailed for a given event -
        checked against both the master switch and the specific flag
        (e.g. profile.wants("notify_new_orders")).
        """
        return self.email_notifications_enabled and getattr(self, event_flag_name, False)


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