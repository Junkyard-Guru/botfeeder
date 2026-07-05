"""Company-name / CIK -> ticker resolution for signal mapping. Spec: docs/13.

Several signal mappers (USASpending recipients, openFDA sponsors, FDIC holding companies,
13F holdings, BIS entities, 8-K filers) need to attach a raw record to a tradeable ticker.
The source of truth is EDGAR's own company_tickers.json — already fetched/cached by
producer/edgar.py for the watch product, so this adds no new upstream dependency.

CONSERVATIVE BY DESIGN: a false ticker attribution is worse than none — it would route a
signal to the wrong company's feed, which violates the docs/07 "every claim must be true"
rule at the record level. So name matching is exact-after-normalization only (no fuzzy
scoring), and an ambiguous normalized name (two different companies collapsing to the same
key) resolves to None rather than guessing. Records that don't resolve keep their raw
identity fields; they just don't get a ticker-scoped signal.
"""
from __future__ import annotations

import re

# Suffix/noise tokens dropped during normalization. Deliberately does NOT include words that
# distinguish real companies (e.g. "INTERNATIONAL", "FINANCIAL") — only pure legal-form noise.
_DROP_TOKENS = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LLC", "LP", "LLP",
    "LTD", "LIMITED", "PLC", "SA", "NV", "AG", "SE", "THE", "TRUST", "NA",
}

_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")


def normalize_name(name: str | None) -> str | None:
    """'EA Engineering, Science, and Technology, Inc., PBC' -> 'EA ENGINEERING SCIENCE AND TECHNOLOGY PBC'."""
    if not name:
        return None
    s = _NON_ALNUM.sub(" ", name.upper())
    tokens = [t for t in s.split() if t not in _DROP_TOKENS]
    return " ".join(tokens) or None


class TickerMap:
    """Built from EDGAR company_tickers.json rows: {ticker, cik_str, title}."""

    def __init__(self, rows: list[dict]):
        self.by_cik: dict[str, dict] = {}
        self.by_name: dict[str, dict | None] = {}  # None marks an ambiguous (poisoned) key
        for row in rows:
            entry = {"ticker": row["ticker"], "cik": str(int(row["cik_str"])), "name": row["title"]}
            # First ticker wins per CIK — EDGAR lists primary share classes first (GOOGL before GOOG).
            self.by_cik.setdefault(entry["cik"], entry)
            key = normalize_name(row["title"])
            if not key:
                continue
            prior = self.by_name.get(key, "__unset__")
            if prior == "__unset__":
                self.by_name[key] = entry
            elif prior is not None and prior["cik"] != entry["cik"]:
                self.by_name[key] = None  # two DIFFERENT companies share this name — refuse to guess

    def from_cik(self, cik: str | int | None) -> dict | None:
        if cik is None:
            return None
        try:
            return self.by_cik.get(str(int(cik)))
        except (ValueError, TypeError):
            return None

    def from_name(self, name: str | None) -> dict | None:
        key = normalize_name(name)
        if not key:
            return None
        return self.by_name.get(key) or None


_default: TickerMap | None = None


def default_map() -> TickerMap:
    """Process-cached TickerMap from EDGAR's live company_tickers.json (one fetch/process)."""
    global _default
    if _default is None:
        from producer import edgar
        raw = edgar._load_ticker_map()  # noqa: SLF001 — reuse edgar's cache, same process
        _default = TickerMap([
            {"ticker": v["ticker"], "cik_str": v["cik"], "title": v["title"]} for v in raw.values()
        ])
    return _default
