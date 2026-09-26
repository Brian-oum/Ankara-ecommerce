"""
Transactional emails for the order lifecycle - the counterpart to
whatsapp.py. Customers get both: an email lands automatically at every
stage below with no one having to tap anything, while the WhatsApp side
stays a tap-to-send link an admin (or the customer, for the very first
one) triggers - WhatsApp doesn't offer a fee-free way to send without
that tap. Between the two, every stage always reaches the customer on
at least one channel, usually both.

Every stage gets its own function here, and each is called explicitly
from the exact place that stage happens - never from a signal - so the
whole timeline is traceable by reading the call sites:

    checkout()                              -> send_order_placed_email
    payment_callback() / manager_order_update_status()
                                             -> send_payment_confirmed_email
                                             -> send_payment_failed_email
    manager_order_update_delivery_status()  -> send_order_shipped_email
                                             -> send_order_delivered_email

Sending never raises. A broken SMTP config or a flaky network shouldn't
ever break checkout, or the Jenga webhook (which Jenga expects a fast
200 from no matter what) - failures are logged instead, same spirit as
requests.HTTPError handling around the Jenga client elsewhere.

Required settings (add to settings.py):

    DEFAULT_FROM_EMAIL = "Etsirbella Designs <hello@etsirbella.co.ke>"

    # any standard Django email backend - for real delivery, e.g.:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.yourprovider.com"
    EMAIL_HOST_USER = "..."
    EMAIL_HOST_PASSWORD = "..."
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True

    # while developing, this just prints emails to the console/terminal
    # instead of actually sending anything:
    # EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _order_summary_lines(order_items):
    lines = [
        f"{item.quantity}x {item.product.name} - KES {item.subtotal()}"
        for item in order_items
    ]
    return "\n".join(lines) if lines else "(no line items found)"


def _send(subject, body, to_email):
    """Swallow and log failures - see module docstring for why."""
    if not to_email:
        return
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[to_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send email %r to %s", subject, to_email)


def send_order_placed_email(order, order_items):
    """Sent the moment checkout creates the order, before payment is confirmed."""
    subject = f"We've received your order {order.order_reference}"
    body = (
        f"Hi {order.name},\n\n"
        f"Thanks for shopping with Etsirbella Designs! We've received your "
        f"order {order.order_reference} and it's now being processed.\n\n"
        f"{_order_summary_lines(order_items)}\n\n"
        f"Total: KES {order.total_amount}\n\n"
        f"We'll email you again the moment payment is confirmed, and again "
        f"once your order ships.\n\n"
        f"- Etsirbella Designs"
    )
    _send(subject, body, order.email)


def send_payment_confirmed_email(order, order_items):
    """Sent from payment_callback() or a manual admin status change to Completed."""
    subject = f"Payment confirmed for order {order.order_reference}"
    body = (
        f"Hi {order.name},\n\n"
        f"Good news - we've confirmed payment for order "
        f"{order.order_reference}. It's now being prepared for shipping.\n\n"
        f"{_order_summary_lines(order_items)}\n\n"
        f"Total: KES {order.total_amount}\n\n"
        f"We'll let you know as soon as it's on its way.\n\n"
        f"- Etsirbella Designs"
    )
    _send(subject, body, order.email)


def send_payment_failed_email(order):
    """Sent from payment_callback() or a manual admin status change to Failed."""
    subject = f"We couldn't confirm payment for order {order.order_reference}"
    body = (
        f"Hi {order.name},\n\n"
        f"We weren't able to confirm payment for order "
        f"{order.order_reference}. No charge should have gone through, but "
        f"if you were charged, or would like to try again, just reply to "
        f"this email or message us on WhatsApp and we'll sort it out right "
        f"away.\n\n"
        f"- Etsirbella Designs"
    )
    _send(subject, body, order.email)


def send_order_shipped_email(order):
    """Sent when a manager moves delivery_status to 'shipped'."""
    subject = f"Your order {order.order_reference} is on its way"
    body = (
        f"Hi {order.name},\n\n"
        f"Your order {order.order_reference} has shipped and is on its way "
        f"to you.\n\n"
        f"Total: KES {order.total_amount}\n\n"
        f"We'll email you again the moment it's delivered.\n\n"
        f"- Etsirbella Designs"
    )
    _send(subject, body, order.email)


def send_order_delivered_email(order):
    """Sent when a manager moves delivery_status to 'delivered'."""
    subject = f"Delivered! Order {order.order_reference}"
    body = (
        f"Hi {order.name},\n\n"
        f"Order {order.order_reference} has been delivered. We hope you "
        f"love it!\n\n"
        f"If anything isn't right, just reply to this email or message us "
        f"on WhatsApp and we'll make it right.\n\n"
        f"Thank you for shopping with Etsirbella Designs.\n\n"
        f"- Etsirbella Designs"
    )
    _send(subject, body, order.email)