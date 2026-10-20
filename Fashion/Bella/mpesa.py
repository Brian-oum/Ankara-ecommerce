"""
Direct Safaricom Daraja integration: M-Pesa Express (STK push).

Flow
----
1. DarajaClient.stk_push()  -> customer gets the PIN prompt on their phone.
   Daraja replies immediately with a CheckoutRequestID (NOT the payment result).
2. Safaricom POSTs the real result to our callback URL (see
   mpesa_views.mpesa_callback), and/or we ask for it with stk_query().

This module only talks to Daraja. It knows nothing about Orders/Payments -
that glue lives in payments.py.
"""

import base64
import logging
import re
from decimal import ROUND_CEILING, Decimal

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}

_TOKEN_CACHE_KEY = "mpesa:daraja:access_token"
# Daraja tokens live for 3599s; refresh a bit early.
_TOKEN_TTL_SECONDS = 3300


class MpesaError(Exception):
    """Raised for any failure talking to Daraja. `str(exc)` is safe to show customers."""


def whole_shillings(amount):
    """
    Daraja only accepts whole-number amounts. Always round UP so we never
    under-charge by a few cents (e.g. 1,234.20 -> 1,235).
    """
    return int(Decimal(str(amount)).to_integral_value(rounding=ROUND_CEILING))


def normalize_msisdn(raw):
    """
    Any common Kenyan format -> 2547XXXXXXXX / 2541XXXXXXXX.
    Raises ValueError if it isn't a valid Safaricom-style mobile number.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    if not re.fullmatch(r"254[71]\d{8}", digits):
        raise ValueError("Enter a valid Safaricom number, e.g. 0722000000")
    return digits


class DarajaClient:
    STK_PATH = "/mpesa/stkpush/v1/processrequest"
    QUERY_PATH = "/mpesa/stkpushquery/v1/query"
    TOKEN_PATH = "/oauth/v1/generate?grant_type=client_credentials"

    def __init__(self):
        env = getattr(settings, "MPESA_ENV", "sandbox")
        if env not in BASE_URLS:
            raise MpesaError(f"MPESA_ENV must be one of {sorted(BASE_URLS)}, got {env!r}")
        self.base_url = BASE_URLS[env]
        self.shortcode = settings.MPESA_SHORTCODE
        self.passkey = settings.MPESA_PASSKEY

    # ---- auth ---------------------------------------------------------------

    def _access_token(self):
        token = cache.get(_TOKEN_CACHE_KEY)
        if token:
            return token

        try:
            resp = requests.get(
                f"{self.base_url}{self.TOKEN_PATH}",
                auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
                timeout=20,
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        except (requests.RequestException, KeyError, ValueError):
            logger.exception("Daraja OAuth token request failed")
            raise MpesaError("Could not reach M-Pesa right now. Please try again.")

        cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL_SECONDS)
        return token

    def _post(self, path, payload):
        """POST with bearer token; retries once if the cached token was rejected."""
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            try:
                resp = requests.post(
                    f"{self.base_url}{path}", json=payload, headers=headers, timeout=30
                )
            except requests.RequestException:
                logger.exception("Daraja request to %s failed", path)
                raise MpesaError("Could not reach M-Pesa right now. Please try again.")

            if resp.status_code == 401 and attempt == 1:
                cache.delete(_TOKEN_CACHE_KEY)  # stale token - fetch a fresh one
                continue
            break

        try:
            body = resp.json()
        except ValueError:
            logger.error("Daraja non-JSON response %s: %s", resp.status_code, resp.text[:500])
            raise MpesaError("M-Pesa returned an unexpected response. Please try again.")

        # Never log the password/passkey - payload is logged without it.
        logger.info("Daraja %s -> HTTP %s body=%s", path, resp.status_code, body)
        return resp.status_code, body

    # ---- helpers --------------------------------------------------------------

    def _timestamp(self):
        return timezone.localtime().strftime("%Y%m%d%H%M%S")

    def _password(self, timestamp):
        raw = f"{self.shortcode}{self.passkey}{timestamp}"
        return base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    @staticmethod
    def callback_url():
        base = settings.MPESA_CALLBACK_BASE_URL.rstrip("/")
        return f"{base}/payments/hook/{settings.MPESA_CALLBACK_TOKEN}/"

    # ---- STK push -------------------------------------------------------------

    def stk_push(self, *, phone, amount, account_reference, description="Order payment"):
        """
        Send the PIN prompt. Returns {"checkout_request_id", "merchant_request_id"}.

        account_reference: max 12 chars (shown on the customer's M-Pesa SMS)
        description:       max 13 chars
        """
        try:
            msisdn = normalize_msisdn(phone)
        except ValueError as exc:
            raise MpesaError(str(exc))

        timestamp = self._timestamp()
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": self._password(timestamp),
            "Timestamp": timestamp,
            "TransactionType": settings.MPESA_TRANSACTION_TYPE,
            "Amount": whole_shillings(amount),
            "PartyA": msisdn,
            "PartyB": settings.MPESA_PARTY_B or self.shortcode,
            "PhoneNumber": msisdn,
            "CallBackURL": self.callback_url(),
            "AccountReference": account_reference[:12],
            "TransactionDesc": description[:13],
        }

        status, body = self._post(self.STK_PATH, payload)

        if status != 200 or str(body.get("ResponseCode")) != "0":
            logger.error("Daraja STK push rejected: HTTP %s %s", status, body)
            raise MpesaError(
                "M-Pesa could not send the payment prompt. "
                "Please check your number and try again."
            )

        return {
            "checkout_request_id": body["CheckoutRequestID"],
            "merchant_request_id": body.get("MerchantRequestID", ""),
        }

    # ---- STK query ------------------------------------------------------------

    def stk_query(self, checkout_request_id):
        """
        Ask Daraja what happened to a push. Returns one of:
            {"state": "pending"}
            {"state": "success", "result_code": "0",    "result_desc": "..."}
            {"state": "failed",  "result_code": "1032", "result_desc": "..."}
        Safety net for when our callback never arrives.
        """
        timestamp = self._timestamp()
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": self._password(timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        status, body = self._post(self.QUERY_PATH, payload)

        if "ResultCode" in body:
            code = str(body["ResultCode"])
            return {
                "state": "success" if code == "0" else "failed",
                "result_code": code,
                "result_desc": body.get("ResultDesc", ""),
            }

        # While the customer is still looking at the prompt Daraja answers with an
        # *error* body (500.001.1001 "being processed") rather than a result.
        return {"state": "pending"}