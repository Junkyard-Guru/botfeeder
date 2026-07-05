"""FCC ULS (Universal Licensing System) & spectrum auctions source module.
Spec: the source-collection validation notes.

Reachability, checked live 2026-07-03 from THIS environment (not just the prior research
pass — re-verified independently):
    - https://www.fcc.gov/uls/transactions/daily-weekly  -> connection failure (curl exit,
      no TCP response at all; not a 403/404, just unreachable)
    - https://www.fcc.gov/auctions                        -> same, connection failure
    - https://www.fcc.gov/                                 -> same, connection failure
    - https://opendata.fcc.gov/api/views.json               -> 200 OK, reachable

So fcc.gov itself is confirmed unreachable from this sandbox, consistent with the prior
research pass. This module uses the opendata.fcc.gov Socrata fallback, NOT real fcc.gov ULS
weekly transaction files or real-time auction results — see the confidence note below.

opendata.fcc.gov catalog search (live 2026-07-03, /api/views.json): of the ULS/auction-named
entries, `x28i-i4z4` ("FCC Universal Licensing System (ULS)") is an `href`-type catalog entry
(a link-out, not a queryable table) and its /resource/<id>.json endpoint 404s/errors as
"no row or column access to non-tabular tables" — confirmed dead-end, matches prior research.

Three OTHER entries in the same catalog ARE genuinely tabular and queryable (confirmed live,
real rows returned from /resource/<id>.json?$limit=2):
    - r3zi-75n9  "ULS 3650 Locations (Complete Dataset)"
    - dpvg-tvcx  "ULS 3650 Locations Default View"
    - euz5-46g2  "ULS 3650 Locations"                      <- used here (fullest schema of the 3)
This is FCC Wireless Telecommunications Bureau license/location data for the 3650-3700 MHz
band specifically (a real ULS sub-extract), NOT the full multi-service ULS database and NOT
auction results. Real columns (from a live 2-row query against euz5-46g2): u_call_sign,
u_license_name, u_frn, u_location_name, u_location_number, u_application_status, u_status_date,
u_transmitter_location, u_azimuth, u_beam, u_location_city, u_location_state,
u_fcc_equipment_designation_type, u_yn_base_station, sys_updated_on, u_antenna_make,
u_antenna_model, u_application_receipt_date, u_eirp, u_elevation_angle, u_elevation_amsl,
u_emission_designator, u_expired_date, u_fcc_id, u_gain, u_latitude, u_license_id,
u_location_county, u_location_id, u_longitude, u_lower_frequency, u_file_number,
u_overall_height, u_height_of_structure, u_transmission_protocol, u_upper_frequency.

IMPORTANT STALENESS CAVEAT (confirmed live via the dataset's own metadata endpoint,
/api/views/euz5-46g2.json): rowsUpdatedAt = 1545207557 (2018-12-19). This dataset is NOT
being actively updated — it is a frozen historical extract, not a live-refreshed feed. No
newer or better-covering tabular ULS/auction dataset was found in this catalog (100 entries
total returned by the catalog listing; none named "auction" resolve to a queryable table, and
no post-2018 ULS dataset exists in this catalog as of this check).

CONFIDENCE: LOW-MODERATE. This is confirmed-live, real, queryable government data (not
fabricated), but it is (a) scoped to one radio band's licenses only, not general ULS
transactions or auction results, and (b) not being kept current. Treat this source as a
"best available fallback," not "authoritative ULS/auctions feed" — flag to whoever consumes
this snapshot that fcc.gov's own weekly transaction files and auction result pages are the
real authoritative source and remain unreachable from this environment.

Given the dataset is frozen, "new" records can only mean "not yet paginated through" (offset-
based catalog walk), since there's no forward-moving timestamp to poll against. Cadence:
still recommend a daily/quarterly check (cheap $limit=1 probe) in case FCC ever resumes
updating it or opendata.fcc.gov adds a fresher replacement dataset — but do not expect new
data most days.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import httpx

SOURCE_ID = "fcc-uls-auctions"
LABEL = "FCC Universal Licensing System & Spectrum Auctions (U.S. government public domain)"

USER_AGENT = "The Junkyard (botfeeder.junkyard.guru) - contact TBD"
# Fallback dataset (see module docstring): ULS 3650 Locations, live-confirmed tabular+queryable,
# but frozen since 2018-12. NOT the general ULS transaction/auction feed (fcc.gov itself is
# unreachable from this environment) — used as the best available real substitute.
DATASET_ID = "euz5-46g2"
BASE = f"https://opendata.fcc.gov/resource/{DATASET_ID}.json"
PAGE_SIZE = 500


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)


def _normalize(row: dict, *, fetched_at: str) -> dict:
    return {
        "licensee": row.get("u_license_name"),
        "call_sign": row.get("u_call_sign"),
        "frn": row.get("u_frn") or row.get("u_frns"),
        "service_type": "3650-3700 MHz wireless broadband (ULS)",
        "frequency_range": f"{row.get('u_lower_frequency', '')} - {row.get('u_upper_frequency', '')}".strip(" -") or None,
        "action_type": row.get("u_application_status"),
        "effective_date": row.get("u_status_date"),
        "receipt_date": row.get("u_application_receipt_date"),
        "expired_date": row.get("u_expired_date"),
        "location_city": row.get("u_location_city"),
        "location_state": row.get("u_location_state"),
        "location_county": row.get("u_location_county"),
        "license_id": row.get("u_license_id"),
        "fcc_id": row.get("u_fcc_id"),
        "source_url": f"{BASE}?$limit=1&u_license_id={row.get('u_license_id')}",
        "fetched_at": fetched_at,
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: page through the frozen ULS-3650 dataset via Socrata's $offset, one
    page (PAGE_SIZE rows) per cycle, tracking offset in state. Since the dataset isn't live-
    updated (see module docstring), this walks the existing rows once to completion rather
    than polling for genuinely new data; once offset reaches the end, it stops (state records
    "exhausted": true) until a human notices the dataset started moving again."""
    now = datetime.now(timezone.utc).isoformat()

    if state.get("exhausted"):
        return [], state

    offset = state.get("offset", 0)
    params = {"$limit": PAGE_SIZE, "$offset": offset, "$order": "u_license_id"}

    try:
        r = c.get(BASE, params=params)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001 — one bad cycle must not stop other sources
        print(f"[producer:{SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        return [], state

    if not rows:
        state["exhausted"] = True
        return [], state

    records = [_normalize(row, fetched_at=now) for row in rows]
    state["offset"] = offset + len(rows)
    if len(rows) < PAGE_SIZE:
        state["exhausted"] = True
    return records, state
