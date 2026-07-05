"""USASpending.gov federal contract awards source module.
Spec: the source-collection validation notes.

USASpending.gov is Treasury's official system of record for federal spending — U.S.
government public domain, no key required. spending_by_award is a POST endpoint (the query
body is too complex for GET query-string encoding), paginated.

Endpoint: https://api.usaspending.gov/api/v2/search/spending_by_award/
Confirmed live 2026-07-03: a POST for award_type_codes A/B/C/D (all contract types) over a
recent date window returned real DoD (Department of Defense) delivery-order awards — see
tests/fixtures/usaspending_awards_sample.json. `NAICS Code`/`NAICS Description` came back
null for these rows even when requested; treated as optional/best-effort fields here, not a
parsing bug — some award records simply don't carry a NAICS classification in this endpoint's
response, only in the more detailed award-endpoint.

Cadence: this is a genuine daily stream (unlike CFTC/Treasury/openFDA) — federal contract
obligations post continuously, easily thousands/day across all agencies. A daily poll is the
right cadence.

State/cursor design: state stores `last_end_date` (the end_date used on the previous run's
time_period). Each run queries `time_period` from last_end_date (inclusive) through today.
Because a single day can have thousands of awards, and this endpoint's `page_metadata.hasNext`
paginates only within one query, we advance the cursor ONLY after fully paginating the current
window (i.e., after hasNext goes False) so that we never silently skip records — we would
rather occasionally re-fetch and dedupe (via generated_internal_id) than lose an award to a
premature cursor advance. Per-run record count is capped at PER_RUN_CAP as a safety valve
against a single run pulling an unbounded backlog after a long outage; if the window's records
exceed the cap, the cursor does NOT advance and the same window is retried (further deduped)
next run until fully drained.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

SOURCE_ID = "usaspending-awards"
LABEL = "USASpending.gov federal contract awards (U.S. government public domain)"

BASE = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AWARD_TYPE_CODES = ["A", "B", "C", "D"]  # contract types (BPA call, purchase order, delivery/definitive contract)
PAGE_LIMIT = 100
PER_RUN_CAP = 2000
SEEN_CAP = 20000
DEFAULT_LOOKBACK_DAYS = 3  # first-ever run: don't try to pull the entire 2007+ history
# Server-side floor, mirrors the signal mapper's threshold (producer/signals.py _AWARD_MIN_USD).
# Keep in sync: below this an award can't produce a signal, so there's no reason to fetch it.
AWARD_SIGNAL_FLOOR_USD = 10_000_000

FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Award Amount",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Contract Award Type",
    "Description",
    "Period of Performance Start Date",
    "Period of Performance Current End Date",
    "NAICS Code",
    "NAICS Description",
]


def client() -> httpx.Client:
    # USASpending's spending_by_award search is a known-slow endpoint (observed live
    # 2026-07-03: a 30s timeout was too tight and the very first page timed out) — 60s.
    return httpx.Client(timeout=60.0)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _post_with_retry(c: httpx.Client, body: dict, attempts: int = 3):
    """spending_by_award is reliably slow — observed live 2026-07-04 taking >40s and returning
    transient 502s before succeeding. Retry timeouts and 5xx with backoff so a slow-but-healthy
    endpoint doesn't cost the whole cycle (a hard failure still surfaces to run_source, which
    isolates it and retries next tick)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = c.post(BASE, json=body)
            if r.status_code >= 500:
                last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                time.sleep(2 * (i + 1))
                continue
            return r
        except httpx.TimeoutException as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last  # exhausted retries — let fetch_new's handler log + re-raise for run_source


def _normalize(row: dict, fetched_at: str) -> dict:
    return {
        "award_id": row.get("Award ID"),
        "generated_internal_id": row.get("generated_internal_id"),
        "recipient_name": row.get("Recipient Name"),
        "recipient_uei": row.get("Recipient UEI"),
        "award_amount": row.get("Award Amount"),
        "awarding_agency": row.get("Awarding Agency"),
        "awarding_sub_agency": row.get("Awarding Sub Agency"),
        "contract_award_type": row.get("Contract Award Type"),
        "description": row.get("Description"),
        "period_of_performance_start": row.get("Period of Performance Start Date"),
        "period_of_performance_end": row.get("Period of Performance Current End Date"),
        "naics_code": row.get("NAICS Code"),
        "naics_description": row.get("NAICS Description"),
        "fetched_at": fetched_at,
        "source_url": BASE,
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: paginate spending_by_award over [last_end_date, today], dedupe by
    generated_internal_id (falls back to Award ID if that's ever absent), cap per-run volume."""
    today = _today()
    start_date = state.get("last_end_date") or (
        (datetime.now(timezone.utc).date() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    )
    now = datetime.now(timezone.utc).isoformat()
    seen = set(state.get("seen_ids", []))

    body_base = {
        "filters": {
            "award_type_codes": AWARD_TYPE_CODES,
            "time_period": [{"start_date": start_date, "end_date": today}],
            # Only awards large enough to be a market signal (docs/13: mapper's $10M floor). The
            # API filters server-side, so we stop paging a window of routine $100K procurement
            # (Xerox/Grainger office supplies) that never clears the signal bar — the bug that
            # made this source emit 0 signals despite 1,000 records. lower_bound in whole dollars.
            "award_amounts": [{"lower_bound": AWARD_SIGNAL_FLOOR_USD}],
        },
        "fields": FIELDS,
        # Biggest first: a contract win's signal strength scales with size, so the highest-value
        # awards are the ones a buyer most wants surfaced, and paging can stop early on volume.
        "sort": "Award Amount",
        "order": "desc",
        "limit": PAGE_LIMIT,
    }

    new_records: list[dict] = []
    new_seen = set(seen)
    page = 1
    fully_paginated = False

    try:
        while True:
            body = dict(body_base, page=page)
            r = _post_with_retry(c, body)
            r.raise_for_status()
            payload = r.json()

            for row in payload.get("results", []):
                key = row.get("generated_internal_id") or row.get("Award ID")
                if not key or key in seen:
                    continue
                new_seen.add(key)
                new_records.append(_normalize(row, now))

            has_next = bool(payload.get("page_metadata", {}).get("hasNext"))
            if not has_next:
                fully_paginated = True
                break
            if len(new_records) >= PER_RUN_CAP:
                break  # safety valve — cursor will NOT advance, so this window is retried
            page += 1
    except Exception as e:  # noqa: BLE001
        print(f"[producer:{SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        raise

    if fully_paginated:
        state["last_end_date"] = today
    # else: leave last_end_date as-is so next run retries the same window from the top,
    # deduped via seen_ids (we may re-request pages we've already consumed, but we never lose
    # an award to a cursor that advanced before the window was fully drained).

    state["seen_ids"] = list(new_seen)[-SEEN_CAP:]
    return new_records, state
