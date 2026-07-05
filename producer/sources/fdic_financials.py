"""FDIC BankFind bank financial data access layer. Spec: the multi-source pipeline validation notes.

Confirmed live 2026-07-03 against
https://api.fdic.gov/banks/financials?filters=REPDTE:20260331&fields=CERT,NAME,ASSET,DEP,EQ,NETINC&limit=N&format=json
which returned real bank call-report records (4,352 banks) for report date 2026-03-31.
FDIC (Federal Deposit Insurance Corporation) BankFind data is U.S. government public domain.

Response envelope (verified live, not assumed):

    {
      "meta": {"total": 4352, "parameters": {...}, "index": {...}},
      "data": [
        {"data": {"CERT": 10004, "NAME": "ERGO BANK", "ASSET": 263059, "DEP": 221684,
                  "EQ": 25651, "NETINC": 239, "REPDTE": "20260331", "ID": "10004_20260331"},
         "score": 0},
        ...
      ],
      "totals": {"count": 4352}
    }

Field codes verified live by requesting them and confirming non-null values: CERT (FDIC
certificate number), NAME (bank name -- must be explicitly requested via `fields=`, it is not
returned by default), ASSET (total assets, $000s), DEP (total deposits, $000s), EQ (total
equity capital, $000s), NETINC (net income, $000s), REPDTE (report date, YYYYMMDD string).

Pagination verified live: limit=2&offset=0 returned CERT 10004/10011; limit=2&offset=2 returned
CERT 10012/10015 (no overlap, strictly advancing) -- offset genuinely pages through the result
set in a stable CERT-ascending order.

Report dates (REPDTE) are quarter-end dates (YYYYMMDD: 0331, 0630, 0930, 1231) with real
reporting lag: querying REPDTE=20260630 on 2026-07-03 (three days after quarter-end) returned
meta.total=0 / data=[] -- call reports simply are not filed/processed yet. That is the expected,
correct "nothing new this run" case, not an error.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

SOURCE_ID = "fdic-bank-financials"
LABEL = "FDIC BankFind bank financial data (U.S. government public domain)"

BASE = "https://api.fdic.gov/banks/financials"
_TIMEOUT = 30.0
_PAGE_LIMIT = 1000  # FDIC API's practical page size for bulk pulls

FIELDS = ["CERT", "NAME", "ASSET", "DEP", "EQ", "NETINC", "REPDTE"]


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": "The Junkyard (botfeeder.junkyard.guru) - contact TBD"},
        timeout=_TIMEOUT,
    )


def _quarter_end_dates_desc(today: date, count: int = 6) -> list[str]:
    """Most-recent-first quarter-end dates (YYYYMMDD strings) up to and including the most
    recent quarter-end on/before `today`."""
    quarter_month_days = [(3, 31), (6, 30), (9, 30), (12, 31)]
    out: list[str] = []
    year = today.year
    while len(out) < count:
        for month, day in reversed(quarter_month_days):
            qend = date(year, month, day)
            if qend <= today:
                out.append(qend.strftime("%Y%m%d"))
                if len(out) >= count:
                    break
        year -= 1
    return out


def most_recent_reportable_quarter(today: date) -> str:
    """The most recent quarter-end date that should already have data, given FDIC's reporting
    lag. Banks typically file within ~30-45 days of quarter-end, so we don't even try the
    latest quarter-end until it's at least 45 days old."""
    candidates = _quarter_end_dates_desc(today, count=2)
    newest = date.fromisoformat(
        f"{candidates[0][:4]}-{candidates[0][4:6]}-{candidates[0][6:]}"
    )
    if (today - newest).days < 45:
        return candidates[1]
    return candidates[0]


def _page_url(repdte: str, limit: int, offset: int) -> str:
    fields = ",".join(FIELDS)
    return (f"{BASE}?filters=REPDTE:{repdte}&fields={fields}"
            f"&limit={limit}&offset={offset}&format=json")


def normalize_page(payload: dict, fetched_at: str) -> list[dict]:
    """Pure function: one raw FDIC response page -> normalized bank-financial records. No I/O."""
    out: list[dict] = []
    for row in payload.get("data") or []:
        d = row.get("data") or {}
        out.append({
            "cert": d.get("CERT"),
            "name": d.get("NAME"),
            "assets": d.get("ASSET"),
            "deposits": d.get("DEP"),
            "equity_capital": d.get("EQ"),
            "net_income": d.get("NETINC"),
            "repdte": d.get("REPDTE"),
            "fetched_at": fetched_at,
        })
    return out


def _fetch_all_pages(repdte: str, c: httpx.Client) -> list[dict]:
    """Paginate through every bank record for one REPDTE via offset, verified live to advance
    through distinct CERT ranges with no overlap."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    offset = 0
    while True:
        r = c.get(_page_url(repdte, _PAGE_LIMIT, offset))
        r.raise_for_status()
        payload = r.json()
        page_records = normalize_page(payload, fetched_at)
        if not page_records:
            break
        records.extend(page_records)
        total = (payload.get("meta") or {}).get("total", 0)
        offset += len(page_records)
        if offset >= total or len(page_records) < _PAGE_LIMIT:
            break
    return records


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """Quarterly cadence: state tracks the last REPDTE pulled (e.g. "20260331"). If the most
    recent reportable quarter is newer than state, paginate through ALL banks for that REPDTE
    and emit them all as new records; otherwise emit nothing (correct, not a bug -- this is a
    quarterly source, most daily runs find nothing new)."""
    target_repdte = most_recent_reportable_quarter(date.today())
    last_repdte = state.get("last_repdte")

    if last_repdte is not None and target_repdte <= last_repdte:
        return [], state

    records = _fetch_all_pages(target_repdte, c)
    if not records:
        # Reporting lag surprise (data not posted yet even though our estimate said it should
        # be) -- don't advance state, try again next run.
        return [], state

    new_state = dict(state)
    new_state["last_repdte"] = target_repdte
    return records, new_state
