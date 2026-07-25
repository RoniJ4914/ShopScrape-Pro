"""
NOT part of the original upload -- reconstructed so `app.alerts.engine`'s
`from .senders import SENDER_REGISTRY` resolves. If your real repo
already has this file, keep yours instead.

No "sms" entry: `AlertRule.channel` allows "sms" as a value, but no SMS
sender was among the files you gave me, so `engine.py`'s
`SENDER_REGISTRY.get(rule.channel)` will correctly return None (skip +
log) for a store misconfigured with an sms rule until one is added.
"""

from .discord_sender import DiscordSender
from .slack_sender import SlackSender
from .webhook_sender import WebhookSender
from .email_sender import EmailSender

SENDER_REGISTRY = {
    "discord": DiscordSender,
    "slack": SlackSender,
    "webhook": WebhookSender,
    "email": EmailSender,
}

__all__ = ["SENDER_REGISTRY"]
