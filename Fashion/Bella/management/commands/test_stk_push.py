"""
Trigger a real Jenga STK push against whatever JENGA_BASE_URL / credentials
are currently in settings (sandbox or live - this command doesn't care,
it just uses what's configured) without going through the storefront UI.

It creates a genuine Order + Payment row, exactly like checkout() does, so
the callback for this test push will hit your real payment_callback view
and you can confirm the whole pipeline - not just the STK push call.

Usage:
    python manage.py test_stk_push --phone 0722000000 --amount 10
    python manage.py test_stk_push --phone 254722000000 --amount 10 --email test@example.com

Notes:
- JENGA_CALLBACK_URL must be reachable from the public internet for the
  callback to reach you (use ngrok or similar for local dev):
      ngrok http 8000
  then set JENGA_CALLBACK_URL to the ngrok https URL + /callback/
- Check your JengaHQ dashboard's Test Mode docs/API Explorer for any
  reserved test phone numbers your sandbox account expects.
"""

from django.core.management.base import BaseCommand, CommandError

from Bella.models import Order, Payment
from Bella.services import JengaClient, generate_reference


class Command(BaseCommand):
    help = "Fire a test Jenga STK push using the currently configured JENGA_* settings."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="e.g. 0722000000 or 254722000000")
        parser.add_argument("--amount", type=float, default=10.0)
        parser.add_argument("--name", default="Test Customer")
        parser.add_argument("--email", default="test@example.com")

    def handle(self, *args, **options):
        phone = options["phone"]
        amount = options["amount"]
        name = options["name"]
        email = options["email"]

        order_reference = generate_reference("OR")
        payment_reference = generate_reference("PR")

        order = Order.objects.create(
            order_reference=order_reference,
            name=name,
            email=email,
            phone=phone,
            total_amount=amount,
            status="Pending",
        )
        payment = Payment.objects.create(
            order=order,
            order_reference=order_reference,
            payment_reference=payment_reference,
            name=name,
            email=email,
            phone=phone,
            amount=amount,
            status="Pending",
        )

        self.stdout.write(f"Created Order {order_reference} / Payment {payment_reference}")
        self.stdout.write("Calling JengaClient.initiate_checkout()...")

        client = JengaClient()

        try:
            result = client.initiate_checkout(
                order_reference=order_reference,
                payment_reference=payment_reference,
                customer_name=name,
                customer_email=email,
                phone_number=phone,
                amount=amount,
                description=f"Test order {order_reference}",
            )
        except Exception as exc:  # noqa: BLE001 - want to see whatever Jenga/requests raised
            payment.status = "Failed"
            payment.save(update_fields=["status", "updated_at"])
            order.status = "Failed"
            order.save(update_fields=["status", "updated_at"])
            raise CommandError(f"STK push request failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Response from Jenga:"))
        self.stdout.write(str(result))

        if result.get("status"):
            payment.invoice_number = result.get("data", {}).get("invoiceNumber", "")
            payment.save(update_fields=["invoice_number", "updated_at"])
            self.stdout.write(self.style.SUCCESS(
                f"STK push accepted. Check the phone {phone} for the M-Pesa prompt, "
                f"then watch your logs for the callback to hit /callback/."
            ))
        else:
            payment.status = "Failed"
            payment.save(update_fields=["status", "updated_at"])
            order.status = "Failed"
            order.save(update_fields=["status", "updated_at"])
            self.stdout.write(self.style.ERROR(
                f"Jenga rejected the request: {result.get('message')}"
            ))