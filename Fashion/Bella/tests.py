import base64, json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Bella.models import Order, Payment
from Bella.mpesa import DarajaClient, MpesaError, normalize_msisdn, whole_shillings
from Bella import payments

TOKEN = "s3cret-token"
REF = "OR1234567890"

MPESA_TEST_SETTINGS = dict(
    MPESA_LIVE_ENABLED=True, MPESA_ENV="sandbox",
    MPESA_CONSUMER_KEY="k", MPESA_CONSUMER_SECRET="s",
    MPESA_SHORTCODE="174379", MPESA_PASSKEY="pk",
    MPESA_TRANSACTION_TYPE="CustomerPayBillOnline", MPESA_PARTY_B=None,
    MPESA_CALLBACK_BASE_URL="https://shop.example.com/", MPESA_CALLBACK_TOKEN=TOKEN,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    # Production uses CompressedManifestStaticFilesStorage, which raises
    # "Missing staticfiles manifest entry" for any {% static %} tag (e.g. in
    # base.html) unless collectstatic has run. Tests shouldn't depend on that.
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)

HOOK = reverse("mpesa_callback", args=[TOKEN])


class _EmailSpy:
    """Records which payment emails would have been sent."""
    def __init__(self): self.SENT = []
    def start(self):
        self._p = [
            mock.patch("Bella.emails.send_payment_confirmed_email", lambda o, i: self.SENT.append(("ok", o.order_reference))),
            mock.patch("Bella.emails.send_payment_failed_email", lambda o: self.SENT.append(("fail", o.order_reference))),
        ]
        for p in self._p: p.start()
    def stop(self):
        for p in self._p: p.stop()


def fake_resp(status=200, body=None):
    r = mock.Mock(); r.status_code = status; r.json.return_value = body if body is not None else {}
    r.text = json.dumps(body); r.raise_for_status.side_effect = None
    return r


def make_payment(amount="1235", status="Pending", cr="ws_CO_1", age=0, phone="254722000111"):
    order = Order.objects.create(order_reference=REF, name="A", email="a@b.co",
                                 phone=phone, total_amount=Decimal(amount), status=status)
    p = Payment.objects.create(order=order, order_reference=REF, payment_reference="PR1234567890",
                               phone=phone, amount=Decimal(amount), status=status, checkout_request_id=cr)
    if age:
        Payment.objects.filter(pk=p.pk).update(updated_at=timezone.now() - timedelta(seconds=age))
    return p


def cb_body(code=0, amount=1235, cr="ws_CO_1", receipt="QWE123"):
    cb = {"MerchantRequestID": "m1", "CheckoutRequestID": cr, "ResultCode": code,
          "ResultDesc": "ok" if code == 0 else "Request cancelled by user"}
    if code == 0:
        cb["CallbackMetadata"] = {"Item": [{"Name": "Amount", "Value": amount},
                                           {"Name": "MpesaReceiptNumber", "Value": receipt},
                                           {"Name": "PhoneNumber", "Value": 254722000111}]}
    return json.dumps({"Body": {"stkCallback": cb}})


@override_settings(**MPESA_TEST_SETTINGS)
class Helpers(TestCase):
    def test_msisdn(self):
        for raw in ["0722000111", "722000111", "+254722000111", "254 722 000 111", "0112345678", "0722-000-111"]:
            self.assertRegex(normalize_msisdn(raw), r"^254[71]\d{8}$")
        for bad in ["", "0622000111", "12345", "254722"]:
            with self.assertRaises(ValueError): normalize_msisdn(bad)

    def test_whole_shillings(self):
        self.assertEqual(whole_shillings(Decimal("1234.01")), 1235)
        self.assertEqual(whole_shillings(Decimal("1234.00")), 1234)
        self.assertEqual(whole_shillings("0.5"), 1)


