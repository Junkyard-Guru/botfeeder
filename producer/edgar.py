"""EDGAR access layer. Spec: docs/03-product-edgar-form4.md, docs/04-data-sources.md.

Provenance is built in here: we hit EDGAR DIRECTLY (no reseller), which is what makes
"fresh-squeezed from the primary source" literally true. EDGAR content is U.S.-government
public domain — free to access and reuse.

Compliance is the entire legal burden: declare a real User-Agent and stay <= 10 req/s.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET

import httpx

# TODO(phase4): a reachable contact before going live; SEC wants a real UA.
USER_AGENT = "The Junkyard (botfeeder.junkyard.guru) - contact TBD"  # ASCII only; headers are latin-1
BASE = "https://www.sec.gov"
_MIN_INTERVAL = 0.15  # ~6.7 req/s, safely under SEC's 10/s ceiling

_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_ACC_RE = re.compile(r"accession-number=([0-9-]+)")
_DATA_RE = re.compile(r"/data/(\d+)/(\d+)/")

_last = 0.0


def _throttle() -> None:
    global _last
    gap = time.monotonic() - _last
    if gap < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - gap)
    _last = time.monotonic()


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=30.0,
    )


def fetch(url: str, c: httpx.Client | None = None) -> bytes:
    """GET one URL, throttled and with the declared User-Agent."""
    own = c is None
    c = c or client()
    try:
        _throttle()
        r = c.get(url)
        r.raise_for_status()
        return r.content
    finally:
        if own:
            c.close()


def _parse_edgar_atom(xml: bytes) -> list[dict]:
    """Parse a browse-edgar Form 4 atom feed into filing dicts, deduped by accession.

    Shared by the firehose ('getcurrent') and the per-CIK watch query ('getcompany') — both
    return the same entry shape: {accession, cik, acc_nodash, form, filed_at, index_url}.
    """
    root = ET.fromstring(xml)
    out: list[dict] = []
    seen: set[str] = set()
    for e in root.findall("a:entry", _ATOM):
        m = _ACC_RE.search(e.findtext("a:id", default="", namespaces=_ATOM))
        if not m:
            continue
        acc = m.group(1)
        if acc in seen:
            continue
        seen.add(acc)
        link = e.find("a:link", _ATOM)
        href = link.get("href") if link is not None else ""
        dm = _DATA_RE.search(href or "")
        cat = e.find("a:category", _ATOM)
        out.append({
            "accession": acc,
            "cik": dm.group(1) if dm else None,
            "acc_nodash": dm.group(2) if dm else acc.replace("-", ""),
            "form": cat.get("term") if cat is not None else "4",
            "filed_at": e.findtext("a:updated", default=None, namespaces=_ATOM),
            "index_url": href,
            "title": e.findtext("a:title", default=None, namespaces=_ATOM),
            "summary": e.findtext("a:summary", default=None, namespaces=_ATOM),
        })
    return out


def recent_filings(form_type: str, limit: int = 40, c: httpx.Client | None = None) -> list[dict]:
    """Recent filings of any EDGAR form type from the 'getcurrent' firehose.

    Validated live 2026-07-03 (source-collection validation pass) against type=4, D, 8-K, 13F-HR — same atom shape,
    same dedup-by-accession contract, for every form EDGAR's daily-index tracks.
    """
    url = (f"{BASE}/cgi-bin/browse-edgar?action=getcurrent&type={form_type}"
           f"&company=&dateb=&owner=include&count={limit}&output=atom")
    return _parse_edgar_atom(fetch(url, c))


def recent_form4_filings(limit: int = 40, c: httpx.Client | None = None) -> list[dict]:
    """Recent Form 4 / 4-A filings from EDGAR's 'getcurrent' feed (the firehose)."""
    return recent_filings("4", limit, c)


def recent_form4_for_cik(cik: str, limit: int = 10, c: httpx.Client | None = None) -> list[dict]:
    """Form 4 filings where this CIK is issuer OR reporting owner (the watch query).

    EDGAR cross-indexes a Form 4 under both the company and the insider, so one getcompany call
    per watched CIK catches both 'someone traded this company' and 'this insider traded'. This is
    the reliability fix vs. the 40-cap firehose — scoped to exactly what the buyer paid to watch.
    """
    url = (f"{BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4"
           f"&dateb=&owner=include&count={limit}&output=atom")
    return _parse_edgar_atom(fetch(url, c))


# --- watchlist resolution: ticker / company name / raw CIK -> CIK ---------------------------------

_ticker_map: dict | None = None


def _load_ticker_map(c: httpx.Client | None = None) -> dict:
    """EDGAR's official ticker->CIK table, cached in-process. Covers ticker'd companies only;
    insiders/funds without tickers are watched by passing a raw CIK."""
    global _ticker_map
    if _ticker_map is None:
        raw = json.loads(fetch(f"{BASE}/files/company_tickers.json", c))
        _ticker_map = {row["ticker"].upper(): {"cik": str(row["cik_str"]), "ticker": row["ticker"],
                                               "title": row["title"]} for row in raw.values()}
    return _ticker_map


def resolve_to_cik(query: str, c: httpx.Client | None = None) -> dict | None:
    """Resolve a watchlist item to {cik, label, name}. Accepts a raw CIK (digits), an exact
    ticker, or a company-name substring. Returns None if unresolvable."""
    q = (query or "").strip()
    if not q:
        return None
    if q.isdigit():
        return {"cik": str(int(q)), "label": f"CIK{int(q)}", "name": None}
    m = _load_ticker_map(c)
    hit = m.get(q.upper())
    if hit:
        return {"cik": hit["cik"], "label": hit["ticker"], "name": hit["title"]}
    ql = q.lower()
    matches = [v for v in m.values() if ql in v["title"].lower()]
    if matches:
        best = min(matches, key=lambda v: len(v["title"]))  # shortest title = closest match
        return {"cik": best["cik"], "label": best["ticker"], "name": best["title"]}
    return None


def primary_form4_xml_url(cik: str, acc_nodash: str, c: httpx.Client | None = None) -> str | None:
    """Resolve the ownership XML document for a filing via its folder index.json.

    The doc name varies (form4.xml, wf-form4_*.xml, edgar.xml, primary_doc.xml...), so we
    look it up rather than assume. R-prefixed files are report fragments — excluded.
    """
    idx = f"{BASE}/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
    items = json.loads(fetch(idx, c)).get("directory", {}).get("item", [])
    xmls = [it["name"] for it in items if it.get("name", "").lower().endswith(".xml")]
    cand = [n for n in xmls if not n.lower().startswith("r")] or xmls
    for pat in ("form4", "ownership", "primary_doc", "doc4", "edgar"):
        for n in cand:
            if pat in n.lower():
                return f"{BASE}/Archives/edgar/data/{cik}/{acc_nodash}/{n}"
    return f"{BASE}/Archives/edgar/data/{cik}/{acc_nodash}/{cand[0]}" if cand else None
