"""Server tests — data routes + the conditional paywall. Spec: docs/02, docs/03.

Verifies the hard rule that an empty result is NEVER billed: even with x402 enabled, a
request that yields no data returns 200 (free), and only a data-bearing request issues 402.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from producer.writer import write_snapshot
from server import app as app_module
from server import payments
from server.app import app

client = TestClient(app)


def _rec(ticker, code, date="2026-06-20"):
    return {
        "issuer": {"ticker": ticker, "name": f"{ticker} Inc", "cik": "1"},
        "insider": {"name": "Jane Doe", "cik": "2", "roles": ["officer:CEO"]},
        "transaction": {
            "table": "non_derivative", "code": code, "code_meaning": "x",
            "discretionary": True, "transaction_date": date, "shares": 100,
            "price": 10.0, "rule_10b5_1": False,
        },
        "source_url": "https://www.sec.gov/x", "filing_id": "acc-1",
        "document_type": "4", "is_amendment": False,
    }


@pytest.fixture(autouse=True)
def snapshot(tmp_path, monkeypatch):
    records = [_rec("AAPL", "P"), _rec("AAPL", "S"), _rec("MSFT", "S")]
    write_snapshot({"generated_at": "2026-06-28T00:00:00Z", "count": len(records),
                    "records": records}, tmp_path)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(payments, "MODE", "off")  # dev default unless a test flips it
    yield


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_meta_live_counts():
    m = client.get("/v1/meta").json()
    assert m["live"]["record_count"] == 3
    assert m["live"]["distinct_issuers"] == 2
    assert m["x402_enabled"] is False
    assert m["x402_mode"] == "off"
    assert m["price_usd_per_record"] == 0.006


def test_latest_returns_data_free_in_dev():
    r = client.get("/v1/insider/latest").json()
    assert r["count"] == 3


def test_latest_codes_filter():
    r = client.get("/v1/insider/latest?codes=P").json()
    assert r["count"] == 1 and r["records"][0]["transaction"]["code"] == "P"


def test_bulk_returns_everything():
    r = client.get("/v1/insider/bulk").json()
    assert r["count"] == 3  # whole snapshot, no filter


def test_meta_exposes_bulk_price():
    m = client.get("/v1/meta").json()
    assert m["bulk_price_usd_per_call"] == 5.00
    assert m["endpoints"]["bulk"] == "/v1/insider/bulk"


def test_sample_is_free_even_when_paywalled(monkeypatch):
    """The proof rung must serve one record FREE even with x402 on — it's how a buyer verifies us."""
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "WALLET", "0x3A56664695c06A6a36c97fe3029303f3Feed4bFb")
    r = client.get("/v1/insider/sample")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 1 and len(j["records"]) == 1 and j["tier"] == "free-sample"


def test_meta_exposes_tier_ladder():
    m = client.get("/v1/meta").json()
    assert m["endpoints"]["sample"] == "/v1/insider/sample"
    t = m["tiers"]
    assert t["free_sample"]["price_usd"] == 0.0 and t["free_sample"]["status"] == "live"
    assert t["lookup"]["price_usd_per_record"] == 0.006
    assert t["bulk"]["price_usd"] == 5.00
    assert t["scored_insider_signal"]["status"] == "roadmap" and t["cluster"]["status"] == "roadmap"
    assert t["signals_cross_source"]["status"] == "live"


def test_by_ticker_filters():
    r = client.get("/v1/insider/AAPL").json()
    assert r["count"] == 2 and all(x["issuer"]["ticker"] == "AAPL" for x in r["records"])


def test_unknown_ticker_is_empty_and_free():
    resp = client.get("/v1/insider/ZZZZ")
    assert resp.status_code == 200 and resp.json()["count"] == 0


# --- the conditional paywall (trust mode: gate logic without crypto) ---

def test_data_request_requires_payment_in_trust_mode(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "trust")
    resp = client.get("/v1/insider/latest")
    assert resp.status_code == 402
    assert resp.json()["accepts"][0]["asset"] == "USDC"


def test_paid_request_is_served_in_trust_mode(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "trust")
    resp = client.get("/v1/insider/latest", headers={"X-PAYMENT": "proof"})
    assert resp.status_code == 200 and resp.json()["count"] == 3


def test_empty_result_is_free_even_when_paywalled(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "trust")
    resp = client.get("/v1/insider/ZZZZ")  # no data -> must NOT 402
    assert resp.status_code == 200 and resp.json()["count"] == 0


# --- x402 mode: the real library builds a spec-correct 402 (offline) ---

def test_x402_mode_builds_library_402(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "WALLET", "0x3A56664695c06A6a36c97fe3029303f3Feed4bFb")
    resp = client.get("/v1/insider/latest")  # has data, no payment -> library 402
    assert resp.status_code == 402
    body = resp.json()
    assert "accepts" in body
    assert payments.WALLET in str(body)  # our receiving address is in the requirements


def test_x402_402_carries_bazaar_discovery(monkeypatch):
    """The 402 must self-describe so a facilitator can index us (resource + bazaar extension)."""
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "WALLET", "0x3A56664695c06A6a36c97fe3029303f3Feed4bFb")
    body = str(client.get("/v1/insider/latest").json())
    assert "bazaar" in body.lower()        # declare_discovery_extension rode along
    assert payments.SERVICE_NAME in body   # ResourceInfo service_name present
