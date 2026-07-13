"""Cross-source /v1/signals endpoint tests (docs/13). Offline: source snapshots are written
into a tmp data dir; the fixture ticker map is injected so no mapper touches the network."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import producer.signals as signals
from producer.tickermap import TickerMap
from producer.writer import write_snapshot
from server import app as app_module
from server import payments
from server.app import app

client = TestClient(app)


def _form4_rec(ticker="AAPL"):
    return {"issuer": {"ticker": ticker, "name": f"{ticker} Inc", "cik": "1"},
            "insider": {"name": "Jane Doe", "cik": "2", "roles": []},
            "transaction": {"table": "non_derivative", "code": "P", "code_meaning": "x",
                            "discretionary": True, "transaction_date": "2026-06-20",
                            "rule_10b5_1": False},
            "fetched_at": "2026-07-01T00:00:00Z", "filing_id": "acc-1",
            "source_url": "https://www.sec.gov/x"}


@pytest.fixture(autouse=True)
def signal_world(tmp_path, monkeypatch):
    signals._TICKERS = TickerMap([{"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}])

    # Form 4 main snapshot (mapped at serve time — no stored envelope).
    write_snapshot({"generated_at": "2026-07-01T00:00:00Z", "records": [_form4_rec()]}, tmp_path)

    # One source snapshot with a stored envelope (8-K bankruptcy for AAPL)...
    write_snapshot({"generated_at": "2026-07-01T00:00:00Z", "records": [{
        "filing_id": "8k-1", "fetched_at": "2026-07-02T00:00:00Z",
        "issuer": {"cik": "320193", "name": "Apple Inc."},
        "items": [{"code": "1.03", "description": "Bankruptcy"}],
        "signal": {"taxonomy": "2026-07-03", "signal_type": "material_event",
                   "event": "bankruptcy_or_receivership", "direction": "bearish",
                   "strength": "high", "scope": {"ticker": "AAPL"}},
    }]}, tmp_path / "sources" / "sec-8k")

    # ...and one source record WITHOUT a signal (must never appear in /v1/signals output).
    write_snapshot({"generated_at": "2026-07-01T00:00:00Z", "records": [{
        "cusip": "xyz", "fetched_at": "2026-07-01T00:00:00Z"}]},
        tmp_path / "sources" / "cftc-cot")

    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(payments, "MODE", "off")
    yield
    signals._TICKERS = None


def test_sample_is_free_and_one_per_source():
    r = client.get("/v1/signals/sample").json()
    assert r["tier"] == "free-sample"
    sources = {rec["source"] for rec in r["records"]}
    assert sources == {"sec-8k", "edgar-form4-insider"}


def test_latest_merges_sources_and_serves_envelopes():
    r = client.get("/v1/signals/latest").json()
    assert r["count"] == 2
    assert all("signal" in rec for rec in r["records"])
    # newest fetched_at first: the 8-K (07-02) before the Form 4 (07-01)
    assert r["records"][0]["source"] == "sec-8k"


def test_latest_filters_by_type_direction_strength():
    assert client.get("/v1/signals/latest?types=material_event").json()["count"] == 1
    assert client.get("/v1/signals/latest?direction=bullish").json()["count"] == 1
    assert client.get("/v1/signals/latest?min_strength=high").json()["count"] == 2
    assert client.get("/v1/signals/latest?types=congress_trade").json()["count"] == 0


def test_by_ticker_merges_form4_and_8k():
    r = client.get("/v1/signals/by-ticker/aapl").json()
    assert r["ticker"] == "AAPL"
    assert r["count"] == 2
    types = {rec["signal"]["signal_type"] for rec in r["records"]}
    assert types == {"insider_trade", "material_event"}


def test_by_ticker_unknown_is_free_empty():
    r = client.get("/v1/signals/by-ticker/ZZZZ")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_signals_are_free_and_empty_never_billed_with_x402_on(monkeypatch):
    monkeypatch.setattr(payments, "MODE", "trust")
    assert client.get("/v1/signals/by-ticker/ZZZZ").status_code == 200  # empty -> free
    assert client.get("/v1/signals/latest").status_code == 200          # data -> free (standing policy)


def test_signals_paywall_re_engages_when_free_data_off(monkeypatch):
    """Reversibility guard: with FREE_DATA off, a data-bearing signals request paywalls again."""
    monkeypatch.setattr(payments, "MODE", "trust")
    monkeypatch.setattr(payments, "FREE_DATA", False)
    assert client.get("/v1/signals/by-ticker/ZZZZ").status_code == 200  # empty still free
    assert client.get("/v1/signals/latest").status_code == 402          # data -> paywalled


def test_meta_describes_signals_tier():
    tiers = client.get("/v1/meta").json()["tiers"]
    sig = tiers["signals_cross_source"]
    assert sig["status"] == "live"
    assert "congress_trade" in sig["signal_types"]
