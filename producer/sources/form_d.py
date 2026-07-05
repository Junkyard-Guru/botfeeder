"""SEC EDGAR Form D source module. Spec: the source-collection validation notes.

Form D is the notice private issuers file for Reg D exempt securities offerings — U.S.
government public domain, same provenance story as Form 4 (docs/03).

Schema validated live 2026-07-03 against a real recent Form D filing (see
tests/fixtures/form_d_sample.xml): root is <edgarSubmission>, no XML namespace declared on
the root (schema is referenced by convention/URL, not xmlns), so stdlib ElementTree with
plain tag names works — same as Form 4.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

import httpx

from producer import edgar

SOURCE_ID = "sec-form-d"
LABEL = "SEC EDGAR Form D (U.S. government public domain)"

DEFAULT_LIMIT = 40
SEEN_CAP = 8000


def client() -> httpx.Client:
    return edgar.client()


def _primary_form_d_xml_url(cik: str, acc_nodash: str, c: httpx.Client) -> str | None:
    """Resolve Form D's primary doc via index.json. Live-validated: it's literally
    named 'primary_doc.xml' (unlike Form 4's varied naming) — but we still look it up
    rather than hardcode the name, in case an older/amended filing differs."""
    import json

    idx = f"{edgar.BASE}/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
    items = json.loads(edgar.fetch(idx, c)).get("directory", {}).get("item", [])
    names = [it["name"] for it in items if it.get("name", "").lower().endswith(".xml")]
    for n in names:
        if "primary_doc" in n.lower():
            return f"{edgar.BASE}/Archives/edgar/data/{cik}/{acc_nodash}/{n}"
    return f"{edgar.BASE}/Archives/edgar/data/{cik}/{acc_nodash}/{names[0]}" if names else None


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def parse_form_d(
    xml: bytes,
    *,
    source_url: str | None = None,
    filing_id: str | None = None,
    filed_at: str | None = None,
    fetched_at: str | None = None,
) -> dict:
    """Parse one Form D primary_doc.xml into one normalized record.

    Degrades gracefully: missing/renamed fields come back None rather than raising, so one
    surprising filing never crashes the whole batch (producer/main.py's per-item try/except
    pattern still applies at the call site).
    """
    root = ET.fromstring(xml)

    issuer = root.find("primaryIssuer")
    offering = root.find("offeringData")

    exemptions: list[str] = []
    if offering is not None:
        fee = offering.find("federalExemptionsExclusions")
        if fee is not None:
            exemptions = [_text(item) for item in fee.findall("item") if _text(item)]

    sales = offering.find("offeringSalesAmounts") if offering is not None else None

    return {
        "filing_id": filing_id,
        "filed_at": filed_at,
        "fetched_at": fetched_at,
        "source_url": source_url,
        "issuer": {
            "cik": _text(issuer.find("cik")) if issuer is not None else None,
            "name": _text(issuer.find("entityName")) if issuer is not None else None,
            "state_of_incorporation": _text(issuer.find("jurisdictionOfInc")) if issuer is not None else None,
        },
        "offering": {
            "exemptions": exemptions,
            "total_offering_amount": _text(sales.find("totalOfferingAmount")) if sales is not None else None,
            "total_amount_sold": _text(sales.find("totalAmountSold")) if sales is not None else None,
            "total_remaining": _text(sales.find("totalRemaining")) if sales is not None else None,
        },
    }


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: recent Form D filings -> resolve primary doc -> parse -> dedupe."""
    seen = state.get("seen", [])
    seen_set = set(seen)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    new_records: list[dict] = []

    filings = edgar.recent_filings("D", DEFAULT_LIMIT, c)
    for f in filings:
        acc = f["accession"]
        if acc in seen_set:
            continue
        try:
            xurl = _primary_form_d_xml_url(f["cik"], f["acc_nodash"], c)
            if not xurl:
                seen.append(acc)
                seen_set.add(acc)
                continue
            rec = parse_form_d(
                edgar.fetch(xurl, c),
                source_url=xurl,
                filing_id=acc,
                filed_at=f.get("filed_at"),
                fetched_at=now,
            )
            new_records.append(rec)
        except Exception as e:  # noqa: BLE001 — one bad filing must not stop the batch
            print(f"[producer:{SOURCE_ID}] skip {acc}: {e}", file=sys.stderr)
        finally:
            seen.append(acc)
            seen_set.add(acc)

    state["seen"] = seen[-SEEN_CAP:]
    return new_records, state
