"""SEC EDGAR 13F-HR institutional-holdings source module. Spec: the source-collection validation notes.

13F-HR filings carry TWO XML docs, live-validated 2026-07-03 (tests/fixtures/form_13f_*):

  - primary_doc.xml — cover page (filing manager name/CIK, period of report). Small (~2KB).
  - a per-quarter-named doc, e.g. '2026qtr2submissionapr2026.xml' — the information table,
    one <infoTable> per holding. This is NOT reliably named with "infotable" in practice
    (surprise vs. the spec guess) — we resolve it by elimination: the largest non-primary_doc
    XML in the filing index, since real info tables run tens-to-hundreds of KB vs. a ~2KB cover.

The info table root is namespaced (xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/
informationtable") and every element uses the ns1: prefix, unlike Form 4's bare tags.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from producer import edgar

SOURCE_ID = "sec-13f-hr"
LABEL = "SEC EDGAR 13F-HR institutional holdings (U.S. government public domain)"

DEFAULT_LIMIT = 40
SEEN_CAP = 8000

_NS = {"t": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def client() -> httpx.Client:
    return edgar.client()


def _resolve_docs(cik: str, acc_nodash: str, c: httpx.Client) -> tuple[str | None, str | None]:
    """Return (cover_page_url, info_table_url) for a 13F-HR filing.

    info table = the largest non-'primary_doc' XML in the folder (see module docstring for
    why name-matching on 'infotable' doesn't hold up against real filings).
    """
    idx = f"{edgar.BASE}/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
    items = json.loads(edgar.fetch(idx, c)).get("directory", {}).get("item", [])
    xml_items = [it for it in items if it.get("name", "").lower().endswith(".xml")]

    cover = next((it for it in xml_items if "primary_doc" in it["name"].lower()), None)
    others = [it for it in xml_items if it is not cover]

    def _size(it: dict) -> int:
        try:
            return int(it.get("size") or 0)
        except ValueError:
            return 0

    info = max(others, key=_size, default=None)

    base = f"{edgar.BASE}/Archives/edgar/data/{cik}/{acc_nodash}"
    cover_url = f"{base}/{cover['name']}" if cover else None
    info_url = f"{base}/{info['name']}" if info else None
    return cover_url, info_url


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_cover(xml: bytes) -> dict:
    """Parse the 13F-HR cover page (primary_doc.xml) for filer identity."""
    root = ET.fromstring(xml)
    # cover page declares a default namespace; find-by-localname to stay robust.
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[: root.tag.index("}") + 1]
    cik = root.find(f".//{ns}filer/{ns}credentials/{ns}cik")
    name = root.find(f".//{ns}coverPage/{ns}filingManager/{ns}name")
    period = root.find(f".//{ns}periodOfReport")
    return {
        "filer_cik": _text(cik),
        "filer_name": _text(name),
        "period_of_report": _text(period),
    }


def parse_13f_info_table(
    xml: bytes,
    *,
    source_url: str | None = None,
    filing_id: str | None = None,
    filed_at: str | None = None,
    fetched_at: str | None = None,
    filer_cik: str | None = None,
    filer_name: str | None = None,
) -> list[dict]:
    """Parse the 13F-HR information table into one normalized record per holding row."""
    root = ET.fromstring(xml)
    records = []
    for row in root.findall("t:infoTable", _NS):
        shrs = row.find("t:shrsOrPrnAmt", _NS)
        voting = row.find("t:votingAuthority", _NS)
        records.append({
            "filing_id": filing_id,
            "filed_at": filed_at,
            "fetched_at": fetched_at,
            "source_url": source_url,
            "filer": {"cik": filer_cik, "name": filer_name},
            "holding": {
                "name_of_issuer": _text(row.find("t:nameOfIssuer", _NS)),
                "title_of_class": _text(row.find("t:titleOfClass", _NS)),
                "cusip": _text(row.find("t:cusip", _NS)),
                "value": _num(_text(row.find("t:value", _NS))),
                "shares": _num(_text(shrs.find("t:sshPrnamt", _NS))) if shrs is not None else None,
                "shares_type": _text(shrs.find("t:sshPrnamtType", _NS)) if shrs is not None else None,
                "put_call": _text(row.find("t:putCall", _NS)),
                "investment_discretion": _text(row.find("t:investmentDiscretion", _NS)),
                "voting_authority_sole": _num(_text(voting.find("t:Sole", _NS))) if voting is not None else None,
                "voting_authority_shared": _num(_text(voting.find("t:Shared", _NS))) if voting is not None else None,
                "voting_authority_none": _num(_text(voting.find("t:None", _NS))) if voting is not None else None,
            },
        })
    return records


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: recent 13F-HR filings -> resolve cover+info-table docs -> flatten rows."""
    seen = state.get("seen", [])
    seen_set = set(seen)
    now = datetime.now(timezone.utc).isoformat()

    new_records: list[dict] = []
    filings = edgar.recent_filings("13F-HR", DEFAULT_LIMIT, c)
    for f in filings:
        acc = f["accession"]
        if acc in seen_set:
            continue
        try:
            cover_url, info_url = _resolve_docs(f["cik"], f["acc_nodash"], c)
            filer_cik = filer_name = None
            if cover_url:
                cover = parse_cover(edgar.fetch(cover_url, c))
                filer_cik = cover.get("filer_cik") or f.get("cik")
                filer_name = cover.get("filer_name")
            if info_url:
                rows = parse_13f_info_table(
                    edgar.fetch(info_url, c),
                    source_url=info_url,
                    filing_id=acc,
                    filed_at=f.get("filed_at"),
                    fetched_at=now,
                    filer_cik=filer_cik or f.get("cik"),
                    filer_name=filer_name,
                )
                new_records.extend(rows)
        except Exception as e:  # noqa: BLE001 — one bad filing must not stop the batch
            print(f"[producer:{SOURCE_ID}] skip {acc}: {e}", file=sys.stderr)
        finally:
            seen.append(acc)
            seen_set.add(acc)

    state["seen"] = seen[-SEEN_CAP:]
    return new_records, state
