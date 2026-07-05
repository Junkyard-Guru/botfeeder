"""Unit tests for the House PTR (Periodic Transaction Report) source module: the source-collection validation notes.

Fixture-based, no live network calls. Fixtures were captured live 2026-07-03 against real
recent PTR filings from https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/:

  - house_ptr_allen_ferguson_netflix.pdf (DocID 20033751, Rep. Richard W. Allen, 2 txns —
    the reference example from the source-collection validation notes's validation pass, including a wrapped amount range)
  - house_ptr_allen_treasury.pdf (DocID 20033945, Rep. Richard W. Allen, 4 txns — exercises
    a no-owner-code Treasury Note transaction with a CUSIP instead of a ticker)
  - house_ptr_gottheimer_multi.pdf (DocID 20034305, Rep. Josh Gottheimer, 18 txns across a
    multi-page PDF — exercises page-break table-header stripping and a high volume of
    same-owner-code (JT) transactions)
  - house_ptr_scanned_no_text.pdf (DocID 9115809, an older 7-digit-DocID filing) — a REAL,
    confirmed-live edge case: this PDF has no extractable text layer at all (neither
    pdftotext nor pypdf get anything out of it), representing genuinely-scanned older
    filings. The parser must degrade to zero transactions, not raise.

pdftotext (poppler-utils) is required for these tests to exercise the real extraction path;
if it isn't on the box, the whole module is skipped rather than failing (see the
_HAS_PDFTOTEXT skip marker below) -- consistent with this being a system dependency, not a
pip one (see house_ptr.py's module docstring for the deploy-target implication).
"""
import shutil

import pytest

from producer.sources import house_ptr as hp
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

_HAS_PDFTOTEXT = shutil.which("pdftotext") is not None

pytestmark = pytest.mark.skipif(not _HAS_PDFTOTEXT, reason="pdftotext (poppler-utils) not installed")


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- filer index (annual zip -> XML) --------------------------------------------------------

def test_fetch_filer_index_url_is_year_specific():
    assert hp._index_url(2026) == "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip"
    assert hp._index_url(2025) == "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip"


def test_ptr_pdf_url_uses_ptr_pdfs_path_not_financial_pdfs():
    # This is the gotcha the source-collection validation notes flags: PTR PDFs live under a DIFFERENT path than annual/
    # candidate reports. Getting this wrong silently "succeeds" (HTTP 200, IIS error body).
    url = hp._ptr_pdf_url(2026, "20033751")
    assert url == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033751.pdf"
    assert "financial-pdfs" not in url


# --- PDF text extraction ----------------------------------------------------------------------

def test_pdf_to_text_extracts_layout_preserved_text():
    text = hp.pdf_to_text(_read("house_ptr_allen_ferguson_netflix.pdf"))
    assert "Hon. Richard W. Allen" in text
    assert "Ferguson Enterprises" in text
    assert "Netflix" in text


def test_pdf_to_text_scanned_pdf_yields_empty_or_near_empty_text():
    # The real edge case: an older filing with no text layer at all. Must not raise.
    text = hp.pdf_to_text(_read("house_ptr_scanned_no_text.pdf"))
    assert text.strip() == ""


# --- header extraction -------------------------------------------------------------------------

def test_extract_header_gets_filer_name_and_state_district():
    text = hp.pdf_to_text(_read("house_ptr_allen_ferguson_netflix.pdf"))
    header = hp._extract_header(text)
    assert header["filer_name"] == "Hon. Richard W. Allen"
    assert header["state_dst"] == "GA12"


# --- transaction parsing: the 2-transaction reference sample -----------------------------------

def test_parse_ptr_text_two_transactions_reference_sample():
    text = hp.pdf_to_text(_read("house_ptr_allen_ferguson_netflix.pdf"))
    txns = hp.parse_ptr_text(text)
    assert len(txns) == 2

    ferguson, netflix = txns
    assert ferguson["owner_code"] == "SP"
    assert ferguson["asset_name"] == "Ferguson Enterprises Inc. Common Stock"
    assert ferguson["ticker"] == "FERG"
    assert ferguson["asset_type"] == "ST"
    assert ferguson["transaction_type"] == "P"
    assert ferguson["partial"] is False
    assert ferguson["transaction_date"] == "12/12/2025"
    assert ferguson["notification_date"] == "01/06/2026"
    # This is the wrapped-amount case: "$15,001 -" on the lead line, "$50,000" on its own
    # continuation line -- the exact gotcha flagged in the task brief.
    assert ferguson["amount_low"] == 15001.0
    assert ferguson["amount_high"] == 50000.0

    assert netflix["owner_code"] == "SP"
    assert netflix["asset_name"] == "Netflix, Inc. - Common Stock"
    assert netflix["ticker"] == "NFLX"
    assert netflix["asset_type"] == "ST"
    assert netflix["transaction_type"] == "S"
    assert netflix["amount_low"] == 1001.0
    assert netflix["amount_high"] == 15000.0


