from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

from .base import BaseSender

logger = logging.getLogger(__name__)


class EmailSender(BaseSender):
    """
    Sends via SMTP using stdlib `smtplib` -- no extra dependency needed.
    Configured entirely through environment variables so credentials never
    live in code:

        SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_ADDRESS
    """
    channel = "email"

    def __init__(self):
        self.host = os.environ.get("SMTP_HOST")
        self.port = int(os.environ.get("SMTP_PORT", 587))
        self.username = os.environ.get("SMTP_USERNAME")
        self.password = os.environ.get("SMTP_PASSWORD")
        self.from_address = os.environ.get("SMTP_FROM_ADDRESS", self.username or "alerts@shopscrapepro.com")

    async def send(self, destination: str, payload: Tuple[str, str, str]) -> bool:
        """`payload` is (subject, text_body, html_body) from format_email_digest()."""
        if not self.host:
            logger.warning("SMTP_HOST not configured; skipping email send to %s", destination)
            return False

        subject, text_body, html_body = payload
        return await asyncio.to_thread(self._send_sync, destination, subject, text_body, html_body)

    def _send_sync(self, destination: str, subject: str, text_body: str, html_body: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_address
            msg["To"] = destination
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.sendmail(self.from_address, [destination], msg.as_string())
            return True
        except Exception as exc:  # noqa: BLE001 -- one failed email must not crash the alert run
            logger.warning("Email send to %s failed: %s", destination, exc)
            return False
