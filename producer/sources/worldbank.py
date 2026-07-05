"""World Bank Open Data access layer. Spec: the multi-source pipeline validation notes.

Confirmed live 2026-07-03 against
https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD?format=json&per_page=N
which returned the real US GDP series (NY.GDP.MKTP.CD) for 2024/2025. Response shape is a
2-element JSON array: [ {page, pages, per_page, total, sourceid, lastupdated}, [ {indicator,
country, countryiso3code, date, value, unit, obs_status, decimal}, ... ] ]. The data list is
already flat (one dict per country/indicator/year) -- no JSON-stat decoding needed here, unlike
Eurostat.

World Bank content is public domain / CC-BY 4.0 -- free to access and reuse with attribution.

This is a reference dataset (29,536 indicators x ~200 countries): we deliberately curate a
small, fixed watchlist of indicator x country pairs rather than attempting a full pull.
"""
from __future__ import annotations

import sys

import httpx

SOURCE_ID = "worldbank-indicators"
LABEL = "World Bank Open Data (public domain / CC-BY 4.0)"

BASE = "https://api.worldbank.org/v2"
_TIMEOUT = 30.0
_PER_PAGE = 20  # a handful of recent years per series is plenty; these update ~annually

# Curated watchlist: 5 real World Bank indicator codes x 10 major economies. Codes verified
# live against the API (source-collection validation pass): GDP, inflation, unemployment, real interest rate, plus GDP
# growth as the "one more relevant" pick (all are widely used macro headline indicators).
INDICATORS: dict[str, str] = {
    "NY.GDP.MKTP.CD": "GDP (current US$)",
    "FP.CPI.TOTL.ZG": "Inflation, consumer prices (annual %)",
    "SL.UEM.TOTL.ZS": "Unemployment, total (% of total labor force)",
    "FR.INR.RINR": "Real interest rate (%)",
    "NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)",
}

# ISO2 country codes. "EU" is the World Bank's European Union aggregate.
COUNTRIES: dict[str, str] = {
    "US": "United States",
    "CN": "China",
    "JP": "Japan",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "IN": "India",
    "BR": "Brazil",
    "CA": "Canada",
    "KR": "Korea, Rep.",
}


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": "The Junkyard (botfeeder.junkyard.guru) - contact TBD"},
        timeout=_TIMEOUT,
    )


def _series_url(country: str, indicator: str) -> str:
    return f"{BASE}/country/{country}/indicator/{indicator}?format=json&per_page={_PER_PAGE}"


def _fetch_series(country: str, indicator: str, c: httpx.Client) -> list[dict]:
    """One country/indicator series -> list of raw World Bank data-point dicts (newest first)."""
    r = c.get(_series_url(country, indicator))
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    return payload[1]


def normalize_series(raw_points: list[dict], fetched_at: str) -> list[dict]:
    """Pure function: raw World Bank data points -> normalized records. No I/O.

    Skips points with a null value (World Bank returns these for years not yet reported).
    """
    out: list[dict] = []
    for pt in raw_points:
        if pt.get("value") is None:
            continue
        indicator = pt.get("indicator") or {}
        country = pt.get("country") or {}
        code = indicator.get("id")
        cc = country.get("id")
        out.append({
            "country_code": cc,
            "country_name": country.get("value"),
            "indicator_code": code,
            "indicator_name": indicator.get("value"),
            "date": pt.get("date"),
            "value": pt.get("value"),
            "unit": pt.get("unit") or None,
            "source_url": _series_url(cc, code) if cc and code else None,
            "fetched_at": fetched_at,
        })
    return out


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """Fetch each curated indicator/country series, emit only data points newer than state.

    State: {"US:NY.GDP.MKTP.CD": "2025", ...} -- last date string already emitted per series
    key. World Bank updates annually (mostly) so most runs will find nothing new; that's
    correct, not a bug.
    """
    from datetime import datetime, timezone

    fetched_at = datetime.now(timezone.utc).isoformat()
    new_state = dict(state)
    new_records: list[dict] = []

    for country in COUNTRIES:
        for indicator in INDICATORS:
            key = f"{country}:{indicator}"
            last_date = state.get(key)

            try:
                raw = _fetch_series(country, indicator, c)
            except Exception as e:  # noqa: BLE001 — one slow/failing series (50 sequential
                # calls per run; observed live 2026-07-03: a single hung request took down the
                # whole run before this fix) must not cost every other series its data.
                print(f"[producer:{SOURCE_ID}] {key}: fetch failed, skipping this series: {e}",
                      file=sys.stderr)
                continue
            records = normalize_series(raw, fetched_at)
            if not records:
                continue

            # World Bank returns newest-date-first; only keep dates strictly newer than state.
            fresh = [r for r in records if last_date is None or r["date"] > last_date]
            if not fresh:
                continue

            new_records.extend(fresh)
            newest = max(r["date"] for r in records)
            new_state[key] = newest

    return new_records, new_state
