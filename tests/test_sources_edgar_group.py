"""Unit tests for the 3 new EDGAR source modules (Form D, 8-K, 13F-HR): the source-collection validation notes.

Fixture-based, no live network calls — matches tests/test_parser.py's convention. Fixtures
were captured live 2026-07-03 against real recent filings:

  - form_d_atom_sample.xml / form_d_sample.xml (primary_doc.xml)
  - form_8k_atom_sample.xml
  - form_13f_atom_sample.xml / form_13f_cover_sample.xml (primary_doc.xml) /
    form_13f_infotable_sample.xml (the per-quarter-named information table doc)
"""
from pathlib import Path

from producer.edgar import _parse_edgar_atom
from producer.sources.form_8k import normalize as normalize_8k
from producer.sources.form_8k import _parse_items, _parse_title
from producer.sources.form_d import parse_form_d
from producer.sources.form_13f import parse_13f_info_table, parse_cover

FIXTURES = Path(__file__).parent / "fixtures"


# --- Form D ---------------------------------------------------------------------------------

def test_form_d_atom_parses_entries():
    # EDGAR's type=D 'getcurrent' firehose can interleave a few related forms (e.g. DEFA14A
    # amendments filed alongside a D/A) — assert D/D-A dominate rather than 100% purity.
    entries = _parse_edgar_atom((FIXTURES / "form_d_atom_sample.xml").read_bytes())
    assert entries
    assert all(e["accession"] for e in entries)
    d_forms = [e for e in entries if e["form"] in ("D", "D/A")]
    assert len(d_forms) >= len(entries) / 2


def test_form_d_primary_doc_parses_issuer_and_offering():
    rec = parse_form_d(
        (FIXTURES / "form_d_sample.xml").read_bytes(),
        source_url="https://www.sec.gov/Archives/edgar/data/2132077/000213207726000001/primary_doc.xml",
        filing_id="0002132077-26-000001",
        filed_at="2026-07-02",
        fetched_at="2026-07-03T00:00:00Z",
    )
    assert rec["issuer"]["cik"] == "0002132077"
    assert "Stonepeak" in rec["issuer"]["name"]
    assert rec["issuer"]["state_of_incorporation"] == "DELAWARE"
    assert "3C" in rec["offering"]["exemptions"]
    assert rec["offering"]["total_offering_amount"] == "Indefinite"
    assert rec["offering"]["total_remaining"] == "Indefinite"
    assert rec["filing_id"] == "0002132077-26-000001"
    assert rec["source_url"].startswith("https://www.sec.gov/Archives/edgar/")


def test_form_d_degrades_gracefully_on_missing_offering():
    rec = parse_form_d(b"<edgarSubmission><primaryIssuer><cik>1</cik></primaryIssuer></edgarSubmission>")
    assert rec["issuer"]["cik"] == "1"
    assert rec["offering"]["exemptions"] == []
    assert rec["offering"]["total_offering_amount"] is None


# --- 8-K -------------------------------------------------------------------------------------

def test_8k_atom_summary_field_present():
    entries = _parse_edgar_atom((FIXTURES / "form_8k_atom_sample.xml").read_bytes())
    assert entries
    assert all(e["form"] == "8-K" for e in entries)
    assert any(e["summary"] and "Item" in e["summary"] for e in entries)
    assert all(e["title"] for e in entries)


def test_8k_title_parses_name_and_cik():
    name, cik = _parse_title("8-K - AppTech Payments Corp. (0001070050) (Filer)")
    assert name == "AppTech Payments Corp."
    assert cik == "0001070050"


def test_8k_items_parsed_from_summary():
    summary = (
        " <b>Filed:</b> 2026-07-02 <b>AccNo:</b> 0001683168-26-005262 <b>Size:</b> 270 KB\n"
        "<br>Item 1.01: Entry into a Material Definitive Agreement\n"
        "<br>Item 9.01: Financial Statements and Exhibits\n"
    )
    items = _parse_items(summary)
    assert items == [
        {"code": "1.01", "description": "Entry into a Material Definitive Agreement"},
        {"code": "9.01", "description": "Financial Statements and Exhibits"},
    ]


def test_8k_normalize_real_fixture():
    entries = _parse_edgar_atom((FIXTURES / "form_8k_atom_sample.xml").read_bytes())
    rec = normalize_8k(entries[0], fetched_at="2026-07-03T00:00:00Z")
    assert rec["issuer"]["name"]
    assert rec["issuer"]["cik"]
    assert rec["items"]
    assert rec["filing_id"] == entries[0]["accession"]
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"


# --- 13F-HR ----------------------------------------------------------------------------------

def test_13f_atom_no_item_codes():
    entries = _parse_edgar_atom((FIXTURES / "form_13f_atom_sample.xml").read_bytes())
    assert entries
    assert all(e["form"] == "13F-HR" for e in entries)


def test_13f_cover_page_parses_filer():
    cover = parse_cover((FIXTURES / "form_13f_cover_sample.xml").read_bytes())
    assert cover["filer_cik"] == "0001762716"
    assert "BURKETT" in cover["filer_name"]
    assert cover["period_of_report"] == "06-30-2026"


def test_13f_info_table_flattens_one_record_per_holding():
    recs = parse_13f_info_table(
        (FIXTURES / "form_13f_infotable_sample.xml").read_bytes(),
        source_url="https://www.sec.gov/Archives/edgar/data/1762716/000176271626000003/2026qtr2submissionapr2026.xml",
        filing_id="0001762716-26-000003",
        filed_at="2026-07-02",
        fetched_at="2026-07-03T00:00:00Z",
        filer_cik="0001762716",
        filer_name="BURKETT FINANCIAL SERVICES, LLC",
    )
    assert len(recs) == 266
    first = recs[0]
    assert first["holding"]["name_of_issuer"] == "Schwab US Large Cap Growth ETF"
    assert first["holding"]["cusip"] == "808524300"
    assert first["holding"]["value"] == 54654995.0
    assert first["holding"]["shares"] == 1615100.0
    assert first["holding"]["investment_discretion"] == "SOLE"
    assert first["filer"]["cik"] == "0001762716"
    # all rows share the same filing-level metadata
    assert all(r["filing_id"] == "0001762716-26-000003" for r in recs)
    assert all(r["filer"]["name"] == "BURKETT FINANCIAL SERVICES, LLC" for r in recs)


def test_13f_info_table_handles_missing_optional_fields():
    xml = b"""<?xml version="1.0"?>
<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <ns1:infoTable>
    <ns1:nameOfIssuer>Test Co</ns1:nameOfIssuer>
    <ns1:cusip>123456789</ns1:cusip>
    <ns1:value>100</ns1:value>
    <ns1:shrsOrPrnAmt>
      <ns1:sshPrnamt>10</ns1:sshPrnamt>
      <ns1:sshPrnamtType>SH</ns1:sshPrnamtType>
    </ns1:shrsOrPrnAmt>
    <ns1:investmentDiscretion>SOLE</ns1:investmentDiscretion>
  </ns1:infoTable>
</ns1:informationTable>"""
    recs = parse_13f_info_table(xml, filing_id="f")
    assert len(recs) == 1
    assert recs[0]["holding"]["put_call"] is None
    assert recs[0]["holding"]["voting_authority_sole"] is None