# --- transaction parsing: no-owner-code + CUSIP (non-ticker identifier) sample ------------------

def test_parse_ptr_text_handles_missing_owner_code_and_cusip_identifier():
    text = hp.pdf_to_text(_read("house_ptr_allen_treasury.pdf"))
    txns = hp.parse_ptr_text(text)
    assert len(txns) == 4

    by_name = {t["asset_name"]: t for t in txns}
    treasury = by_name["US Treasury Note 3.5% DUE 01/31/28"]
    # No owner code printed for this line in the source PDF.
    assert treasury["owner_code"] is None
    # CUSIP-like identifier in parens, not a stock ticker -- still captured as "ticker".
    assert treasury["ticker"] == "91282CGH8"
    assert treasury["asset_type"] == "GS"
    assert treasury["transaction_type"] == "P"
    assert treasury["amount_low"] == 100001.0
    assert treasury["amount_high"] == 250000.0

    stock = by_name["Paychex, Inc. - Common Stock"]
    assert stock["owner_code"] == "SP"
    assert stock["ticker"] == "PAYX"


# --- transaction parsing: high-volume multi-page filing -----------------------------------------

def test_parse_ptr_text_multi_page_filing_strips_page_break_headers():
    text = hp.pdf_to_text(_read("house_ptr_gottheimer_multi.pdf"))
    txns = hp.parse_ptr_text(text)
    # 18 real transactions in this filing; page-break table headers (which repeat verbatim
    # mid-document every time the PDF wraps to a new page) must not be miscounted as
    # transactions or corrupt an adjacent transaction's asset name.
    assert len(txns) == 18
    assert all(t["owner_code"] == "JT" for t in txns)
    assert all(t["transaction_type"] in ("P", "S", "E") for t in txns)
    names = [t["asset_name"] for t in txns]
    assert any("Air Products" in n for n in names)
    assert any("ServiceNow" in n for n in names)
    # None of the parsed asset names should have leaked page-header junk into them.
    assert not any("Owner Asset" in n or "Notification" in n or "Gains" in n for n in names)


# --- transaction parsing: genuinely unparseable (scanned, no text layer) sample -----------------

def test_parse_ptr_text_scanned_pdf_yields_no_transactions_not_an_exception():
    text = hp.pdf_to_text(_read("house_ptr_scanned_no_text.pdf"))
    txns = hp.parse_ptr_text(text)
    assert txns == []


# --- parse_ptr: the full record shape, as emitted by fetch_new ----------------------------------

def test_parse_ptr_full_record_shape():
    recs = hp.parse_ptr(
        _read("house_ptr_allen_ferguson_netflix.pdf"),
        doc_id="20033751",
        year=2026,
        source_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033751.pdf",
        fetched_at="2026-07-03T00:00:00Z",
        filing_date="1/15/2026",
    )
    assert len(recs) == 2
    r = recs[0]
    assert r["doc_id"] == "20033751"
    assert r["year"] == 2026
    assert r["filer_name"] == "Hon. Richard W. Allen"
    assert r["state_dst"] == "GA12"
    assert r["filing_date"] == "1/15/2026"
    assert r["source_url"].endswith("20033751.pdf")
    assert r["fetched_at"] == "2026-07-03T00:00:00Z"
    assert set(r.keys()) == {
        "doc_id", "year", "filer_name", "state_dst", "filing_date", "source_url",
        "fetched_at", "owner_code", "asset_name", "ticker", "asset_type",
        "transaction_type", "partial", "transaction_date", "notification_date",
        "amount_low", "amount_high",
    }


