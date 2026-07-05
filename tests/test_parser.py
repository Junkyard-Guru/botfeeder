"""Parser correctness tests — the moat, proven against a REAL filing. Spec: docs/03 §moat.

Fixture: tests/fixtures/form4_lennox_10b51.xml — a real Form 4 (Lennox International,
insider Gary Bedard, filed 2023-12-12). 24 non-derivative transactions, document-level
Rule 10b5-1 flag set, footnotes present. A passing suite here is the literal
substantiation of the "real processing" + "medium-quality but correct" claims (docs/07).
"""
from pathlib import Path

import pytest

from producer.parser import TXN_CODES, parse_form4

FIXTURE = Path(__file__).parent / "fixtures" / "form4_lennox_10b51.xml"


@pytest.fixture(scope="module")
def records():
    return parse_form4(
        FIXTURE.read_bytes(),
        source_url="https://www.sec.gov/Archives/edgar/data/1719836/000112760223029453/form4.xml",
        filing_id="0001127602-23-029453",
        filed_at="2023-12-12",
        fetched_at="2026-06-28T00:00:00Z",
    )


# --- code table (pure unit) ---

def test_open_market_buy_is_discretionary():
    assert TXN_CODES["P"] == ("open_market_purchase", True)


def test_tax_withholding_is_not_discretionary():
    assert TXN_CODES["F"][1] is False


# --- real-filing assertions ---

def test_transaction_count(records):
    # 21 sales + 2 tax-withholdings + 1 award = 24, single reporting owner.
    assert len(records) == 24


def test_issuer_resolved(records):
    iss = records[0]["issuer"]
    assert iss["ticker"] == "LII"
    assert "LENNOX" in iss["name"].upper()
    assert iss["cik"] == "0001069202"


def test_insider_resolved(records):
    ins = records[0]["insider"]
    assert ins["name"] == "Bedard Gary S"
    assert ins["is_officer"] is True
    assert ins["is_director"] is False
    assert any(r.startswith("officer:") for r in ins["roles"])


def test_transaction_code_distribution(records):
    codes = [r["transaction"]["code"] for r in records]
    assert codes.count("S") == 21
    assert codes.count("F") == 2
    assert codes.count("A") == 1


def test_sales_are_discretionary_and_classified(records):
    sales = [r for r in records if r["transaction"]["code"] == "S"]
    assert all(s["transaction"]["discretionary"] is True for s in sales)
    assert all(s["transaction"]["code_meaning"] == "open_market_sale" for s in sales)
    assert all(s["transaction"]["acquired_disposed"] == "D" for s in sales)
    assert all(s["transaction"]["shares"] and s["transaction"]["shares"] > 0 for s in sales)


def test_tax_withholding_classified_non_discretionary(records):
    fs = [r for r in records if r["transaction"]["code"] == "F"]
    assert fs and all(f["transaction"]["discretionary"] is False for f in fs)


def test_rule_10b5_1_flag_from_document(records):
    # <aff10b5One>1</aff10b5One> at the document level -> every record flagged.
    assert all(r["transaction"]["rule_10b5_1"] is True for r in records)


def test_footnotes_linked_to_transactions(records):
    # At least one transaction references a footnote whose text names the rule.
    with_fn = [r for r in records if r["transaction"]["footnotes"]]
    assert with_fn
    texts = " ".join(f["text"] for r in with_fn for f in r["transaction"]["footnotes"])
    assert "10b5-1" in texts


def test_provenance_and_metadata_present(records):
    r = records[0]
    assert r["source_url"].startswith("https://www.sec.gov/Archives/edgar/")
    assert r["filing_id"] == "0001127602-23-029453"
    assert r["fetched_at"] == "2026-06-28T00:00:00Z"
    assert r["document_type"] == "4"
    assert r["is_amendment"] is False
    assert r["transaction"]["ownership"] in ("D", "I")
    assert r["transaction"]["shares_owned_after"] is not None
