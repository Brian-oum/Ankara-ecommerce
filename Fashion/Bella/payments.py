"""
Glue between Daraja (mpesa.py) and our Order / Payment rows.

Everything that changes a payment's outcome goes through
apply_payment_result(), whether the news came from Safaricom's callback or
from our own STK-query fallback. That keeps the rules in one place:

  * idempotent   - Safaricom can (and does) deliver a callback twice
  * race-safe    - callback and status-poll can land at the same moment
  * Completed wins - if money actually moved, we never leave it as Failed
"""

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction

from .mpesa import DarajaClient, MpesaError, whole_shillings
from .models import Payment

logger = logging.getLogger(__name__)

# Shown to the customer instead of Safaricom's raw ResultDesc.
_FRIENDLY_FAILURES = {
    "1": "Your M-Pesa balance is too low to complete this payment.",
    "1001": "Another M-Pesa transaction is in progress on your phone. Wait a moment and try again.",
    "1019": "The payment request expired. Please try again.",
    "1025": "We couldn't send the prompt to your phone. Please try again.",
    "1032": "You cancelled the payment on your phone.",
    "1037": "The prompt timed out before a PIN was entered.",
    "2001": "The M-Pesa PIN entered was incorrect.",
    "9999": "We couldn't send the prompt to your phone. Please try again.",
}


def stk_enabled():
    """True if any gateway is live and customers will get a PIN prompt."""
    return bool(
        getattr(settings, "MPESA_LIVE_ENABLED", False)
        or getattr(settings, "JENGA_LIVE_ENABLED", False)
    )


def daraja_enabled():
    return bool(getattr(settings, "MPESA_LIVE_ENABLED", False))


def friendly_failure(result_code, result_desc=""):
    return _FRIENDLY_FAILURES.get(
        str(result_code),
        result_desc or "The payment was not completed.",
    )


def start_stk_push(payment):
    """
    Send (or re-send) the PIN prompt for this payment and reset it to Pending.
    Raises MpesaError on failure; the caller decides how to surface it.
    """
    result = DarajaClient().stk_push(
        phone=payment.phone,
        amount=payment.amount,
        account_reference=payment.order_reference,
        description="Order payment",
    )

    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
        payment.checkout_request_id = result["checkout_request_id"]
        payment.merchant_request_id = result["merchant_request_id"]
        payment.status = "Pending"
        payment.result_code = ""
        payment.result_desc = ""
        payment.save(update_fields=[
            "checkout_request_id", "merchant_request_id", "status",
            "result_code", "result_desc", "updated_at",
        ])
        if payment.order_id and payment.order.status != "Completed":
            payment.order.status = "Pending"
            payment.order.save(update_fields=["status", "updated_at"])
    return payment


def apply_payment_result(payment_id, *, success, receipt="", result_code="", result_desc=""):
    """
    Record the outcome of a payment. Returns (payment, changed).

    `changed` is False when this was a duplicate/late report and nothing was
    modified - callers can ignore it, it's for logging/tests.
    """
    # Imported lazily: emails.py pulls in the template/mail stack.
    from .emails import send_payment_confirmed_email, send_payment_failed_email

    new_status = "Completed" if success else "Failed"

    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("order").get(pk=payment_id)

        if payment.status == "Completed":
            return payment, False  # already settled - never downgrade a paid order
        if payment.status == new_status:
            return payment, False  # duplicate delivery
        # (Pending -> any, or Failed -> Completed: a late success still counts.)

        payment.status = new_status
        payment.result_code = str(result_code)[:10]
        payment.result_desc = (result_desc or "")[:255]
        if receipt:
            payment.telco_reference = receipt
        payment.save(update_fields=[
            "status", "result_code", "result_desc", "telco_reference", "updated_at",
        ])

        order = payment.order
        if order is not None:
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])

            # Send after commit so a mail hiccup can never roll back a real payment.
            if success:
                transaction.on_commit(
                    lambda: send_payment_confirmed_email(
                        order, order.items.select_related("product").all()
                    ),
                    robust=True,
                )
            else:
                transaction.on_commit(lambda: send_payment_failed_email(order), robust=True)

    logger.info(
        "Payment %s -> %s (code=%s receipt=%s)",
        payment.payment_reference, new_status, result_code, receipt,
    )
    return payment, True


def amount_matches(payment, paid_amount):
    """Callback amount must equal what we asked Daraja to charge."""
    try:
        return Decimal(str(paid_amount)) == Decimal(whole_shillings(payment.amount))
    except (InvalidOperation, ValueError, TypeError):
        return False