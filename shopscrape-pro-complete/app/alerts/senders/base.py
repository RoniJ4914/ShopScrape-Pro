"""
NOT part of the original upload -- `discord_sender.py`, `slack_sender.py`,
`webhook_sender.py`, and `email_sender.py` all do `from .base import
BaseSender`, but `base.py` itself wasn't among the files you gave me.
This is a minimal reconstruction of the interface those four files
actually use (`channel: str` class attribute, async `send(destination,
payload) -> bool`). If your real repo already has this file, use yours
instead -- this one is inferred, not original.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseSender(ABC):
    channel: str

    @abstractmethod
    async def send(self, destination: str, payload: Any) -> bool:
        """Send `payload` to `destination`. Returns True on success.

        Implementations must swallow and log their own failures (network
        errors, non-2xx responses, etc.) rather than raising -- per the
        Alert Engine's contract, one broken destination must never take
        down the rest of a store's alert run.
        """
        raise NotImplementedError
