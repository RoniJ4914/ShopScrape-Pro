# ShopScrape Pro — Backend

## Status: All 5 modules complete — Scraper + Database + Analyzer + Alert Engine + Dashboard API

Built incrementally: `Universal Scraper → Database → Inventory Analyzer → Alert Engine → Dashboard API`
(Dashboard *frontend* was dropped from this repo — being built separately
on Base44. This module is the REST API it consumes.)

### Module 5 — Dashboard API (`app/api/`)

The read-only REST layer Base44 talks to. Runs as its own process against
the same database the scraper/analyzer/alert engine write to — it never
writes anything itself.

- **`main.py`** — FastAPI app. Wires all routers, adds CORS (open by
  default via `DASHBOARD_CORS_ORIGINS=*`; set it to a comma-separated
  origin list once Base44's final domain is known — access control is
  the API key either way, not CORS), a slow-request logger, a catch-all
  exception handler that never leaks internals to the frontend, and a
  `GET /health` check. Calls `init_db()` on startup (skippable via
  `SKIP_INIT_DB=1` for deployments that manage schema via migrations).
- **`deps.py`** — three shared dependencies: `get_db` (per-request
  session, close-only since this process never commits), `pagination`
  (`limit`/`offset` query params, capped at 200), and `require_api_key`
  (checks `X-API-Key` against `DASHBOARD_API_KEY` — a no-op if that env
  var isn't set, so local dev needs no setup).
- **`schemas.py`** — Pydantic response models for every entity, plus a
  generic `Paginated[T]` wrapper (`{items, total, limit, offset}`) used
  by every list endpoint.
- **`routers/`** — one file per resource, all under `dependencies=[Depends(require_api_key)]`:
  - `stores.py` — `GET /stores` (list), `GET /stores/{id}` (detail +
    live product/variant counts + most recent scrape run)
  - `products.py` — `GET /products` (filters: store, vendor, type, tag,
    free-text `q` across title/vendor/handle/SKU, `in_stock`, price
    range, sort), `GET /products/{id}` (includes variants)
  - `variants.py` — `GET /variants`, `GET /variants/{id}`
  - `events.py` — `GET /events` (filters: store, event type(s),
    severity(ies), vendor, product/variant/sku, run, date range)
  - `price_history.py` — `GET /price-history`, scoped by variant,
    product, or store (requires at least one — an unscoped query over
    this table isn't safe at "millions of products" scale)
  - `scrape_runs.py` — `GET /scrape-runs`, `GET /scrape-runs/{id}`
  - `alerts.py` — `GET /alerts`: the `alert_dispatches` send log (which
    rule fired, when, how many events). Alert *rules* — which store
    routes to which channel under which filters — live in application
    config as `AlertRule` objects (`app/alerts/preferences.py`), not the
    DB, so there's nothing to list/edit here yet.
  - `analytics.py` — `GET /analytics`: store/product/variant totals
    (always current-state) plus a windowed breakdown (`window_days`,
    default 30) of events by type/severity, top vendors by product
    count, average price-change %, most-active products, and scrape
    run success rate.
  - `search.py` — `GET /search?q=...`: matches store name/url, product
    title/vendor/handle/tags, and variant SKU in one call, each entity
    type capped independently so one type can't crowd out the others.

#### Write endpoints (added for the Base44 frontend)

The API is still mostly a read-only layer, but a dashboard needs a few
actions it can originate itself rather than always waiting on the
scheduler:

- `POST /stores` — register a store for tracking (or update
  name/url/platform if the `id` already exists — idempotent, via the
  existing `repository.upsert_store`).
- `POST /stores/{store_id}/activate` / `POST /stores/{store_id}/deactivate`
  — pause/resume tracking for a store. History (products, variants,
  events, price history) is left untouched either way.
- `POST /stores/{store_id}/scrape` — kick off an on-demand scrape ("Scrape
  now" button on a store's detail page) instead of waiting for the
  scheduler. Returns `202` immediately with the new `scrape_runs` row in
  `running` state; the actual scrape → analyze → record chain runs as a
  FastAPI `BackgroundTask`. Poll `GET /scrape-runs/{id}` for the outcome.
  Calls `app.scraper.pipeline.scrape_store(store_id, url)` (see "Module 1
  fix" below for what that now actually does).
- `POST /alerts/test-send` — send one synthetic event through a given
  channel/destination (`discord` | `slack` | `email` | `webhook`) so the
  dashboard can verify a webhook URL or email address is configured
  correctly before wiring it into a real `AlertRule`. Deliberately
  bypasses the rate limiter and never writes to `alert_dispatches` — this
  isn't a real dispatch.

All write endpoints sit behind the same `require_api_key` dependency as
everything else, and CORS now allows `POST` (previously `GET`-only).

- **`app/db/repository.py`** — extended (not replaced) with the read
  queries backing every router above: `get_store`, `get_store_stats`,
  `list_products`/`get_product`, `list_variants`/`get_variant`,
  `list_events`, `list_price_history`, `list_scrape_runs`/`get_scrape_run`,
  `list_alert_dispatches`, `get_analytics_overview`, `search_all`. Same
  rule as before: this file is the only place that builds a query
  against these tables. Every `list_*` function returns
  `(items, total_count)` so routers can build pagination responses
  without a second round-trip. Filtering is portable across SQLite/
  Postgres — e.g. tag/JSON matching goes through `cast(..., String)`
  rather than a Postgres-only JSONB operator, and `.ilike()` degrades to
  a case-insensitive `LIKE` on SQLite automatically.

### Verified

`py_compile` passes on every new/changed file, plus a manual AST pass to
confirm no unused/dangling imports. Same sandbox limitation as every
prior module — no network egress here, so `fastapi`/`sqlalchemy` aren't
installable and I couldn't boot a live server or hit a real endpoint.
I hand-traced each query (join direction, correlated `EXISTS` subqueries
for the product-level price/stock/SKU filters, forward-ref resolution on
`StoreDetailOut.last_run`, `Decimal`→`float` coercion on the Postgres
`AVG()` result in `/analytics`) for correctness. Run
`pip install -r requirements.txt` in an environment with network access,
then:

```bash
uvicorn app.api.main:app --reload
curl http://localhost:8000/health
curl http://localhost:8000/stores
```

### Module 4 — Alert Engine (`app/alerts/`)

- **`preferences.py`** — `AlertRule`: one routing rule per (store filter,
  channel, destination) combining every filter dimension from the spec —
  minimum severity, event-type allowlist, vendor allowlist, absolute price
  threshold, percentage threshold, store filter. `matches()`/`filter_events()`
  are pure functions; filters combine with AND semantics (e.g.
  `percentage_threshold` alone only constrains price/inventory events and
  leaves other event types unaffected — pair it with `event_types` to get
  "only large price moves").
- **`rate_limiter.py`** — DB-backed (new `alert_dispatches` table in
  `app/db/models.py`) rather than in-memory, so limits survive restarts —
  the exact moment a store might be mid-burst.
- **`grouping.py`** — `build_digest()` collapses a run's filtered events
  into counts-by-type plus a prioritized "Top Changes" list (sold-out and
  price-drops surfaced first), independent of any channel's rendering.
- **`formatters/`** — one per channel: `discord.py` (matches the spec's
  exact digest example), `slack.py` (Block Kit mrkdwn), `email.py`
  (subject + text + HTML), `webhook.py` (raw structured JSON for custom
  consumers, since a webhook wants data, not a pre-rendered message).
- **`senders/`** — `discord_sender.py`/`slack_sender.py`/`webhook_sender.py`
  (httpx POST) and `email_sender.py` (stdlib `smtplib`, configured via
  `SMTP_*` env vars, run off-thread so it doesn't block the event loop).
  All senders swallow and log failures rather than raising — one broken
  webhook must never take down a whole alert run.
- **`engine.py`** — `dispatch_alerts()`: the single entry point. Runs every
  configured rule independently per store (Discord gets everything ≥
  warning, email gets only critical price drops on one vendor, a custom
  webhook gets everything — same event list, three different outcomes).

### Verified with a smoke test

Severity filtering, vendor filtering, store-id filtering, and percentage-
threshold + event-type combination all produced exactly the expected
subsets. The digest builder correctly prioritized "Top Changes" (sold-out
surfaced before price moves). All four formatters produced valid,
correctly-structured output — the Discord message reproduces the spec's
exact layout (counts, Top Changes, dashboard link). Sender network calls
weren't exercised (same sandbox limitation as prior modules), but the
formatting/filtering/grouping logic — the actual business logic — is fully
tested.

### Module 3 — Inventory Analyzer (`app/analyzer/`)

- **`events.py`** — `AnalyzerEvent` dataclass mirroring the spec's Event
  Model exactly (Store Name via caller, Product ID, Variant ID, Product
  Title, Vendor, Product Type, SKU, Old/New Value, Old/New Number,
  Severity, Message), with `.to_dict()` producing exactly the shape
  `repository.record_events()` expects.
- **`severity.py`** — percentage-based severity thresholds for price and
  inventory changes (≥20%/≥50% price, ≥30%/≥75% inventory → warning/critical).
- **`diff.py`** — the pure diffing core: `diff_products(old, new)` takes
  two plain lists of `Product` dataclasses and returns events for every
  type in the spec (new/removed product, new/removed variant, price
  increase/decrease, restocked/sold out, inventory increase/decrease,
  compare-at price added/removed/changed). No DB, no I/O — fully unit
  testable with fixtures, and reusable regardless of which DB backend
  supplied the "previous" state. Skips untouched products via fingerprint
  comparison before any per-field diffing, per the spec's performance
  requirement.
- **`aggregates.py`** — `detect_bulk_and_spikes()` looks at a run's full
  event list for store-wide patterns: bulk price changes, inventory
  spikes/drops, with thresholds scaling to store size (fixed count OR
  percentage of catalog, whichever is more permissive).
- **`trending.py`** — the one part that needs history: queries
  `InventoryEvent` for products with unusually many changes in a lookback
  window (default 7 days / 5+ events).
- **`pipeline.py`** — `analyze_and_record()` is the single entry point:
  diff → aggregates → trending → `repository.record_events()`, returning
  the event dicts so the next module (Alert Engine) can act on them
  immediately without a second DB read.

### Verified with a smoke test

Ran a full scenario exercising every event type — price increase (with
correct CRITICAL severity at a 50% jump), inventory decrease, product
removed/added, compare-at price added, sold-out, restocked, new/removed
variant, and the bulk-price-change aggregate. Also confirmed the
fingerprint-skip optimization: an unchanged product produced **zero**
events. `trending.py` needs a live DB (same sandbox network limitation as
before — stubbed just enough to prove the import graph is sound).

### Module 1 fix — `app/scraper/pipeline.py` (`scrape_store`)

This file previously contained an accidental duplicate of
`app/analyzer/pipeline.py` instead of the scraper's own entry point. It now
has the real thing:

- **`scrape_store(store_id, url) -> (List[Product], method_used)`** —
  fetch (`app.scraper.browser.renderer.fetch`) → `detect_platform` +
  `detect_available_methods` → `choose_method` → build the matching
  extractor and call `.extract()`. If the chosen method raises, walks the
  *other* methods detection found, in spec priority order (GraphQL > REST
  > Embedded JSON > JSON-LD > Microdata > HTML), before giving up — HTML
  is always in the detected set, so this only raises if literally every
  method fails. Returns whichever method actually produced results, not
  necessarily the first choice.
- GraphQL needs an endpoint URL detection doesn't hand over directly, so
  `_discover_graphql_endpoint()` resolves it: prefer a GraphQL URL actually
  seen in captured network traffic, then one referenced literally in the
  page's HTML/JS, then fall back to the `/graphql` convention.
- Also added `app/scraper/extractors/base.py` (`BaseExtractor`) — every
  extractor already did `from .base import BaseExtractor` and used
  `self.store_id`, `self.base_url`, `self.method_name`, and
  `self._tag_provenance(products)`, but the base class itself was missing
  from this zip. Reconstructed to match exactly what every extractor
  already assumes.

**Verified:** `detect_platform`/`detect_available_methods`/`choose_method`
and the resulting fallback-order construction were exercised directly
against a synthetic Shopify+JSON-LD page (confirms the priority ordering
and that HTML is always the last entry). `_discover_graphql_endpoint`
was unit-tested against all three resolution paths (network hit, HTML
hit, default fallback). `BaseExtractor._tag_provenance` was tested against
a fake extractor subclass. Same sandbox limitation as every other module —
no network egress, so `httpx`/`beautifulsoup4`/`fastapi`/`sqlalchemy`
aren't installable here and a live end-to-end scrape against a real
storefront hasn't been run. All files pass `py_compile` plus an AST pass
confirming no unused/dangling imports.

### Module 2 — Database (`app/db/`)

- **`base.py`** — engine/session setup. Defaults to a local SQLite file
  with zero config; set `DATABASE_URL` to a `postgresql+psycopg://...` URL
  to switch to Postgres with no code changes anywhere else. SQLite gets
  `check_same_thread=False` for the scheduler's thread pool; Postgres gets
  real connection pooling (`pool_size`/`max_overflow`).
- **`models.py`** — all 7 spec tables: `Store`, `ProductRow`, `VariantRow`,
  `PriceHistory`, `InventoryEvent`, `ScrapeRun`, `SnapshotRow`. Products/
  variants store *current* state (soft-deleted via `is_active` when they
  disappear from a store, not hard-deleted, so events/price history keep
  their foreign keys intact). Full point-in-time history lives in
  `PriceHistory` (one row per actual price change, not per scrape) and
  `InventoryEvent`, rather than duplicating entire product rows every run
  — the only way this scales to "millions of products."
- **`repository.py`** — the only place that touches `Session` objects.
  Key functions: `upsert_snapshot()` (writes a fresh scrape into current
  state, records price history on real changes, soft-deletes vanished
  products), `get_current_products()` (reads current DB state back out as
  plain `Product`/`Variant` dataclasses — this is what the next module,
  the Inventory Analyzer, will diff a new scrape against), plus scrape-run
  lifecycle (`start_scrape_run`/`finish_scrape_run`) and `record_events()`
  for the analyzer to call once it has computed events. Now also holds
  the Dashboard API's read queries (Module 5, above).
- Fingerprint columns cache `Product.fingerprint()`/`Variant.fingerprint()`
  so future incremental runs can skip untouched products with an indexed
  string comparison instead of re-diffing every field — ties directly into
  the spec's "avoid reprocessing unchanged data" performance requirement.

**Note on testing:** this sandbox has no network egress (confirmed via
both `pip install` and `apt-get`), so I couldn't install SQLAlchemy to run
this against a live SQLite file the way I smoke-tested Module 1. All files
pass `py_compile`, and I hand-traced the upsert/soft-delete/flush ordering
for correctness (SQLAlchemy resolves insert order by FK dependency graph
regardless of `session.add()` order, so the new-product-row-before-variant
pattern here is safe). Run `pip install -r requirements.txt` in an
environment with network access, then:

```python
from app.db import init_db, get_session, repository
init_db()
with get_session() as session:
    repository.upsert_store(session, "store1", "Example Store", "https://example.com")
```

### What's implemented (`app/scraper/`, `app/models/`)

- **`app/models/product.py`** — the normalized `Product` / `Variant` / `Snapshot`
  dataclasses every extractor converts into. Includes `fingerprint()` hashing
  used later by the Inventory Analyzer to cheaply detect changes and skip
  unchanged data.
- **`app/scraper/platform_detection.py`** — no hardcoded Shopify. Detects
  platform (Shopify, WooCommerce, Magento, BigCommerce, Wix, Squarespace,
  Salesforce Commerce Cloud, generic Next.js/Nuxt/React/HTML) via
  independent regex signal sets, *separately* from choosing an extraction
  method. Method priority: GraphQL → REST → Embedded JSON → JSON-LD →
  Microdata → HTML, exactly per spec.
- **`app/scraper/browser/renderer.py`** — fetches static HTML first (cheap),
  escalates to Playwright/Chromium only when JS rendering signals are
  detected, and captures all XHR/fetch/document network requests so the
  pipeline can discover GraphQL/REST APIs a static fetch would never see.
  Playwright failures degrade to static HTML rather than crashing a scrape.
- **`app/scraper/extractors/`** — one extractor per data source
  (`graphql_extractor.py`, `rest_extractor.py`, `embedded_json_extractor.py`,
  `jsonld_extractor.py`, `microdata_extractor.py`, `html_extractor.py`).
  GraphQL and REST extractors handle their own cursor/page-based pagination
  internally. Every extractor's only public contract is "return
  `List[Product]`" — nothing outside `extractors/` ever branches on
  platform or method.
- **`app/scraper/pipeline.py`** — `scrape_store(store_id, url)` is the single
  public entry point. It runs detect → extract, and if the chosen method
  fails at runtime, walks the remaining detected methods in priority order
  before giving up (defensive fallback chain, not just single-shot).

### Verified with a smoke test

Platform detection, JSON-LD, embedded JSON (`__NEXT_DATA__`), microdata, and
HTML-fallback extraction were all run against representative sample pages —
all normalize correctly into `Product`/`Variant`, and fingerprinting
correctly changes when price/availability changes. (GraphQL/REST extractors
depend on `httpx` for real network calls, which isn't installable in this
sandboxed environment — no network egress here — but they compile cleanly
and follow the same tested normalization pattern as the others.)

### To run for real, in your own environment

```bash
pip install -r requirements.txt
playwright install chromium
uvicorn app.api.main:app --reload
```

### All modules complete

The backend is done: scraper → database → analyzer → alert engine →
dashboard API, all reading/writing the same DB. Base44 can now point its
frontend at this API (`/stores`, `/products`, `/variants`, `/events`,
`/price-history`, `/analytics`, `/alerts`, `/scrape-runs`, `/search`) and
wire up a scheduler (APScheduler is already in `requirements.txt`) to
call `scrape_store` → `analyze_and_record` → `dispatch_alerts` on a
recurring interval per store.
