"""FRED (Federal Reserve Economic Data) source module. Spec: the source-collection validation notes.

FRED is the St. Louis Fed's macro time-series database — U.S. government public domain,
same provenance story as EDGAR (docs/03).

Requires a free API key (env var FRED_API_KEY):
    1. Sign up at https://fred.stlouisfed.org/docs/api/api_key.html
    2. Signup is instant self-serve, no approval wait, no cost.
    3. Set it: FRED_API_KEY=<your key>

If the key is missing, fetch_new() logs one clear message and returns ([], state) unchanged
rather than raising — same "never take down another source" contract as producer/runner.py.

# TODO(phase4): this module was built against FRED's documented, decade-stable JSON schema
# (https://fred.stlouisfed.org/docs/api/fred/series_observations.html) — NOT validated against
# a live 200 response, because no FRED_API_KEY was available at build time. The observations
# endpoint's shape (top-level "observations" list of {date, value, realtime_start/end} dicts)
# has been stable for years, so this is a reasonable case to build against docs without a live
# hit, but it is unverified against a real key/response. Re-check on first live run.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone

import httpx

SOURCE_ID = "fred-series"
LABEL = "FRED — Federal Reserve Economic Data (U.S. government public domain)"

API_KEY_ENV = "FRED_API_KEY"
BASE = "https://api.stlouisfed.org/fred/series/observations"

# A handful of the most market-moving headline macro series — not FRED's full 845k+ catalog.
SERIES = {
    "CPIAUCSL": "Consumer Price Index for All Urban Consumers: All Items",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Federal Funds Effective Rate",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "PAYEMS": "All Employees, Total Nonfarm (Nonfarm Payrolls)",
}

_warned = False


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        print(f"[producer:{SOURCE_ID}] {msg}", file=sys.stderr)
        _warned = True


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: for each curated series, pull observations newer than the last-seen
    date (via FRED's observation_start param), normalize, dedupe by (series_id, date)."""
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        _warn_once(
            f"{API_KEY_ENV} not set — skipping FRED fetch. Get a free key (instant, "
            f"no approval wait) at https://fred.stlouisfed.org/docs/api/api_key.html"
        )
        return [], state

    last_dates: dict = state.get("last_date", {})
    now = datetime.now(timezone.utc).isoformat()
    new_records: list[dict] = []
    new_last_dates = dict(last_dates)

    for series_id, series_name in SERIES.items():
        start = last_dates.get(series_id)
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start

        try:
            r = c.get(BASE, params=params)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # noqa: BLE001 — one bad series must not stop the others
            print(f"[producer:{SOURCE_ID}] skip {series_id}: {e}", file=sys.stderr)
            continue

        obs = payload.get("observations", [])
        max_date = start
        for o in obs:
            d = o.get("date")
            v = o.get("value")
            if d is None:
                continue
            # Skip the boundary observation itself if we've already recorded it (observation_start
            # is inclusive), and skip FRED's "." missing-value sentinel.
            if start and d <= start:
                continue
            if v == ".":
                v = None
            new_records.append({
                "series_id": series_id,
                "series_name": series_name,
                "date": d,
                "value": v,
                "fetched_at": now,
                "source_url": f"{BASE}?series_id={series_id}",
            })
            if max_date is None or d > max_date:
                max_date = d

        if max_date:
            new_last_dates[series_id] = max_date

    state["last_date"] = new_last_dates
    return new_records, state
