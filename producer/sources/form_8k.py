"""SEC EDGAR 8-K source module. Spec: the source-collection validation notes.

8-K material-event Item codes already ride along in the 'getcurrent' atom feed's <summary>
HTML (validated live 2026-07-03, see tests/fixtures/form_8k_atom_sample.xml) — no primary-doc
fetch needed for the base record. Item lines look like:

    <b>Filed:</b> 2026-07-02 <b>AccNo:</b> 0001683168-26-005262 <b>Size:</b> 270 KB
    <br>Item 1.01: Entry into a Material Definitive Agreement
    <br>Item 2.03: Creation of a Direct Financial Obligation ...
    <br>Item 9.01: Financial Statements and Exhibits

Filer name/CIK come from the atom <title>, "8-K - {Company} ({CIK}) (Filer)".
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

import httpx

from producer import edgar

SOURCE_ID = "sec-8k"
LABEL = "SEC EDGAR 8-K (U.S. government public domain)"

DEFAULT_LIMIT = 40
SEEN_CAP = 8000

_TITLE_RE = re.compile(r"^\s*8-K\S*\s*-\s*(.+?)\s*\((\d+)\)\s*\(Filer\)\s*$")
_ITEM_RE = re.compile(r"Item\s+([0-9]+\.[0-9]+)\s*:\s*([^<\n]+)")


def client() -> httpx.Client:
    return edgar.client()


def _parse_title(title: str | None) -> tuple[str | None, str | None]:
    """'8-K - Company Name (0001234567) (Filer)' -> (name, cik)."""
    if not title:
        return None, None
    m = _TITLE_RE.match(title)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _parse_items(summary: str | None) -> list[dict]:
    """Extract Item codes + descriptions from the atom <summary> HTML text."""
    if not summary:
        return []
    items = []
    for m in _ITEM_RE.finditer(summary):
        items.append({"code": m.group(1), "description": m.group(2).strip()})
    return items


def normalize(filing: dict, *, fetched_at: str | None = None) -> dict:
    """Turn one edgar.recent_filings('8-K', ...) entry into a normalized record."""
    name, cik_from_title = _parse_title(filing.get("title"))
    return {
        "filing_id": filing["accession"],
        "filed_at": filing.get("filed_at"),
        "fetched_at": fetched_at,
        "source_url": filing.get("index_url"),
        "issuer": {
            "cik": filing.get("cik") or cik_from_title,
            "name": name,
        },
        "items": _parse_items(filing.get("summary")),
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: recent 8-K filings, item codes parsed straight from the atom summary."""
    seen = state.get("seen", [])
    seen_set = set(seen)
    now = datetime.now(timezone.utc).isoformat()

    new_records: list[dict] = []
    filings = edgar.recent_filings("8-K", DEFAULT_LIMIT, c)
    for f in filings:
        acc = f["accession"]
        if acc in seen_set:
            continue
        try:
            new_records.append(normalize(f, fetched_at=now))
        except Exception as e:  # noqa: BLE001 — one bad filing must not stop the batch
            print(f"[producer:{SOURCE_ID}] skip {acc}: {e}", file=sys.stderr)
        finally:
            seen.append(acc)
            seen_set.add(acc)

    state["seen"] = seen[-SEEN_CAP:]
    return new_records, state