@override_settings(**MPESA_TEST_SETTINGS)
class Client(TestCase):
    def setUp(self): cache.clear()

    @mock.patch("Bella.mpesa.requests")
    def test_stk_push_payload(self, rq):
        rq.RequestException = Exception
        rq.get.return_value = fake_resp(200, {"access_token": "TOK", "expires_in": "3599"})
        rq.post.return_value = fake_resp(200, {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_9", "MerchantRequestID": "mr9"})
        out = DarajaClient().stk_push(phone="0722000111", amount=Decimal("1234.20"),
                                      account_reference="OR1234567890EXTRA", description="Order payment long")
        self.assertEqual(out, {"checkout_request_id": "ws_CO_9", "merchant_request_id": "mr9"})
        url = rq.post.call_args.args[0]; kw = rq.post.call_args.kwargs
        self.assertTrue(url.endswith("/mpesa/stkpush/v1/processrequest"))
        self.assertEqual(kw["headers"]["Authorization"], "Bearer TOK")
        p = kw["json"]
        self.assertEqual(p["Amount"], 1235)
        self.assertEqual(p["PartyA"], "254722000111"); self.assertEqual(p["PhoneNumber"], "254722000111")
        self.assertEqual(p["PartyB"], "174379")
        self.assertEqual(len(p["AccountReference"]), 12); self.assertLessEqual(len(p["TransactionDesc"]), 13)
        self.assertEqual(p["CallBackURL"], f"https://shop.example.com/payments/hook/{TOKEN}/")
        self.assertEqual(base64.b64decode(p["Password"]).decode(), "174379pk" + p["Timestamp"])
        self.assertRegex(p["Timestamp"], r"^\d{14}$")

    @mock.patch("Bella.mpesa.requests")
    def test_token_cached_and_refreshed_on_401(self, rq):
        rq.RequestException = Exception
        rq.get.side_effect = [fake_resp(200, {"access_token": "OLD"}), fake_resp(200, {"access_token": "NEW"})]
        rq.post.side_effect = [fake_resp(401, {"errorMessage": "Invalid Access Token"}),
                               fake_resp(200, {"ResponseCode": "0", "CheckoutRequestID": "c"})]
        DarajaClient().stk_push(phone="0722000111", amount=10, account_reference="A")
        self.assertEqual(rq.get.call_count, 2)
        self.assertEqual(rq.post.call_args.kwargs["headers"]["Authorization"], "Bearer NEW")

    @mock.patch("Bella.mpesa.requests")
    def test_rejected_push_raises(self, rq):
        rq.RequestException = Exception
        rq.get.return_value = fake_resp(200, {"access_token": "T"})
        rq.post.return_value = fake_resp(400, {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"})
        with self.assertRaises(MpesaError): DarajaClient().stk_push(phone="0722000111", amount=10, account_reference="A")

    @mock.patch("Bella.mpesa.requests")
    def test_query_states(self, rq):
        rq.RequestException = Exception
        rq.get.return_value = fake_resp(200, {"access_token": "T"})
        c = DarajaClient()
        rq.post.return_value = fake_resp(500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"})
        self.assertEqual(c.stk_query("x")["state"], "pending")
        rq.post.return_value = fake_resp(200, {"ResultCode": "1032", "ResultDesc": "Request cancelled by user"})
        self.assertEqual(c.stk_query("x")["state"], "failed")
        rq.post.return_value = fake_resp(200, {"ResultCode": "0", "ResultDesc": "ok"})
        self.assertEqual(c.stk_query("x")["state"], "success")


@override_settings(**MPESA_TEST_SETTINGS)
class Callback(TestCase):
    def setUp(self):
        cache.clear(); self.spy = _EmailSpy(); self.spy.start(); self.addCleanup(self.spy.stop)
    def post(self, body, url=HOOK):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, body, content_type="application/json")

    def test_wrong_token_404(self):
        make_payment(); self.assertEqual(self.post(cb_body(), reverse("mpesa_callback", args=["nope"])).status_code, 404)
        self.assertEqual(Payment.objects.get().status, "Pending")

    def test_success_then_duplicate(self):
        make_payment()
        r = self.post(cb_body()); self.assertEqual(r.json(), {"ResultCode": 0, "ResultDesc": "Accepted"})
        p = Payment.objects.get(); self.assertEqual((p.status, p.telco_reference), ("Completed", "QWE123"))
        self.assertEqual(p.order.status, "Completed")
        self.post(cb_body())  # Safaricom redelivery
        self.assertEqual(self.spy.SENT, [("ok", REF)])  # exactly one email

    def test_amount_mismatch_not_completed(self):
        make_payment(); self.post(cb_body(amount=1))
        self.assertEqual(Payment.objects.get().status, "Pending"); self.assertEqual(self.spy.SENT, [])

    def test_cancelled(self):
        make_payment(); self.post(cb_body(code=1032))
        p = Payment.objects.get(); self.assertEqual((p.status, p.result_code), ("Failed", "1032"))
        self.assertEqual(p.order.status, "Failed"); self.assertEqual(self.spy.SENT, [("fail", REF)])

    def test_unknown_checkout_id_ignored(self):
        make_payment(); r = self.post(cb_body(cr="other")); self.assertEqual(r.status_code, 200)
        self.assertEqual(Payment.objects.get().status, "Pending")

    def test_late_success_beats_failed_but_not_reverse(self):
        make_payment(); self.post(cb_body(code=1037)); self.assertEqual(Payment.objects.get().status, "Failed")
        self.post(cb_body()); self.assertEqual(Payment.objects.get().status, "Completed")
        self.post(cb_body(code=1032)); self.assertEqual(Payment.objects.get().status, "Completed")

    def test_malformed(self):
        self.assertEqual(self.post("{not json").status_code, 400)
        self.assertEqual(self.post('{"Body":{}}').status_code, 400)

    def test_get_405(self):
        self.assertEqual(self.client.get(HOOK).status_code, 405)


@override_settings(**MPESA_TEST_SETTINGS)
class StatusAndRetry(TestCase):
    def setUp(self):
        cache.clear(); self.spy = _EmailSpy(); self.spy.start(); self.addCleanup(self.spy.stop)
    def status(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.get(reverse("payment_status", args=[REF])).json()

    @mock.patch("Bella.views.DarajaClient")
    def test_fresh_pending_does_not_query(self, dc):
        make_payment(age=3); self.assertEqual(self.status(), {"status": "Pending"}); dc.assert_not_called()

    @mock.patch("Bella.views.DarajaClient")
    def test_stale_pending_queries_and_completes(self, dc):
        make_payment(age=30)
        dc.return_value.stk_query.return_value = {"state": "success", "result_code": "0", "result_desc": "ok"}
        out = self.status(); self.assertEqual(out["status"], "Completed"); self.assertEqual(out["redirect"], reverse("order_confirmation", args=[REF]))
        self.assertEqual(self.spy.SENT, [("ok", REF)])

    @mock.patch("Bella.views.DarajaClient")
    def test_query_throttled_and_pending(self, dc):
        make_payment(age=30); dc.return_value.stk_query.return_value = {"state": "pending"}
        self.status(); self.status(); self.assertEqual(dc.return_value.stk_query.call_count, 1)

    @mock.patch("Bella.views.DarajaClient")
    def test_query_cancelled_gives_friendly_message(self, dc):
        make_payment(age=30); dc.return_value.stk_query.return_value = {"state": "failed", "result_code": "1032", "result_desc": "x"}
        out = self.status(); self.assertEqual(out["status"], "Failed"); self.assertIn("cancelled", out["message"])

    @mock.patch("Bella.views.DarajaClient")
    def test_query_error_is_swallowed(self, dc):
        make_payment(age=30); dc.return_value.stk_query.side_effect = MpesaError("down")
        self.assertEqual(self.status(), {"status": "Pending"})

    def set_session(self, ref="OR1234567890"):
        s = self.client.session; s["last_order_reference"] = ref; s.save()

    def test_retry_forbidden_without_session(self):
        make_payment(status="Failed"); self.assertEqual(self.client.post(reverse("payment_retry", args=[REF])).status_code, 403)

    def test_retry_blocked_when_prompt_fresh(self):
        make_payment(age=5); self.set_session()
        self.assertEqual(self.client.post(reverse("payment_retry", args=[REF])).status_code, 409)

    def test_retry_completed_blocked(self):
        make_payment(status="Completed"); self.set_session()
        self.assertEqual(self.client.post(reverse("payment_retry", args=[REF])).status_code, 409)

    @mock.patch("Bella.payments.DarajaClient")
    def test_retry_with_new_phone(self, dc):
        make_payment(status="Failed", cr="old"); self.set_session()
        dc.return_value.stk_push.return_value = {"checkout_request_id": "new", "merchant_request_id": "m2"}
        r = self.client.post(reverse("payment_retry", args=[REF]), {"phone": "0733111222"})
        self.assertEqual(r.json(), {"ok": True})
        p = Payment.objects.get()
        self.assertEqual((p.status, p.checkout_request_id, p.phone, p.order.phone, p.order.status),
                         ("Pending", "new", "254733111222", "254733111222", "Pending"))
        self.assertEqual(dc.return_value.stk_push.call_args.kwargs["phone"], "254733111222")
        # immediately after a send, the "prompt just sent" guard wins
        self.assertEqual(self.client.post(reverse("payment_retry", args=[REF])).status_code, 409)
        # and even if the payment flips to Failed at once, the cooldown stops rapid re-sends
        Payment.objects.update(status="Failed")
        self.assertEqual(self.client.post(reverse("payment_retry", args=[REF])).status_code, 429)
        cache.clear()
        Payment.objects.update(status="Pending")  # restore state for the callback checks below
        # old callback for the superseded prompt is ignored; new one applies
        self.client.post(HOOK, cb_body(code=1032, cr="old"), content_type="application/json")
        self.assertEqual(Payment.objects.get().status, "Pending")
        self.client.post(HOOK, cb_body(cr="new"), content_type="application/json")
        self.assertEqual(Payment.objects.get().status, "Completed")

    def test_retry_bad_phone(self):
        make_payment(status="Failed"); self.set_session()
        r = self.client.post(reverse("payment_retry", args=[REF]), {"phone": "123"}); self.assertEqual(r.status_code, 400)

    @mock.patch("Bella.payments.DarajaClient")
    def test_retry_gateway_error(self, dc):
        make_payment(status="Failed"); self.set_session(); dc.return_value.stk_push.side_effect = MpesaError("nope")
        r = self.client.post(reverse("payment_retry", args=[REF])); self.assertEqual((r.status_code, r.json()["message"]), (502, "nope"))

    def test_pending_page(self):
        p = make_payment(age=2)
        from Bella.models import Product, OrderItem
        prod = Product.objects.create(name="Ankara Bonnet", price=Decimal("500"))
        OrderItem.objects.create(order=p.order, product=prod, quantity=2, price=Decimal("500"),
                                 colour_name="Burgundy", colour_hex="#800020")
        r = self.client.get(reverse("payment_pending", args=[REF]))
        self.assertContains(r, "Ankara Bonnet"); self.assertContains(r, "Burgundy"); self.assertContains(r, "KES 1,000")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'data-status-url="{}"'.format(reverse("payment_status", args=[REF])))
        self.assertContains(r, "KES 1,235"); self.assertNotContains(r, '<form class="pay-retry"')  # no session -> no retry form
        self.set_session(); self.assertContains(self.client.get(reverse("payment_pending", args=[REF])), '<form class="pay-retry"')

    def test_pending_page_redirects_when_paid(self):
        make_payment(status="Completed"); r = self.client.get(reverse("payment_pending", args=[REF]))
        self.assertEqual((r.status_code, r["Location"]), (302, reverse("order_confirmation", args=[REF])))