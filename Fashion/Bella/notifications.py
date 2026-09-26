"""
Manager notification emails - separate from emails.py, which handles
customer-facing order emails. These go the other direction: to staff,
about things staff want to know about (new orders, new reviews awaiting
approval, new contact messages), gated per-manager by the preferences
on their ManagerProfile (see Settings -> Preferences).

Sending never blocks or breaks the request that triggered it - any
failure is logged and swallowed, same spirit as the Jenga error
handling elsewhere in views.py.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import ManagerProfile

logger = logging.getLogger(__name__)


def _recipients_for(event_flag_name):
    """
    Emails of every manager whose ManagerProfile has both the master
    switch and this specific event flag turned on. Managers with no
    profile yet (very first login, before they've ever hit Settings)
    are skipped rather than defaulting them in - the profile is
    lazily created with the field defaults the moment they visit
    Settings or update their avatar, so this is a narrow, short-lived
    gap in practice.
    """
    return list(
        ManagerProfile.objects
        .filter(email_notifications_enabled=True, **{event_flag_name: True})
        .exclude(user__email="")
        .select_related("user")
        .values_list("user__email", flat=True)
    )


def _send(event_flag_name, subject, message):
    recipients = _recipients_for(event_flag_name)
    if not recipients:
        return

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:
        # A manager's inbox being unreachable shouldn't fail the
        # checkout, review, or contact-form request that triggered it.
        logger.exception("Manager notification email failed (%s)", subject)


def notify_new_order(order):
    subject = f"New order {order.order_reference} - KES {order.total_amount:,.0f}"
    message = (
        f"{order.name} just placed an order for KES {order.total_amount:,.0f}.\n\n"
        f"Reference: {order.order_reference}\n"
        f"Phone: {order.phone}\n"
        f"Email: {order.email}\n"
        f"Delivery area: {order.delivery_area or '-'}\n\n"
        "View it under Orders in the manager dashboard."
    )
    _send("notify_new_orders", subject, message)


def notify_new_review(kind, name, rating, comment):
    """kind is a short label - "product" or "store" - for the subject line."""
    subject = f"New {kind} review submitted ({rating} star{'s' if rating != 1 else ''})"
    body_lines = [f"{name} left a {rating}-star {kind} review."]
    if comment:
        body_lines.append(f'\n"{comment}"')
    body_lines.append("\n\nIt's waiting for approval in Django admin before it goes live.")
    _send("notify_new_reviews", subject, "\n".join(body_lines))


def notify_contact_message(contact_message):
    subject = f"New contact message: {contact_message.subject or 'No subject'}"
    message = (
        f"From: {contact_message.name} <{contact_message.email}>\n\n"
        f"{contact_message.message}"
    )
    _send("notify_contact_messages", subject, message)