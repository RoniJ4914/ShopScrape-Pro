"""
Discord formatter -- produces a single grouped summary message per the
spec's example, rather than one message per event:

    🚨 ShopShopLive
    68 New Products
    12 Price Drops
    5 Restocks
    Top Changes
    • Nike Air Max
    • Adidas Hoodie
    • Winter Jacket
    View Dashboard →
"""

from __future__ import annotations

from typing import Optional

from app.alerts.grouping import AlertDigest


def format_discord_digest(digest: AlertDigest, dashboard_url: Optional[str] = None) -> dict:
    lines = [f"🚨 **{digest.store_name}**", ""]

    for label, count in digest.counts.items():
        lines.append(f"{count} {label}")

    if digest.top_changes:
        lines.append("")
        lines.append("**Top Changes**")
        for title in digest.top_changes:
            lines.append(f"• {title}")

    if dashboard_url:
        lines.append("")
        lines.append(f"[View Dashboard →]({dashboard_url})")

    content = "\n".join(lines)
    # Discord webhook payload shape: https://discord.com/developers/docs/resources/webhook
    return {"content": content}


def format_discord_single_event(event: dict) -> dict:
    """Fallback for ungrouped/single-event sends (rule.grouped = False)."""
    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(event.get("severity", "info"), "ℹ️")
    return {"content": f"{severity_emoji} {event.get('message', '')}"}
