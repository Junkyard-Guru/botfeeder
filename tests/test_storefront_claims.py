"""Claim-truth tests (docs/07: no claim ships unless it's backed).

The human page and llms.txt state specific prices and product facts. These tests pin those
statements to the payments constants and live routes so the storefront cannot silently drift
out of sync with what's actually served/charged.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server import payments
from server.app import app

client = TestClient(app)
PAGE = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_page_lookup_price_matches_payments():
    assert f"${payments.PRICE_USD}/record" in PAGE.replace("0.0060", "0.006")
    assert "$0.006 / record" in PAGE


def test_page_bulk_prices_match_payments():
    assert f"${payments.BULK_PRICE_USD:.2f}" in PAGE           # $5.00
    assert f"${payments.BULK_10K_PRICE_USD:.2f}" in PAGE       # $50.00
    assert f"${payments.BULK_PER_RECORD_USD}/record" in PAGE   # $0.005/record
    assert f"${payments.BULK_10K_PER_RECORD_USD}/record" in PAGE


def test_page_diy_cost_matches_payments():
    assert f"${payments.DIY_COST_PER_FILING_USD}" in PAGE


def test_page_advertises_only_live_machine_surfaces():
    for path in ("/llms.txt", "/v1/meta", "/openapi.json",
                 "/v1/insider/sample", "/v1/signals/sample"):
        assert path in PAGE
        assert client.get(path).status_code == 200, f"{path} advertised but not live"


def test_llms_txt_prices_are_live_constants():
    txt = client.get("/llms.txt").text
    assert f"${payments.PRICE_USD}/record" in txt
    assert f"${payments.BULK_PRICE_USD}" in txt
    assert "source_url" in txt
    assert "never billed" in txt or "never be billed" in txt.lower() or "are never billed" in txt


def test_meta_quickstart_and_products():
    m = client.get("/v1/meta").json()
    assert set(m["products"]) == {"edgar-form4-insider", "signals-cross-source", "watch-retainer"}
    assert any("402" in step for step in m["quickstart_for_agents"])
    assert m["endpoints"]["llms_txt"] == "/llms.txt"
    assert m["endpoints"]["signals_by_ticker"] == "/v1/signals/by-ticker/{ticker}"
