from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Category, Product, ProductVariant, ProductColour, Order, OrderItem, Payment, ContactMessage,
    ProductReview, StoreReview, ManagerProfile,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("name", "price", "sort_order", "is_default")


class ProductColourInline(admin.TabularInline):
    model = ProductColour
    extra = 1
    fields = ("name", "hex_code", "sort_order")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "display_price",  "is_active", "created_at")
    list_filter = ("category", "is_active")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductVariantInline, ProductColourInline]

    fieldsets = (
        (None, {"fields": ("category", "name", "slug", "description", "image", "price", "is_active")}),
        
    )

    def display_price(self, obj):
        low, high = obj.price_range()
        return f"KES {low:,.0f}" if low == high else f"KES {low:,.0f}–{high:,.0f}"
    display_price.short_description = "Price"



class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_reference", "name", "phone", "total_amount", "status",
        "delivery_status", "created_at", "whatsapp_button",
    )
    list_filter = ("status", "delivery_status")
    search_fields = ("order_reference", "name", "phone", "email")
    readonly_fields = ("whatsapp_button", "shipped_whatsapp_button", "delivered_whatsapp_button")
    inlines = [OrderItemInline]
    actions = ["mark_shipped", "mark_delivered"]

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
    whatsapp_button.short_description = "WhatsApp: order received"

    def shipped_whatsapp_button(self, obj):
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" class="button">Tell customer it shipped</a>',
            obj.get_shipped_whatsapp_link(),
        )
    shipped_whatsapp_button.short_description = "WhatsApp: shipped"

    def delivered_whatsapp_button(self, obj):
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" class="button">Tell customer it arrived</a>',
            obj.get_delivered_whatsapp_link(),
        )
    delivered_whatsapp_button.short_description = "WhatsApp: delivered"

    @admin.action(description="Mark shipped — email customer + prep WhatsApp reply")
    def mark_shipped(self, request, queryset):
        from django.utils import timezone

        from .emails import send_order_shipped_email

        updated = 0
        for order in queryset.filter(status="Completed"):
            order.delivery_status = "shipped"
            if not order.shipped_at:
                order.shipped_at = timezone.now()
            order.save(update_fields=["delivery_status", "shipped_at", "updated_at"])
            send_order_shipped_email(order)
            updated += 1
        skipped = queryset.exclude(status="Completed").count()
        message = f"{updated} order(s) marked shipped and emailed."
        if skipped:
            message += f" Skipped {skipped} whose payment isn't Completed yet."
        self.message_user(request, message)

    @admin.action(description="Mark delivered — email customer + prep WhatsApp reply")
    def mark_delivered(self, request, queryset):
        from django.utils import timezone

        from .emails import send_order_delivered_email

        updated = 0
        for order in queryset.filter(status="Completed"):
            order.delivery_status = "delivered"
            if not order.delivered_at:
                order.delivered_at = timezone.now()
            order.save(update_fields=["delivery_status", "delivered_at", "updated_at"])
            send_order_delivered_email(order)
            updated += 1
        skipped = queryset.exclude(status="Completed").count()
        message = f"{updated} order(s) marked delivered and emailed."
        if skipped:
            message += f" Skipped {skipped} whose payment isn't Completed yet."
        self.message_user(request, message)


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


@admin.register(ManagerProfile)
class ManagerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name")