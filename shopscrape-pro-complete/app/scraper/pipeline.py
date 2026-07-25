"""
Inventory Analyzer pipeline.

    previous snapshot (DB)  ---\\
                                 >--  diff_products  --> per-product events
    current snapshot (scrape) --/
                                        |
                                        v
                              detect_bulk_and_spikes  --> run-level summary events
                                        |
                                        v
                                detect_trending (DB history) --> trending events
                                        |
                                        v
                              repository.record_events (persist)

`analyze_and_record()` is the single public entry point the scheduler
calls after a scrape completes. It returns the full list of event dicts
(already persisted) so the Alert Engine can act on them immediately
without a second DB round-trip.
"""

from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.models.product import Product
from app.db import repository
from .diff import diff_products
from .aggregates import detect_bulk_and_spikes
from .trending import detect_trending
from .events import AnalyzerEvent


def analyze_and_record(
    session: Session,
    store_id: str,
    run_id: int,
    previous_products: List[Product],
    new_products: List[Product],
    enable_trending: bool = True,
) -> List[dict]:
    """
    Run the full analysis for one scrape and persist the resulting events.
    Returns the events as plain dicts (same shape written to the DB) for
    the Alert Engine to consume in the same pipeline pass.
    """
    events: List[AnalyzerEvent] = diff_products(previous_products, new_products)

    events.extend(detect_bulk_and_spikes(events, total_product_count=len(new_products)))

    if enable_trending:
        events.extend(detect_trending(session, store_id))

    event_dicts = [e.to_dict() for e in events]
    repository.record_events(session, store_id, run_id, event_dicts)

    return event_dicts
