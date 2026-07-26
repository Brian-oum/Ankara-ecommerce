"""
Send order details to WhatsApp via a plain `wa.me` click-to-chat link -
no API, no approval process, no fees. The tradeoff: this only opens a
chat with the message pre-typed in; a person still has to tap Send.

Two distinct links live here, going in opposite directions:

  build_order_whatsapp_link()
      Customer -> Business. Shown to the customer on their order
      confirmation page. They tap it to tell the store "I've placed
      this order" over WhatsApp.

  build_order_admin_reply_link()
      Business -> Customer. Shown to the admin in Django admin. The
      admin taps it to reply to the customer's own number, confirming
      the order was received / is being prepared.

Required setting (add to settings.py):

    WHATSAPP_BUSINESS_NUMBER = "2547XXXXXXXX"   # your store's number,
                                                  # country code, no '+',
                                                  # no leading 0

To get an actual automated reply without writing any server code, turn
on WhatsApp Business App's built-in "Away message" or "Greeting message"
(Settings > Business tools > Away message / Greeting message on the
phone that owns WHATSAPP_BUSINESS_NUMBER). It fires automatically the
moment a customer's message arrives - which is exactly when they tap
Send on the customer-facing link this module builds. It's a generic
stand-in, though - build_order_admin_reply_link() is for the real,
order-specific reply.
"""

from urllib.parse import quote

from django.conf import settings


def _normalize_msisdn(phone):
    """Turn '07xx...' / '+2547xx...' / '2547xx...' into '2547xx...'."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    return phone


def _order_summary_lines(order_items):
    lines = [
        f"{item.quantity}x {item.product.name} - KES {item.subtotal()}"
        for item in order_items
    ]
    return "\n".join(lines) if lines else "(no line items found)"


def build_order_whatsapp_link(order, order_items, to_number=None):
    """
    Customer -> Business. Return a https://wa.me/... URL that, when
    opened, launches WhatsApp with a chat to `to_number` (defaults to
    settings.WHATSAPP_BUSINESS_NUMBER) pre-filled with a summary of the
    given order, worded as the customer confirming they've placed it.

    `order_items` - iterable of OrderItem instances (order.items.all()).
    """
    to_number = _normalize_msisdn(to_number or settings.WHATSAPP_BUSINESS_NUMBER)

    message = (
        f"Hi! I'd like to confirm my order {order.order_reference}.\n\n"
        f"Name: {order.name}\n"
        f"Phone: {order.phone}\n\n"
        f"{_order_summary_lines(order_items)}\n\n"
        f"Total: KES {order.total_amount}"
    )

    return f"https://wa.me/{to_number}?text={quote(message)}"


def build_order_admin_reply_link(order, order_items):
    """
    Business -> Customer. Return a https://wa.me/... URL that opens a
    chat to the CUSTOMER's own number (order.phone), pre-filled with a
    message from the store confirming the order was received. Meant for
    the admin to tap after checking an order - regardless of whether
    `status` got to Completed via the Jenga callback or a manual edit.
    """
    to_number = _normalize_msisdn(order.phone)

    message = (
        f"Hi {order.name}! We've received your order {order.order_reference} "
        f"(KES {order.total_amount}) and it's being prepared.\n\n"
        f"{_order_summary_lines(order_items)}\n\n"
        f"Thank you for shopping with us!"
    )

    return f"https://wa.me/{to_number}?text={quote(message)}"