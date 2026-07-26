from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Category, Product, ProductVariant, Order, OrderItem, Payment, ContactMessage,
    ProductReview, StoreReview,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("name", "price", "stock", "sort_order", "is_default")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "display_price", "display_stock", "is_active", "created_at")
    list_filter = ("category", "is_active")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductVariantInline]

    fieldsets = (
        (None, {"fields": ("category", "name", "slug", "description", "image", "is_active")}),
        (
            "Simple product price/stock",
            {
                "fields": ("price", "stock"),
                "description": (
                    "Only used if this product has NO variants below. As soon as you add "
                    "one or more variants (e.g. sizes, materials), their prices/stock take "
                    "over and these two fields are ignored on the storefront."
                ),
            },
        ),
    )

    def display_price(self, obj):
        low, high = obj.price_range()
        return f"KES {low:,.0f}" if low == high else f"KES {low:,.0f}–{high:,.0f}"
    display_price.short_description = "Price"

    def display_stock(self, obj):
        return obj.total_stock()
    display_stock.short_description = "Stock"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_reference", "name", "phone", "total_amount", "status", "created_at", "whatsapp_button")
    list_filter = ("status",)
    search_fields = ("order_reference", "name", "phone", "email")
    readonly_fields = ("whatsapp_button",)
    inlines = [OrderItemInline]

    def whatsapp_button(self, obj):
        # Business -> Customer: opens a chat to the CUSTOMER's number,
        # pre-filled confirming their order was received. Works no
        # matter how `status` got to Completed - Jenga callback or a
        # manual edit right here in admin - since the link is built
        # fresh from the order's own data each time it's rendered.
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" class="button">Reply to customer on WhatsApp</a>',
            obj.get_admin_reply_whatsapp_link(),
        )
    whatsapp_button.short_description = "WhatsApp"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "order_reference", "amount", "status", "telco_reference", "created_at")
    list_filter = ("status",)
    search_fields = ("payment_reference", "order_reference", "phone", "telco_reference")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "subject", "created_at")
    search_fields = ("name", "email", "subject", "message")


class _ApprovableReviewAdmin(admin.ModelAdmin):
    """Shared bulk-approve action for both review types."""
    actions = ["approve_reviews"]

    @admin.action(description="Approve selected reviews")
    def approve_reviews(self, request, queryset):
        updated = queryset.update(is_approved=True)
        self.message_user(request, f"{updated} review(s) approved.")


@admin.register(ProductReview)
class ProductReviewAdmin(_ApprovableReviewAdmin):
    list_display = ("product", "name", "rating", "is_approved", "created_at")
    list_filter = ("is_approved", "rating")
    search_fields = ("product__name", "name", "email", "comment")


@admin.register(StoreReview)
class StoreReviewAdmin(_ApprovableReviewAdmin):
    list_display = ("name", "rating", "is_approved", "created_at")
    list_filter = ("is_approved", "rating")
    search_fields = ("name", "email", "comment")