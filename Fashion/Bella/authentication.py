import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class JengaAuthentication:
    """Handles merchant authentication against the Jenga Identity API."""

    def __init__(self):
        self.url = (
            f"{settings.JENGA_BASE_URL}"
            "/authentication/api/v3/authenticate/merchant"
        )

    def get_access_token(self):
        headers = {
            "Content-Type": "application/json",
            "Api-Key": settings.JENGA_API_KEY,
        }

        payload = {
            "merchantCode": settings.JENGA_MERCHANT_CODE,
            "consumerSecret": settings.JENGA_CONSUMER_SECRET,
        }

        response = requests.post(
            self.url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        logger.info(
            "Jenga auth response status=%s body=%s",
            response.status_code,
            response.text,
        )

        response.raise_for_status()

        return response.json()