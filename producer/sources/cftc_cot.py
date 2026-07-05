"""CFTC Commitment of Traders (disaggregated, futures-only) source module.
Spec: the source-collection validation notes.

The CFTC (Commodity Futures Trading Commission) publishes a weekly snapshot of open
interest broken down by trader category for every futures market it regulates — U.S.
government public domain data, served over Socrata's SODA (Socrata Open Data API), no
key required.

Endpoint: https://publicreporting.cftc.gov/resource/72hh-3qpy.json
  (dataset 72hh-3qpy = "Disaggregated Futures-Only Report")

Confirmed live 2026-07-03: `?$limit=5&$order=report_date_as_yyyy_mm_dd DESC` returned real
rows (WHEAT-SRW, ICE Brent/fuel-oil futures, etc.) for report week 2026-06-23 — see
tests/fixtures/cftc_cot_sample.json. Cadence is weekly: the CFTC publishes this report every
Friday for the prior Tuesday's positions, so polling daily is harmless but wasteful — a
weekly poll is the intended cadence.

Field mapping (raw Socrata column -> normalized field), values documented at
https://www.cftc.gov/MarketReports/CommitmentsofTraders/ExplanatoryNotes :
  - prod_merc_positions_long/short           -> commercial_long / commercial_short
      ("producer/merchant/processor/user" = commercial hedgers)
  - swap_positions_long_all / swap__positions_short_all -> swap_dealer_long / swap_dealer_short
  - m_money_positions_long_all / _short_all  -> managed_money_long / managed_money_short
      (hedge funds / CTAs — the "speculators" category most watched for sentiment)
  - other_rept_positions_long/short          -> other_reportable_long / other_reportable_short
  - nonrept_positions_long_all/short_all     -> nonreportable_long / nonreportable_short
      (small traders below CFTC reporting thresholds, derived not directly reported)
  - open_interest_all                        -> open_interest
  - change_in_open_interest_all               -> open_interest_change

Rows below Socrata's "_old"/"_other" splits (legacy futures-only vs futures-and-options
breakdowns) are NOT surfaced here — this module sticks to the "_all" (combined futures +
options) columns, which is what most downstream consumers actually want.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import httpx

SOURCE_ID = "cftc-cot"
LABEL = "CFTC Commitment of Traders (U.S. government public domain)"

BASE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
DEFAULT_LIMIT = 5000  # weekly batch spans every commodity/exchange in one report date


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _num(v):
    """Socrata returns numeric columns as strings; coerce to int, tolerating blanks/None."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


def _normalize(row: dict, fetched_at: str) -> dict:
    return {
        "report_date": (row.get("report_date_as_yyyy_mm_dd") or "")[:10] or None,
        "report_week": row.get("yyyy_report_week_ww"),
        "market_and_exchange_names": row.get("market_and_exchange_names"),
        "contract_market_name": row.get("contract_market_name"),
        "commodity_name": row.get("commodity_name"),
        "exchange_code": row.get("cftc_market_code"),
        "contract_market_code": row.get("cftc_contract_market_code"),
        "open_interest": _num(row.get("open_interest_all")),
        "open_interest_change": _num(row.get("change_in_open_interest_all")),
        "commercial_long": _num(row.get("prod_merc_positions_long")),
        "commercial_short": _num(row.get("prod_merc_positions_short")),
        "swap_dealer_long": _num(row.get("swap_positions_long_all")),
        "swap_dealer_short": _num(row.get("swap__positions_short_all")),
        "managed_money_long": _num(row.get("m_money_positions_long_all")),
        "managed_money_short": _num(row.get("m_money_positions_short_all")),
        "other_reportable_long": _num(row.get("other_rept_positions_long")),
        "other_reportable_short": _num(row.get("other_rept_positions_short")),
        "nonreportable_long": _num(row.get("nonrept_positions_long_all")),
        "nonreportable_short": _num(row.get("nonrept_positions_short_all")),
        "total_reportable_long": _num(row.get("tot_rept_positions_long_all")),
        "total_reportable_short": _num(row.get("tot_rept_positions_short")),
        "fetched_at": fetched_at,
        "source_url": BASE,
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: pull report rows newer than the last-seen report date, across every
    commodity/exchange in that weekly release. Socrata's $where lets us filter server-side."""
    last_date = state.get("last_report_date")
    now = datetime.now(timezone.utc).isoformat()

    params = {
        "$order": "report_date_as_yyyy_mm_dd,contract_market_name",
        "$limit": str(DEFAULT_LIMIT),
    }
    if last_date:
        params["$where"] = f"report_date_as_yyyy_mm_dd > '{last_date}T00:00:00.000'"

    try:
        r = c.get(BASE, params=params)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001 — surfaced to runner.py's per-source try/except
        print(f"[producer:{SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        raise

    new_records = [_normalize(row, now) for row in rows]

    max_date = last_date
    for rec in new_records:
        d = rec["report_date"]
        if d and (max_date is None or d > max_date):
            max_date = d
    if max_date:
        state["last_report_date"] = max_date

    return new_records, state
