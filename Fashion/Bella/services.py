import logging
import uuid
from base64 import b64encode

import requests
from django.conf import settings
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from .authentication import JengaAuthentication

logger = logging.getLogger(__name__)


class JengaClient:
    """
    Client for Jenga's Wallet-Based Settlement M-Pesa STK push
    ("Checkout" API) - this is the product the JengaHQ account is
    actually subscribed to ("MPESA Checkout as APIS").

    Funds land in your Jenga Wallet first; you settle them out to your
    Equity account from the Jenga portal (or via the Settlement API) -
    they are not credited to your bank account directly like the
    Account-Based Settlement product is.

    Docs: https://developer.jengahq.io/api-explorer/receive-money/
          mpesa-stk-push/wallet-based-settlement/mpesa-stk-push-init
    """

    CHECKOUT_INIT_PATH = "/api-checkout/mpesa-stk-push/v3.0/init"

    def __init__(self):
        self._access_token = None

    # ---- authentication -------------------------------------------------

    def get_access_token(self):
        if not self._access_token:
            auth = JengaAuthentication()
            token_response = auth.get_access_token()
            self._access_token = token_response["accessToken"]
        return self._access_token

    # ---- signing ----------------------------------------------------------

    def _sign(self, plain_text: str) -> str:
        with open(settings.JENGA_PRIVATE_KEY, "r") as key_file:
            private_key = RSA.import_key(key_file.read())

        digest = SHA256.new(plain_text.encode("utf-8"))
        signature = pkcs1_15.new(private_key).sign(digest)
        return b64encode(signature).decode("utf-8")

    def _checkout_signature(self, order_reference, currency, msisdn, amount_str):
        # Exact order required by Jenga - do not reorder or add separators.
        plain_text = f"{order_reference}{currency}{msisdn}{amount_str}"
        return self._sign(plain_text)

    @staticmethod
    def _to_local_msisdn(phone_number):
        """
        Jenga's Wallet-Based Checkout example uses LOCAL format
        (0722000111), unlike the Account-Based product which wants
        254722000111. Convert here so callers can keep passing the
        254... format everywhere else in the app.
        """
        digits = phone_number.strip()
        if digits.startswith("+"):
            digits = digits[1:]
        if digits.startswith("254") and len(digits) == 12:
            return "0" + digits[3:]
        return digits

    # ---- STK push (wallet-based checkout) ----------------------------------

    def initiate_checkout(
        self,
        order_reference,
        payment_reference,
        customer_name,
        customer_email,
        phone_number,
        amount,
        identity_number="00000000",
        currency="KES",
        description="Purchase",
    ):
        """
        order_reference / payment_reference: unique per order / per request
        phone_number: format 2547XXXXXXXX
        amount: Decimal/float
        """
        url = f"{settings.JENGA_BASE_URL}{self.CHECKOUT_INIT_PATH}"
        amount_str = f"{float(amount):.2f}"
        local_msisdn = self._to_local_msisdn(phone_number)

        signature = self._checkout_signature(
            order_reference=order_reference,
            currency=currency,
            msisdn=local_msisdn,
            amount_str=amount_str,
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.get_access_token()}",
            "Signature": signature,
        }

        payload = {
            "order": {
                "orderReference": order_reference,
                # IMPORTANT: same string used in the signature, not a
                # bare float - a float like 500.0 serializes differently
                # ("500.0") than the "500.00" signed above, and any
                # mismatch between the signed value and the body value
                # is rejected as an invalid signature.
                "orderAmount": amount_str,
                "orderCurrency": currency,
                "source": "APICHECKOUT",
                "countryCode": "KE",
                "description": description,
            },
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "phoneNumber": local_msisdn,
                "identityNumber": identity_number,
                "firstAddress": "",
                "secondAddress": "",
            },
            "payment": {
                "paymentReference": payment_reference,
                "paymentCurrency": currency,
                "channel": "MOBILE",
                "service": "MPESA",
                "provider": "JENGA",
                "callbackUrl": settings.JENGA_CALLBACK_URL,
                "details": {
                    "msisdn": local_msisdn,
                    "paymentAmount": amount_str,
                },
            },
        }

        logger.info(
            "Jenga checkout STK push signing plaintext=%r signature=%s",
            f"{order_reference}{currency}{local_msisdn}{amount_str}",
            signature,
        )

        response = requests.post(url, headers=headers, json=payload, timeout=30)

        logger.info(
            "Jenga checkout STK push status=%s body=%s",
            response.status_code,
            response.text,
        )

        response.raise_for_status()
        return response.json()


    # ---- order status -------------------------------------------------

    def query_order_status(self, order_reference):
        """
        GET the real, current status of an order - useful when the
        initiate response is ambiguous. No signature required, just the
        bearer token.
        """
        url = (
            f"{settings.JENGA_BASE_URL}"
            f"/api-checkout/mpesa-stk-push/v3.0/status/order/{order_reference}"
        )
        headers = {"Authorization": f"Bearer {self.get_access_token()}"}

        response = requests.get(url, headers=headers, timeout=30)

        logger.info(
            "Jenga order status status=%s body=%s",
            response.status_code,
            response.text,
        )

        response.raise_for_status()
        return response.json()


def generate_reference(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:10].upper()}"