"""Free-week promo: during an announced window every paid endpoint serves without payment,
the fact is surfaced honestly (meta + llms.txt), and free deliveries are logged distinctly
(outcome='free') without advancing the paid cadence or booking revenue.

We monkeypatch payments.is_free_now / free_until rather than reloading modules, so the shared
app instance (and its one-shot MCP session manager) is never disturbed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from producer.writer import write_snapshot
from server import app as app_module
from server import payments, volume_store
from server.app import app

client = TestClient(app)
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _rec(ticker: str) -> dict:
    return {
        "issuer": {"ticker": ticker, "name": f"{ticker} Inc", "cik": "1"},
        "insider": {"name": "Jane Doe", "cik": "2", "roles": ["officer:CEO"]},
        "transaction": {"table": "non_derivative", "code": "P", "code_meaning": "x",
                        "discretionary": True, "transaction_date": "2026-07-01",
                        "shares": 100, "price": 10.0, "rule_10b5_1": False},
        "source_url": "https://www.sec.gov/x", "filing_id": "acc-1",
        "document_type": "4", "is_amendment": False,
    }


@pytest.fixture(autouse=True)
def snapshot(tmp_path, monkeypatch):
    """A non-empty snapshot so paid endpoints actually reach the payment gate (an empty
    result is free by design and would make these tests pass for the wrong reason)."""
    records = [_rec("AAPL"), _rec("MSFT")]
    write_snapshot({"generated_at": "2026-07-01T00:00:00Z", "count": len(records),
                    "records": records}, tmp_path)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    yield


def _open_free(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "free_until", lambda: FAR_FUTURE)
    monkeypatch.setattr(payments, "is_free_now", lambda: True)


def test_is_free_now_window(monkeypatch):
    monkeypatch.setenv("FEEDFACE_FREE_UNTIL", "2099-01-01T00:00:00Z")
    assert payments.is_free_now() is True
    monkeypatch.setenv("FEEDFACE_FREE_UNTIL", "2000-01-01T00:00:00Z")
    assert payments.is_free_now() is False
    monkeypatch.delenv("FEEDFACE_FREE_UNTIL", raising=False)
    assert payments.is_free_now() is False
    monkeypatch.setenv("FEEDFACE_FREE_UNTIL", "not-a-date")
    assert payments.is_free_now() is False  # fail closed


def test_paid_endpoint_serves_free_during_window(monkeypatch, tmp_path):
    _open_free(monkeypatch)
    monkeypatch.setattr(volume_store, "DB_PATH", tmp_path / "v.db")
    r = client.get("/v1/insider/latest?limit=1")
    assert r.status_code == 200  # no 402 — served without payment


def test_meta_and_llms_announce_free(monkeypatch):
    _open_free(monkeypatch)
    promo = client.get("/v1/meta").json()["promotion"]
    assert promo["free_for_everyone"] is True
    assert promo["free_until"].startswith("2099")
    assert "FREE WEEK" in client.get("/llms.txt").text


def test_no_promo_but_data_is_free(monkeypatch):
    """No promo window, real x402 mode — data is STILL free (standing free-data policy),
    only the promo banner is absent."""
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "free_until", lambda: None)
    monkeypatch.setattr(payments, "is_free_now", lambda: False)
    assert client.get("/v1/meta").json()["promotion"]["free_for_everyone"] is False
    assert "FREE WEEK" not in client.get("/llms.txt").text
    assert client.get("/v1/insider/latest?limit=1").status_code == 200  # free data, no 402


def test_paywall_machinery_re_engages_when_free_data_off(monkeypatch):
    """Reversibility guard: flip FREE_DATA off and the paid-data gate works exactly as before —
    a data-bearing request issues a 402. (Watch stays paid regardless; see test_watch_routes.)"""
    monkeypatch.setattr(payments, "MODE", "x402")
    monkeypatch.setattr(payments, "free_until", lambda: None)
    monkeypatch.setattr(payments, "is_free_now", lambda: False)
    monkeypatch.setattr(payments, "FREE_DATA", False)
    monkeypatch.setattr(payments, "WALLET", "0x3A56664695c06A6a36c97fe3029303f3Feed4bFb")
    assert client.get("/v1/insider/latest?limit=1").status_code == 402


def test_free_delivery_logged_but_not_a_settlement(monkeypatch, tmp_path):
    _open_free(monkeypatch)
    db = tmp_path / "v.db"
    monkeypatch.setattr(volume_store, "DB_PATH", db)
    client.get("/v1/insider/latest?limit=1")
    # logged as 'free', never as a settlement, and does NOT advance the paid cadence
    assert volume_store.settled_purchases(db_path=db) == 0
    with volume_store._conn(db) as con:
        outcomes = [r["outcome"] for r in con.execute("SELECT outcome FROM calls")]
    assert "free" in outcomes
    assert "settled" not in outcomes
