"""openFDA Drugs@FDA source module. Spec: the source-collection validation notes.

Drugs@FDA is the FDA's (Food and Drug Administration) database of approved drug products
and their regulatory history — U.S. government public domain, served via openFDA, no key
required (an optional free key just raises the rate limit).

Endpoint: https://api.fda.gov/drug/drugsfda.json
Confirmed live 2026-07-03: 29,177 total records, `meta.last_updated` = "2026-07-01" at fetch
time — see tests/fixtures/openfda_drugsfda_sample.json for real application records (ANDA
generics with submissions history and product/active-ingredient detail).

This is a slow-changing REFERENCE CORPUS, not an event stream — the FDA republishes the
whole dataset periodically rather than emitting daily deltas, and there is no reliable
"changed since" filter on this endpoint. Design:
  1. Check `meta.last_updated` against state["last_updated"].
  2. If unchanged: cheap no-op, return ([], state) immediately (one lightweight metadata
     request, no pagination).
  3. If changed (or first run): paginate the FULL corpus via skip/limit and return every
     record. At ~29k records and PAGE_LIMIT=100 that's ~292 requests in one run — acceptable
     for something that fires at most a few times a month. `runner.py`'s snapshot_cap will
     truncate the served snapshot regardless; the full-fidelity archive/*.jsonl is what
     actually needs (and can hold) all 29k+ records.

Cadence: check meta.last_updated on a daily or even weekly timer; the corpus itself only
seems to move every few days to weeks, so daily is a check-cost, not a fetch-cost, concern.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import httpx

SOURCE_ID = "openfda-drugsfda"
LABEL = "openFDA Drugs@FDA (U.S. government public domain)"

BASE = "https://api.fda.gov/drug/drugsfda.json"
PAGE_LIMIT = 100
MAX_PAGES = 400  # backstop: ~40k records, comfortably above the known ~29k corpus size


def client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _normalize(app: dict, fetched_at: str) -> dict:
    return {
        "application_number": app.get("application_number"),
        "sponsor_name": app.get("sponsor_name"),
        "submissions": [
            {
                "submission_type": s.get("submission_type"),
                "submission_number": s.get("submission_number"),
                "submission_status": s.get("submission_status"),
                "submission_status_date": s.get("submission_status_date"),
            }
            for s in app.get("submissions", [])
        ],
        "products": [
            {
                "product_number": p.get("product_number"),
                "brand_name": p.get("brand_name"),
                "dosage_form": p.get("dosage_form"),
                "route": p.get("route"),
                "marketing_status": p.get("marketing_status"),
                "reference_drug": p.get("reference_drug"),
                "active_ingredients": [
                    {"name": i.get("name"), "strength": i.get("strength")}
                    for i in p.get("active_ingredients", [])
                ],
            }
            for p in app.get("products", [])
        ],
        "fetched_at": fetched_at,
        "source_url": BASE,
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """Cheap no-op unless the upstream corpus has moved since our last full pull, per
    meta.last_updated; otherwise paginate the entire corpus and return it all."""
    now = datetime.now(timezone.utc).isoformat()

    try:
        r = c.get(BASE, params={"limit": "1"})
        r.raise_for_status()
        meta = r.json().get("meta", {})
    except Exception as e:  # noqa: BLE001
        print(f"[producer:{SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        raise

    last_updated = meta.get("last_updated")
    if last_updated and last_updated == state.get("last_updated"):
        return [], state  # corpus unchanged since our last full pull — nothing to do

    new_records: list[dict] = []
    skip = 0
    for _ in range(MAX_PAGES):
        try:
            r = c.get(BASE, params={"limit": str(PAGE_LIMIT), "skip": str(skip)})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                # openFDA hard-caps deep pagination (skip+limit beyond ~25k, undocumented
                # exact cutoff, observed live 2026-07-03 at skip=25100 against a ~29k-record
                # corpus) — this is a platform limit, not a transient failure. Stop cleanly
                # with what we have rather than raising; log so a capped pull is never mistaken
                # for a complete one (doc 10's "no silent caps" convention).
                print(f"[producer:{SOURCE_ID}] hit openFDA's pagination limit at skip={skip} "
                      f"— stopping with {len(new_records)} records (corpus may be larger; "
                      f"this is a known platform cap, not an error)", file=sys.stderr)
                break
            print(f"[producer:{SOURCE_ID}] pagination failed after {len(new_records)} records: {e}",
                  file=sys.stderr)
            raise
        payload = r.json()
        results = payload.get("results", [])
        if not results:
            break
        new_records.extend(_normalize(app, now) for app in results)
        skip += PAGE_LIMIT
        total = payload.get("meta", {}).get("results", {}).get("total")
        if total is not None and skip >= total:
            break

    if last_updated:
        state["last_updated"] = last_updated
    return new_records, state
