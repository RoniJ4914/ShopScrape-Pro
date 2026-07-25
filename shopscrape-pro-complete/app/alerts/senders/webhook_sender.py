from __future__ import annotations

import logging
from typing import Dict, Any

import httpx

from .base import BaseSender

logger = logging.getLogger(__name__)


class WebhookSender(BaseSender):
    channel = "webhook"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def send(self, destination: str, payload: Dict[str, Any]) -> bool:
        client_owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(destination, json=payload, headers={"User-Agent": "ShopScrapePro-AlertEngine/1.0"})
            if resp.status_code >= 300:
                logger.warning("Webhook %s returned %s", destination, resp.status_code)
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning("Webhook send to %s failed: %s", destination, exc)
            return False
        finally:
            if client_owned:
                await client.aclose()
