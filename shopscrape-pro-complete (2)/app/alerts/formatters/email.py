"""
Email formatter -- plain-text + minimal HTML body. Kept intentionally
simple (no external template engine dependency) since this is one alert
channel among several, not the product's primary surface.
"""

from __future__ import annotations

from typing import Optional, Tuple

from app.alerts.grouping import AlertDigest


def format_email_digest(digest: AlertDigest, dashboard_url: Optional[str] = None) -> Tuple[str, str, str]:
    """Returns (subject, plain_text_body, html_body)."""
    subject = f"ShopScrape Pro: {digest.total_events} updates for {digest.store_name}"

    text_lines = [f"{digest.store_name}", ""]
    for label, count in digest.counts.items():
        text_lines.append(f"{count} {label}")
    if digest.top_changes:
        text_lines.append("")
        text_lines.append("Top Changes:")
        text_lines.extend(f"- {title}" for title in digest.top_changes)
    if dashboard_url:
        text_lines.append("")
        text_lines.append(f"View Dashboard: {dashboard_url}")
    text_body = "\n".join(text_lines)

    html_rows = "".join(f"<li>{count} {label}</li>" for label, count in digest.counts.items())
    html_top = "".join(f"<li>{title}</li>" for title in digest.top_changes)
    dashboard_link = f'<p><a href="{dashboard_url}">View Dashboard →</a></p>' if dashboard_url else ""
    html_body = f"""
    <h2>{digest.store_name}</h2>
    <ul>{html_rows}</ul>
    {"<h3>Top Changes</h3><ul>" + html_top + "</ul>" if digest.top_changes else ""}
    {dashboard_link}
    """.strip()

    return subject, text_body, html_body
