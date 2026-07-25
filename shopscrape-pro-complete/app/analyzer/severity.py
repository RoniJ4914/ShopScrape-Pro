"""
Severity scoring.

Kept as plain functions with tunable thresholds (rather than hardcoded
inline in the diff logic) so alert preferences -- "Minimum severity" from
the spec's Alert Engine -- can filter on something consistent, and so a
store owner's threshold can eventually be made configurable per store
without touching the diffing code itself.
"""

from __future__ import annotations

from .events import Severity


def price_change_severity(old_price: float, new_price: float) -> Severity:
    if old_price in (None, 0):
        return Severity.INFO
    pct_change = abs(new_price - old_price) / old_price * 100
    if pct_change >= 50:
        return Severity.CRITICAL
    if pct_change >= 20:
        return Severity.WARNING
    return Severity.INFO


def inventory_change_severity(old_qty: float, new_qty: float) -> Severity:
    if old_qty in (None, 0):
        return Severity.INFO
    pct_change = abs(new_qty - old_qty) / old_qty * 100
    if pct_change >= 75:
        return Severity.CRITICAL
    if pct_change >= 30:
        return Severity.WARNING
    return Severity.INFO


def pct_change(old: float, new: float) -> float:
    if not old:
        return 0.0
    return (new - old) / old * 100