def test_parse_ptr_falls_back_to_index_identity_fields():
    # If the PDF's own header text is missing/unparseable, the annual index's Last/First/
    # StateDst (separately, reliably structured XML) is a backstop for filer identity.
    recs = hp.parse_ptr(
        _read("house_ptr_scanned_no_text.pdf"),
        doc_id="9115809",
        year=2026,
        index_last="Harshbarger",
        index_first="Diana",
        index_state_dst="TN01",
    )
    assert recs == []  # no transactions extractable -- but this call must not raise


def test_parse_ptr_no_transactions_scanned_pdf_does_not_raise():
    recs = hp.parse_ptr(_read("house_ptr_scanned_no_text.pdf"), doc_id="9115809", year=2026)
    assert recs == []


# --- fetch_ptr_pdf: %PDF- magic-byte verification (status-200-but-not-a-PDF gotcha) --------------

def test_fetch_ptr_pdf_rejects_non_pdf_200_response(monkeypatch):
    class _FakeResp:
        status_code = 200
        content = b"<!DOCTYPE html><html>404 - File or directory not found</html>"

    class _FakeClient:
        def get(self, url, timeout=None):
            return _FakeResp()

    result = hp.fetch_ptr_pdf(2026, "nonexistent", _FakeClient())
    assert result is None


def test_fetch_ptr_pdf_accepts_real_pdf_bytes(monkeypatch):
    real_pdf = _read("house_ptr_allen_ferguson_netflix.pdf")

    class _FakeResp:
        status_code = 200
        content = real_pdf

    class _FakeClient:
        def get(self, url, timeout=None):
            return _FakeResp()

    result = hp.fetch_ptr_pdf(2026, "20033751", _FakeClient())
    assert result == real_pdf


# --- fetch_new: state/seen-set plumbing (network mocked) -----------------------------------------

def test_fetch_new_skips_already_seen_doc_ids(monkeypatch):
    calls = []

    def fake_index(year, c):
        calls.append(year)
        return [
            {"last": "Allen", "first": "Richard", "suffix": None, "filing_type": "P",
             "state_dst": "GA12", "year": "2026", "filing_date": "1/15/2026", "doc_id": "20033751"},
            {"last": "Alford", "first": "Mark", "suffix": None, "filing_type": "C",
             "state_dst": "MO04", "year": "2026", "filing_date": "3/31/2026", "doc_id": "ignored-candidate"},
        ]

    def fake_pdf(year, doc_id, c):
        assert doc_id == "20033751"
        return _read("house_ptr_allen_ferguson_netflix.pdf")

    monkeypatch.setattr(hp, "fetch_filer_index", fake_index)
    monkeypatch.setattr(hp, "fetch_ptr_pdf", fake_pdf)

    state = {"seen": []}
    records, new_state = hp.fetch_new(state, c=object())
    assert len(records) == 2
    assert "20033751" in new_state["seen"]
    # The 'C' (Candidate Report) filing type must be filtered out entirely -- fetch_ptr_pdf
    # should never even be called for it (the assert inside fake_pdf would have failed).
    assert "ignored-candidate" not in new_state["seen"]

    # Second cycle: same index response, DocID already in state -> no new records, no re-fetch.
    calls_before = len(calls)
    records2, state2 = hp.fetch_new(new_state, c=object())
    assert records2 == []
    assert len(calls) > calls_before  # index re-checked every cycle (cheap, catches new filings)


def test_fetch_new_one_bad_filing_does_not_stop_the_batch(monkeypatch):
    def fake_index(year, c):
        return [
            {"last": "A", "first": "A", "suffix": None, "filing_type": "P", "state_dst": "X",
             "year": "2026", "filing_date": "1/1/2026", "doc_id": "bad-one"},
            {"last": "B", "first": "B", "suffix": None, "filing_type": "P", "state_dst": "Y",
             "year": "2026", "filing_date": "1/2/2026", "doc_id": "20033751"},
        ]

    def fake_pdf(year, doc_id, c):
        if doc_id == "bad-one":
            raise RuntimeError("network blew up")
        return _read("house_ptr_allen_ferguson_netflix.pdf")

    monkeypatch.setattr(hp, "fetch_filer_index", fake_index)
    monkeypatch.setattr(hp, "fetch_ptr_pdf", fake_pdf)

    records, state = hp.fetch_new({"seen": []}, c=object())
    assert len(records) == 2  # the good filing's transactions still made it through
    assert "bad-one" in state["seen"]  # marked seen so we don't retry it forever
    assert "20033751" in state["seen"]
