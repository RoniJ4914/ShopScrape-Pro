"""
Generic webhook formatter -- unlike Discord/Slack, a custom webhook
consumer wants raw structured data, not a pre-rendered message string.
"""

from __future__ import annotations

from typing import Dict, Any

from app.alerts.grouping import AlertDigest


def format_webhook_payload(digest: AlertDigest) -> Dict[str, Any]:
    return {
        "store_id": digest.store_id,
        "store_name": digest.store_name,
        "total_events": digest.total_events,
        "counts": digest.counts,
        "top_changes": digest.top_changes,
        "events": digest.events,
    }
