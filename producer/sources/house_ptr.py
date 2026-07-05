"""U.S. House of Representatives Periodic Transaction Report (PTR) source module.

Spec: the source-collection validation notes. PTRs are the STOCK Act-mandated disclosures
Members of Congress file within 45 days of a covered securities transaction (buy/sell/
exchange) above $1,000 — U.S. government public record, same provenance story as Form 4/D.

Pipeline (validated live 2026-07-03):
  1. Annual filer index: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/
     {year}FD.zip -> {year}FD.xml, one <Member> per disclosure filing. FilingType == "P" is
     a Periodic Transaction Report (C = Candidate Report, W/X/D/A/H/T = other report types
     we don't want here).
  2. PTR PDF: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{DocID}.pdf
     NOTE this is a DIFFERENT path (ptr-pdfs) than annual/candidate reports (financial-pdfs).
     A wrong URL guess still comes back HTTP 200 with an IIS custom-error HTML body, NOT a
     404 status -- so every fetch is verified by checking the response starts with the
     %PDF- magic bytes before we trust it as a real PDF.
  3. Text extraction: subprocess to `pdftotext -layout` (poppler-utils), NOT pypdf. Compared
     head-to-head against real filings: pdftotext -layout preserves the PDF's column
     positions (owner code / asset name / txn type / dates / amount all stay visually
     aligned), which is what makes the per-transaction regex below reliable. pypdf's
     extract_text() collapses that layout -- e.g. adjacent date columns run together
     ("12/12/202501/06/2026" with no separating whitespace) -- which is unsafe to regex
     against. INFRA NOTE: this means the deploy target needs the poppler-utils system
     package (`apt install poppler-utils` on the Ubuntu VPS) -- not a pip dependency, but a
     real system dependency the deploy scripts/Dockerfile must include.
  4. Parsing: THE MOAT, same principle as producer/parser.py -- structured regex parsing of
     the layout-preserved text, not LLM inference. Handles (see _parse_transactions):
       - multi-line-wrapped asset names/tickers ("Ferguson Enterprises Inc. Common\nStock
         (FERG) [ST]")
       - the owner code (SP/JT/DC) being blank for the filer's own direct holdings
       - "(partial)" transaction-type suffix
       - amount ranges that wrap the high end onto its own line
       - repeated table headers on every PDF page break (skipped, not parsed as data)
       - trailing "F...S: New" / "S...O: <broker/account>" / "D...: <free text, itself
         sometimes multi-line>" metadata lines per transaction (owner/description; not part
         of the transaction fields we extract, but must not be mistaken for a new
         transaction's owner-code+asset line)
     Genuinely un-parseable filings (older scanned PTRs with no text layer at all -- observed
     live on a 7-digit DocID from an earlier reporting period) yield zero transactions and are
     logged, not crashed on -- see fetch_new's per-filing try/except.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

BASE = "https://disclosures-clerk.house.gov"
USER_AGENT = "The Junkyard (botfeeder.junkyard.guru) - contact TBD"  # ASCII only

SOURCE_ID = "house-ptr"
LABEL = "U.S. House of Representatives Periodic Transaction Reports (public record, STOCK Act)"

SEEN_CAP = 8000
_PDF_TIMEOUT = 30.0


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=30.0,
    )


# --- annual filer index -----------------------------------------------------------------

def _index_url(year: int) -> str:
    return f"{BASE}/public_disc/financial-pdfs/{year}FD.zip"


def fetch_filer_index(year: int, c: httpx.Client) -> list[dict]:
    """Download+unzip {year}FD.zip and parse {year}FD.xml into filing dicts.

    Returns [] if the year's zip isn't published yet (e.g. very early January before the
    Clerk's office has rolled the index over) rather than raising -- see the year-boundary
    note in fetch_new.
    """
    r = c.get(_index_url(year))
    if r.status_code != 200:
        return []
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml_name = f"{year}FD.xml"
        if xml_name not in zf.namelist():
            return []
        xml_bytes = zf.read(xml_name)
    except zipfile.BadZipFile:
        return []

    root = ET.fromstring(xml_bytes)
    out = []
    for m in root.findall("Member"):
        out.append({
            "last": (m.findtext("Last") or "").strip(),
            "first": (m.findtext("First") or "").strip(),
            "suffix": (m.findtext("Suffix") or "").strip() or None,
            "filing_type": (m.findtext("FilingType") or "").strip(),
            "state_dst": (m.findtext("StateDst") or "").strip() or None,
            "year": (m.findtext("Year") or "").strip() or None,
            "filing_date": (m.findtext("FilingDate") or "").strip() or None,
            "doc_id": (m.findtext("DocID") or "").strip(),
        })
    return out


def _ptr_pdf_url(year: int, doc_id: str) -> str:
    return f"{BASE}/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"


def fetch_ptr_pdf(year: int, doc_id: str, c: httpx.Client) -> bytes | None:
    """GET the PTR PDF. Returns None if the response isn't actually a PDF -- a bad URL comes
    back HTTP 200 with an IIS error page, not a 404, so status alone can't be trusted."""
    url = _ptr_pdf_url(year, doc_id)
    r = c.get(url, timeout=_PDF_TIMEOUT)
    if r.status_code != 200 or not r.content.startswith(b"%PDF-"):
        return None
    return r.content


# --- PDF text extraction -----------------------------------------------------------------

def pdf_to_text(pdf_bytes: bytes) -> str:
    """Layout-preserving text extraction via poppler's pdftotext (subprocess).

    Chosen over pypdf after testing both against real filings: pdftotext -layout keeps
    columns visually aligned (required for the regex parser below); pypdf's extract_text()
    does not preserve column gaps and runs adjacent fields together. Raises
    FileNotFoundError if poppler-utils isn't installed -- the caller (fetch_new) lets that
    propagate per-filing, same as any other parse failure.

    Uses real temp files rather than stdin/stdout pipes: the pdftotext build validated on
    this box (xpdf/Glyph & Cog 4.00, the poppler-compatible CLI shipped via mingw64) doesn't
    accept '-' as a stdin/stdout placeholder -- it just prints usage and exits 99 -- so we
    write the input and read the output from disk instead.
    """
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        in_path = _Path(tmp) / "in.pdf"
        out_path = _Path(tmp) / "out.txt"
        in_path.write_bytes(pdf_bytes)
        subprocess.run(
            ["pdftotext", "-layout", str(in_path), str(out_path)],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return out_path.read_text(encoding="utf-8", errors="replace")


# --- transaction parsing -----------------------------------------------------------------
#
# pdftotext -layout emits one transaction as a ragged multi-line block, e.g.:
#
#   SP        Ferguson Enterprises Inc. Common P     12/12/2025 01/06/2026 $15,001 -
#                                                                                       $50,000
#             Stock (FERG) [ST]
#             F     S  : New
#             S        O : R.W. Allen & Associates, Inc. > RWA&A - Securities
#
# Notice the asset name ("Ferguson Enterprises Inc. Common Stock") and its trailing
# "(TICKER) [TYPE]" tag can straddle the LEAD line and the line(s) immediately after it -- the
# ticker/type is not reliably on the lead line itself. So parsing is two-pass:
#   1. Find every "lead line" -- the line carrying the owner code (optional), txn type
#      (P/S/E, optional "(partial)"), both dates, and the low end of the amount range.
#   2. Collect every line up to (but not including) the next lead line or a metadata line
#      ("F...: New" / "S...O: ..." / "D...: ...", themselves possibly multi-line) into that
#      transaction's block, then extract the full asset name + ticker/type + wrapped
#      high-amount from the block as a whole.

_HEADER_NAME_RE = re.compile(r"^Name:\s*(.+)$", re.MULTILINE)
_HEADER_STATE_RE = re.compile(r"^State/District:\s*(\S+)", re.MULTILINE)

# The lead line: optional owner code, then *anything* (the asset-name prefix, possibly
# truncated mid-word), then txn type + optional "(partial)", both dates, and the amount's low
# end (with or without an inline high end -- the high end may instead wrap to the next line).
_TXN_LEAD_RE = re.compile(
    r"""^\s*(?:(?P<owner>SP|JT|DC)\s+)?
        (?P<name_part>.+?)
        \s+(?P<txn_type>[PSE])(?P<partial>\s*\(partial\))?
        \s+(?P<txn_date>\d{2}/\d{2}/\d{4})
        \s+(?P<notif_date>\d{2}/\d{2}/\d{4})
        \s+(?P<amount_low>\$[\d,]+)
        \s*-\s*(?P<amount_high_inline>\$[\d,]+)?
        \s*$
    """,
    re.VERBOSE,
)

# A standalone "$xxx,xxx" continuation line -- the wrapped high end of an amount range.
_AMOUNT_CONT_RE = re.compile(r"^\s*\$([\d,]+)\s*$")

# Ticker/CUSIP + asset-type tag, e.g. "(FERG) [ST]" or "(91282CGH8) [GS]". May appear with a
# wrapped $ amount trailing on the same physical line (see DocID 20034133's Treasury Note).
_TICKER_TYPE_RE = re.compile(r"\(([A-Za-z0-9./]{1,15})\)\s*\[([A-Z]{1,4})\]")

# Metadata line markers: "F ... S : New", "S ... O : <broker/account>", "D ... : <free text>".
_META_LINE_RE = re.compile(r"^\s*[FSD]\s+[A-Z]?\s*:\s")


def _is_table_header_junk(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s in ("Cap.", "Gains >", "$200?", "Type", "Date", "ID"):
        return True
    # The "ID  Owner Asset ... Transaction Date ... Notification Amount ... Cap." header row
    # repeats verbatim on every PDF page break, with layout-dependent spacing -- match by
    # substring rather than exact line since spacing shifts filing to filing.
    if s.startswith("ID") and "Owner Asset" in s:
        return True
    if "Transaction Date" in s and "Notification" in s:
        return True
    # The multi-line page-break header wraps its column labels across up to 3 physical
    # lines: the first is caught above; the remaining two carry only column-label words
    # ("Type"/"Date" together, or "Gains >"/"$200?") with no transaction data, and would
    # otherwise be folded into whatever transaction block precedes them.
    if "Gains >" in s or "$200?" in s:
        return True
    if s.startswith("Type") and s.rstrip().endswith("Date"):
        return True
    return False


def _is_meta_line(line: str) -> bool:
    return bool(_META_LINE_RE.match(line))


def _is_lead_line(line: str) -> bool:
    return bool(_TXN_LEAD_RE.match(line.strip()))


def _extract_header(text: str) -> dict:
    name_m = _HEADER_NAME_RE.search(text)
    state_m = _HEADER_STATE_RE.search(text)
    return {
        "filer_name": name_m.group(1).strip() if name_m else None,
        "state_dst": state_m.group(1).strip() if state_m else None,
    }


def _dollar_to_float(s: str) -> float | None:
    s = s.strip().lstrip("$").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _transaction_section(text: str) -> list[str]:
    """Lines between the table header and the asset-type-codes footnote / certification
    section, with page-break header repeats and blank lines stripped out."""
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if "Notification" in line and "Date" in line:
            start = i + 1
            break
    end = len(lines)
    for i, line in enumerate(lines):
        if "complete list of asset type abbreviations" in line or line.strip().startswith("I CERTIFY"):
            end = i
            break

    out = []
    for line in lines[start:end]:
        if _is_table_header_junk(line):
            continue
        out.append(line)
    return out


def _split_into_blocks(lines: list[str]) -> list[list[str]]:
    """Group transaction-section lines into one block per transaction, anchored at each lead
    line. A wrapped amount-high line right after a lead line belongs to that same block; every
    following non-lead, non-meta line up to the next lead line is asset-name/ticker/type
    continuation and also belongs to the block. Metadata lines (F/S/D ...) are INCLUDED in the
    block (harmless -- field extraction below only pulls what it recognizes) so that free-text
    "D" description continuations (which can themselves wrap across lines with no marker) are
    consumed rather than mis-read as the start of the next transaction.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    in_meta = False

    for line in lines:
        if not line.strip():
            continue
        if _is_lead_line(line):
            current = [line]
            blocks.append(current)
            in_meta = False
            continue
        if current is None:
            continue  # stray line before any lead line -- ignore
        if _is_meta_line(line):
            in_meta = True
            current.append(line)
            continue
        if in_meta:
            # Continuation of a free-text metadata line (e.g. wrapped "D: ..." description).
            # Only re-open as transaction data if it actually looks like ticker/type/amount
            # continuation directly after the lead (rare) -- otherwise stays metadata.
            current.append(line)
            continue
        current.append(line)

    return blocks


def _parse_block(block: list[str]) -> dict | None:
    lead = block[0]
    m = _TXN_LEAD_RE.match(lead.strip())
    if not m:
        return None

    amount_low = _dollar_to_float(m.group("amount_low"))
    amount_high = _dollar_to_float(m.group("amount_high_inline")) if m.group("amount_high_inline") else None

    # Continuation lines: everything after the lead line, up to (not including) the first
    # metadata line -- that's where the wrapped asset name / ticker / type / wrapped amount
    # live.
    cont_lines: list[str] = []
    for line in block[1:]:
        if _is_meta_line(line):
            break
        cont_lines.append(line)

    # Wrapped high-amount: a continuation line that's ONLY a dollar amount.
    if amount_high is None:
        for line in cont_lines:
            am = _AMOUNT_CONT_RE.match(line)
            if am:
                amount_high = _dollar_to_float(am.group(0))
                break

    # Full asset-name text = lead's name_part + every continuation line that isn't a bare
    # wrapped amount, joined with spaces.
    name_parts = [m.group("name_part").strip()]
    for line in cont_lines:
        if _AMOUNT_CONT_RE.match(line):
            continue
        name_parts.append(line.strip())
    full_name_text = " ".join(p for p in name_parts if p)

    ticker, asset_type = None, None
    tm = _TICKER_TYPE_RE.search(full_name_text)
    if tm:
        ticker, asset_type = tm.group(1), tm.group(2)
        asset_name = full_name_text[: tm.start()].strip()
    else:
        asset_name = full_name_text.strip()

    return {
        "owner_code": m.group("owner"),
        "asset_name": asset_name,
        "ticker": ticker,
        "asset_type": asset_type,
        "transaction_type": m.group("txn_type"),
        "partial": bool(m.group("partial")),
        "transaction_date": m.group("txn_date"),
        "notification_date": m.group("notif_date"),
        "amount_low": amount_low,
        "amount_high": amount_high,
    }


def parse_ptr_text(text: str) -> list[dict]:
    """Parse one PTR's pdftotext -layout output into transaction dicts (no metadata attached
    -- fetch_new merges in filer/DocID/source_url). Returns [] for unparseable/no-transaction
    text (e.g. a scanned PDF with no extractable text layer)."""
    lines = _transaction_section(text)
    blocks = _split_into_blocks(lines)
    out = []
    for block in blocks:
        rec = _parse_block(block)
        if rec is not None:
            out.append(rec)
    return out


def parse_ptr(
    pdf_bytes: bytes,
    *,
    doc_id: str,
    year: int,
    source_url: str | None = None,
    fetched_at: str | None = None,
    filing_date: str | None = None,
    index_last: str | None = None,
    index_first: str | None = None,
    index_state_dst: str | None = None,
) -> list[dict]:
    """Extract text + parse one PTR PDF into normalized per-transaction records.

    Falls back to the annual index's Last/First/StateDst if the PDF's own header text
    doesn't parse cleanly (defense in depth -- the index is separately, reliably structured
    XML, so it's a good backstop for filer identity)."""
    text = pdf_to_text(pdf_bytes)
    header = _extract_header(text)
    txns = parse_ptr_text(text)

    filer_name = header["filer_name"] or " ".join(p for p in (index_first, index_last) if p) or None
    state_dst = header["state_dst"] or index_state_dst

    now = fetched_at or datetime.now(timezone.utc).isoformat()
    records = []
    for tx in txns:
        records.append({
            "doc_id": doc_id,
            "year": year,
            "filer_name": filer_name,
            "state_dst": state_dst,
            "filing_date": filing_date,
            "source_url": source_url,
            "fetched_at": now,
            "owner_code": tx["owner_code"],
            "asset_name": tx["asset_name"],
            "ticker": tx["ticker"],
            "asset_type": tx["asset_type"],
            "transaction_type": tx["transaction_type"],
            "partial": tx["partial"],
            "transaction_date": tx["transaction_date"],
            "notification_date": tx["notification_date"],
            "amount_low": tx["amount_low"],
            "amount_high": tx["amount_high"],
        })
    return records


# --- fetch_new: the runner contract --------------------------------------------------------

def _current_and_prior_year() -> tuple[int, int]:
    today = datetime.now(timezone.utc).date()
    return today.year, today.year - 1


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: current (and, near the year boundary, prior) year's filer index ->
    filter to FilingType == 'P' -> fetch/parse each not-yet-seen DocID's PTR PDF.

    Year-boundary handling: the Clerk's office keys the index/PDF paths by calendar year of
    filing, but PTRs can be filed in January for transactions/notifications dated in the
    prior year (45-day filing window), and the current year's index may lag briefly after
    Jan 1. We always check the current year; in January we ALSO check the prior year, since
    a late-arriving prior-year PTR is still fetchable from last year's zip/PDF path. Outside
    January this second check is skipped as unnecessary work.
    """
    seen = state.get("seen", [])
    seen_set = set(seen)
    now = datetime.now(timezone.utc).isoformat()

    cur_year, prior_year = _current_and_prior_year()
    years = [cur_year]
    if datetime.now(timezone.utc).month == 1:
        years.append(prior_year)

    new_records: list[dict] = []
    for year in years:
        try:
            filings = fetch_filer_index(year, c)
        except Exception as e:  # noqa: BLE001 — one bad year's index must not stop the other
            print(f"[producer:{SOURCE_ID}] index fetch failed for {year}: {e}", file=sys.stderr)
            continue

        for f in filings:
            if f["filing_type"] != "P":
                continue
            doc_id = f["doc_id"]
            if not doc_id or doc_id in seen_set:
                continue
            try:
                pdf_bytes = fetch_ptr_pdf(year, doc_id, c)
                if pdf_bytes is None:
                    print(f"[producer:{SOURCE_ID}] skip {doc_id}: not a PDF (bad url/missing)",
                          file=sys.stderr)
                    continue
                recs = parse_ptr(
                    pdf_bytes,
                    doc_id=doc_id,
                    year=year,
                    source_url=_ptr_pdf_url(year, doc_id),
                    fetched_at=now,
                    filing_date=f.get("filing_date"),
                    index_last=f.get("last"),
                    index_first=f.get("first"),
                    index_state_dst=f.get("state_dst"),
                )
                if not recs:
                    print(f"[producer:{SOURCE_ID}] {doc_id}: 0 transactions parsed "
                          f"(possibly a scanned/no-text-layer filing)", file=sys.stderr)
                new_records.extend(recs)
            except Exception as e:  # noqa: BLE001 — one bad filing must not stop the batch
                print(f"[producer:{SOURCE_ID}] skip {doc_id}: {e}", file=sys.stderr)
            finally:
                seen.append(doc_id)
                seen_set.add(doc_id)

    state["seen"] = seen[-SEEN_CAP:]
    return new_records, state
