"""U.S. Census Bureau International Trade source module. Spec: the source-collection validation notes.

Monthly U.S. export values from the Census Bureau's international trade timeseries API —
U.S. government public domain, same provenance story as EDGAR (docs/03).

Requires a free API key (env var CENSUS_API_KEY):
    1. Sign up at https://api.census.gov/data/key_signup.html
    2. Free self-serve signup, key arrives by email.
    3. Set it: CENSUS_API_KEY=<your key>

If the key is missing, fetch_new() logs one clear message and returns ([], state) unchanged
rather than raising — same "never take down another source" contract as producer/runner.py.

Endpoint reachability was confirmed live in the prior research pass (source-collection validation pass): hitting
    https://api.census.gov/data/timeseries/intltrade/exports/hs?get=ALL_VAL_MO&time=X&COMM_LVL=HS6&key=K
WITHOUT a key returns an explicit "Missing Key" HTML error — proving the endpoint routing
itself is live and correct. This module scopes to total monthly export value at the
ALL commodity aggregate level (COMM_LVL=HS0, CTY_CODE=- / "all countries" total), NOT the
full HS6-commodity-by-country cross product (that's a combinatorially enormous pull —
hundreds of thousands of rows per month). A finer-grained slice can be added later if a buyer
wants commodity- or country-level detail; this keeps the first cut small and reliable.

# TODO(phase4): the row-shape parsing here (first response row = header/column-name array,
# subsequent rows = parallel value arrays) follows the Census Bureau's documented, standard
# convention for ALL of its data.census.gov / api.census.gov timeseries+ACS endpoints
# (https://www.census.gov/data/developers/guidance/api-user-guide.html) — this convention is
# used consistently across the whole Census API family, not something inferred for this one
# endpoint. It was NOT validated against a live 200 response for intltrade specifically,
# because no CENSUS_API_KEY was available at build time. Re-check on first live run.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import httpx

SOURCE_ID = "census-intl-trade"
LABEL = "U.S. Census Bureau International Trade (U.S. government public domain)"

API_KEY_ENV = "CENSUS_API_KEY"
BASE = "https://api.census.gov/data/timeseries/intltrade/exports/hs"

# Total monthly export value, all commodities aggregated (HS0 = broadest commodity level).
COMM_LVL = "HS0"

_warned = False


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        print(f"[producer:{SOURCE_ID}] {msg}", file=sys.stderr)
        _warned = True


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _next_period(period: str) -> str:
    """'2026-05' -> '2026-06' (Census 'time' param is YYYY-MM)."""
    y, m = int(period[:4]), int(period[5:7])
    m += 1
    if m > 12:
        m = 1
        y += 1
    return f"{y:04d}-{m:02d}"


def _months_since(last_period: str | None, today: str) -> list[str]:
    """List of YYYY-MM periods from just after last_period through today's month,
    inclusive. If no cursor yet, just pull the current month."""
    if not last_period:
        return [today]
    out = []
    p = _next_period(last_period)
    while p <= today:
        out.append(p)
        p = _next_period(p)
    return out


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: pull total monthly export value (all commodities aggregate) for any
    months newer than the last-recorded period."""
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        _warn_once(
            f"{API_KEY_ENV} not set — skipping Census trade fetch. Get a free key at "
            f"https://api.census.gov/data/key_signup.html"
        )
        return [], state

    now = datetime.now(timezone.utc)
    today_period = now.strftime("%Y-%m")
    last_period = state.get("last_period")
    periods = _months_since(last_period, today_period)

    new_records: list[dict] = []
    max_period = last_period
    fetched_at = now.isoformat()

    for period in periods:
        params = {
            "get": "ALL_VAL_MO",
            "COMM_LVL": COMM_LVL,
            "time": period,
            "key": api_key,
        }
        try:
            r = c.get(BASE, params=params)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:  # noqa: BLE001 — one bad period must not stop the others
            print(f"[producer:{SOURCE_ID}] skip {period}: {e}", file=sys.stderr)
            continue

        if not rows or len(rows) < 2:
            continue  # header only / empty — period not published yet

        header = rows[0]
        for row in rows[1:]:
            rec = dict(zip(header, row))
            new_records.append({
                "period": rec.get("time", period),
                "commodity_level": COMM_LVL,
                "value_usd": rec.get("ALL_VAL_MO"),
                "fetched_at": fetched_at,
                "source_url": f"{BASE}?get=ALL_VAL_MO&COMM_LVL={COMM_LVL}&time={period}",
            })
        if max_period is None or period > max_period:
            max_period = period

    if max_period:
        state["last_period"] = max_period
    return new_records, state
