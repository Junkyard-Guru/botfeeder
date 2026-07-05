"""Phase 1.5 — lock the tricky parse paths against REAL filings.

Each fixture is a real SEC Form 4 chosen to exercise one hard case the main fixture
doesn't: a derivative (options) table, a multi-owner group filing, and an amendment (4/A).
These are the paths the storefront's "audit any record" promise depends on.
"""
from pathlib import Path

import pytest

from producer.parser import parse_form4

FX = Path(__file__).parent / "fixtures"


def _parse(name):
    return parse_form4((FX / name).read_bytes(), source_url=f"https://sec.gov/{name}", filing_id=name)


# --- derivative table (Tractor Supply, TSCO) ---

@pytest.fixture(scope="module")
def deriv():
    return _parse("form4_derivative_tsco.xml")


def test_both_tables_present(deriv):
    tables = {r["transaction"]["table"] for r in deriv}
    assert "derivative" in tables and "non_derivative" in tables


def test_derivative_records_carry_derivative_fields(deriv):
    dv = [r for r in deriv if r["transaction"]["table"] == "derivative"]
    assert dv
    # Every derivative record should expose the derivative-only keys.
    for r in dv:
        t = r["transaction"]
        assert "conversion_or_exercise_price" in t
        assert "underlying_security_title" in t
        assert "expiration_date" in t


def test_derivative_issuer(deriv):
    assert deriv[0]["issuer"]["ticker"] == "TSCO"


# --- multi-owner group filing (ServiceNow, NOW) ---

@pytest.fixture(scope="module")
def multi():
    return _parse("form4_multiowner_now.xml")


def test_multiple_distinct_owners(multi):
    owners = {r["insider"]["name"] for r in multi if r["insider"]}
    assert len(owners) >= 2


def test_every_record_attributed_to_an_owner(multi):
    assert all(r["insider"] and r["insider"]["name"] for r in multi)


# --- amendment (United Fire Group, UFCS) ---

@pytest.fixture(scope="module")
def amend():
    return _parse("form4_amendment_ufcs.xml")


def test_amendment_flagged(amend):
    assert amend[0]["document_type"] == "4/A"
    assert all(r["is_amendment"] is True for r in amend)
