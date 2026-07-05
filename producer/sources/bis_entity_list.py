"""BIS Entity List (via the Consolidated Screening List) source module.
Spec: the source-collection validation notes.

The Bureau of Industry and Security (BIS) Entity List names parties subject to specific
export-license requirements — U.S. government public domain. It is served here through
Commerce's Consolidated Screening List API, which merges BIS's own list with several OTHER
federal sanctions/screening lists (Treasury OFAC SDN, State Dept, etc). This module filters
results down to just the BIS Entity List (source == "Entity List") to match the doc 10/11
scoping — this ingredient is specifically the BIS list, not the whole consolidated set.

Requires a free api.data.gov key (env var DATA_GOV_API_KEY):
    1. Sign up at https://api.data.gov/signup/
    2. Free self-serve signup, key arrives immediately (same registry that gates many
       other federal APIs, so this key is reusable elsewhere too).
    3. Set it: DATA_GOV_API_KEY=<your key>

If the key is missing, fetch_new() logs one clear message and returns ([], state) unchanged
rather than raising — same "never take down another source" contract as producer/runner.py.

Endpoint reachability was confirmed live in the prior research pass (source-collection validation pass): hitting
    https://data.trade.gov/consolidated_screening_list/v1/search?q=X&size=N&api_key=K
without a key returns 401 — proving the endpoint is live, just gated.

# TODO(phase4): the filter on `source == "Entity List"` and the field names used below
# (name, addresses, federal_register_notice, effective_date, source) are based on the
# Consolidated Screening List's published API schema
# (https://developer.trade.gov/consolidated-screening-list.html), NOT confirmed against a
# live sample response, because no DATA_GOV_API_KEY was available at build time. In
# particular the exact string value of the `source` field for BIS Entity List entries
# (assumed "Entity List" here) should be double-checked against a real response on first
# live run — if it differs, this filter will silently return zero records rather than
# fail loudly, so a low-record-count run of this source should be treated as a signal to
# check the filter, not just "the list didn't change."
#
# The Entity List itself changes relatively slowly (~1,958 entries as of the source-evaluation pass) and this
# API shape exposes no reliable "date added"/incremental cursor, so fetch_new() does a full
# pull on a periodic cadence (e.g. weekly) rather than trying to diff incrementally. This
# no-incremental-cursor assumption is itself unverified without a live sample — if the API
# does expose a usable date filter, a future revision could poll more efficiently.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

SOURCE_ID = "bis-entity-list"
LABEL = "BIS Entity List via Consolidated Screening List (U.S. government public domain)"

API_KEY_ENV = "DATA_GOV_API_KEY"
BASE = "https://data.trade.gov/consolidated_screening_list/v1/search"

# BIS's own list within the merged consolidated screening list — see module docstring TODO.
ENTITY_LIST_SOURCE = "Entity List"

PAGE_SIZE = 100
MAX_PAGES = 50  # generous ceiling (~5,000 records) well above the ~1,958-entry list size

# The list is slow-changing and this API exposes no reliable incremental cursor (see
# docstring), so we only do a full re-pull every REFRESH_DAYS.
REFRESH_DAYS = 7

_warned = False


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        print(f"[producer:{SOURCE_ID}] {msg}", file=sys.stderr)
        _warned = True


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _due_for_refresh(state: dict, now: datetime) -> bool:
    last = state.get("last_full_pull")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now - last_dt >= timedelta(days=REFRESH_DAYS)


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """Full periodic pull of the BIS Entity List (filtered out of the consolidated screening
    list), gated to once every REFRESH_DAYS since there's no reliable incremental cursor."""
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        _warn_once(
            f"{API_KEY_ENV} not set — skipping BIS Entity List fetch. Get a free key at "
            f"https://api.data.gov/signup/"
        )
        return [], state

    now = datetime.now(timezone.utc)
    if not _due_for_refresh(state, now):
        return [], state

    fetched_at = now.isoformat()
    records: list[dict] = []
    offset = 0

    for _ in range(MAX_PAGES):
        params = {
            "api_key": api_key,
            "sources": ENTITY_LIST_SOURCE,
            "size": PAGE_SIZE,
            "offset": offset,
        }
        try:
            r = c.get(BASE, params=params)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # noqa: BLE001 — one bad page must not stop the whole pull
            print(f"[producer:{SOURCE_ID}] page fetch failed at offset {offset}: {e}", file=sys.stderr)
            break

        results = payload.get("results", [])
        if not results:
            break

        for row in results:
            if row.get("source") != ENTITY_LIST_SOURCE:
                continue  # belt-and-suspenders; `sources` query param should already scope this
            addresses = row.get("addresses") or []
            addr = addresses[0] if addresses else {}
            records.append({
                "name": row.get("name"),
                "address": addr.get("address"),
                "country": addr.get("country"),
                "federal_register_notice": row.get("federal_register_notice"),
                "effective_date": row.get("start_date") or row.get("effective_date"),
                "source_list": row.get("source"),
                "fetched_at": fetched_at,
                "source_url": BASE,
            })

        if len(results) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    state["last_full_pull"] = fetched_at
    return records, state
