"""
Slack formatter -- uses Block Kit mrkdwn so the digest renders with the
same visual structure as Discord (bold header, count list, top changes)
inside a Slack incoming-webhook payload.
"""

from __future__ import annotations

from typing import Optional

from app.alerts.grouping import AlertDigest


def format_slack_digest(digest: AlertDigest, dashboard_url: Optional[str] = None) -> dict:
    count_lines = "\n".join(f"• {count} {label}" for label, count in digest.counts.items())

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f":rotating_light: *{digest.store_name}*"}},
    ]
    if count_lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": count_lines}})

    if digest.top_changes:
        top_lines = "\n".join(f"• {title}" for title in digest.top_changes)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Top Changes*\n{top_lines}"}})

    if dashboard_url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{dashboard_url}|View Dashboard →>"},
        })

    # Fallback plain-text `text` field for notifications/accessibility
    fallback = f"{digest.store_name}: {digest.total_events} events"
    return {"text": fallback, "blocks": blocks}


def format_slack_single_event(event: dict) -> dict:
    severity_emoji = {"info": ":information_source:", "warning": ":warning:", "critical": ":rotating_light:"}
    emoji = severity_emoji.get(event.get("severity", "info"), ":information_source:")
    return {"text": f"{emoji} {event.get('message', '')}"}
