"""U.S. Treasury auction results source module. Spec: the source-collection validation notes.

TreasuryDirect's TA_WS (TreasuryDirect Auction Web Service) publishes results for every
Treasury bill/note/bond/TIPS/FRN auction — U.S. government public domain, no key required.

Endpoint: https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=N
Confirmed live 2026-07-03: `days=30` returned 37 real auctions (4-week bills through
long bonds), most-recent auctionDate 2026-07-02 — see
tests/fixtures/treasury_auctions_sample.json. Response is a flat JSON list, newest first.

Cadence: auction-calendar-driven, not continuous. Treasury holds roughly 3-5 auctions most
weekdays across all security types, so a daily poll is the right cadence (weekly would miss
short-bill auctions, since 4-week/8-week/13-week bills auction on tight, overlapping
schedules). `days=N` is a lookback window, not a cursor — we ask for enough days to cover
since the last auction we saw (plus a buffer for weekends/holidays) and then dedupe.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import httpx

SOURCE_ID = "treasury-auctions"
LABEL = "U.S. Treasury auction results (U.S. government public domain)"

BASE = "https://www.treasurydirect.gov/TA_WS/securities/auctioned"
LOOKBACK_BUFFER_DAYS = 5  # cushion for weekends/holidays between polls
MAX_DAYS = 120  # backstop if state is empty/very stale — avoid asking for an unbounded window
DEFAULT_DAYS = 14  # first-ever run, no cursor yet


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_only(v):
    if not v:
        return None
    return v[:10] or None


def _normalize(row: dict, fetched_at: str) -> dict:
    return {
        "cusip": row.get("cusip"),
        "security_type": row.get("securityType"),
        "security_term": row.get("securityTerm"),
        "announcement_date": _date_only(row.get("announcementDate")),
        "auction_date": _date_only(row.get("auctionDate")),
        "issue_date": _date_only(row.get("issueDate")),
        "maturity_date": _date_only(row.get("maturityDate")),
        "high_discount_rate": _num(row.get("highDiscountRate")),
        "high_investment_rate": _num(row.get("highInvestmentRate")),
        "high_yield": _num(row.get("highYield")),
        "high_price": _num(row.get("highPrice")),
        "interest_rate": _num(row.get("interestRate")),
        "average_median_discount_rate": _num(row.get("averageMedianDiscountRate")),
        "average_median_yield": _num(row.get("averageMedianYield")),
        "bid_to_cover_ratio": _num(row.get("bidToCoverRatio")),
        "total_accepted": _num(row.get("totalAccepted")),
        "total_tendered": _num(row.get("totalTendered")),
        "competitive_accepted": _num(row.get("competitiveAccepted")),
        "competitive_tendered": _num(row.get("competitiveTendered")),
        "currently_outstanding": _num(row.get("currentlyOutstanding")),
        "fetched_at": fetched_at,
        "source_url": BASE,
    }


def _days_since(last_date: str) -> int:
    try:
        last = datetime.strptime(last_date, "%Y-%m-%d").date()
    except ValueError:
        return DEFAULT_DAYS
    delta = (datetime.now(timezone.utc).date() - last).days
    return max(1, min(delta + LOOKBACK_BUFFER_DAYS, MAX_DAYS))


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: pull the last-N-days auction results, dedupe by (cusip, auction_date)."""
    last_date = state.get("last_auction_date")
    seen = set(state.get("seen_keys", []))
    now = datetime.now(timezone.utc).isoformat()

    days = _days_since(last_date) if last_date else DEFAULT_DAYS

    try:
        r = c.get(BASE, params={"format": "json", "days": str(days)})
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[producer:{SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        raise

    new_records: list[dict] = []
    new_seen = set(seen)
    max_date = last_date

    for row in rows:
        cusip = row.get("cusip")
        auction_date = _date_only(row.get("auctionDate"))
        key = f"{cusip}|{auction_date}"
        if not cusip or not auction_date or key in seen:
            continue
        new_seen.add(key)
        new_records.append(_normalize(row, now))
        if max_date is None or auction_date > max_date:
            max_date = auction_date

    if max_date:
        state["last_auction_date"] = max_date
    # Cap the seen-key set so state.json doesn't grow unbounded across years of daily polls.
    state["seen_keys"] = list(new_seen)[-5000:]

    return new_records, state
