"""Claim-truth tests (docs/07: no claim ships unless it's backed).

The human page and llms.txt state specific prices and product facts. These tests pin those
statements to the payments constants and live routes so the storefront cannot silently drift
out of sync with what's actually served/charged.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server import payments, watch
from server.app import app

client = TestClient(app)
PAGE = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_page_advertises_data_as_free():
    """The page must say the data is free — with no lingering per-record data price."""
    assert '<p class="price">Free</p>' in PAGE
    assert "The data is free" in PAGE
    assert "$0.006" not in PAGE  # the old per-record data price is gone
    assert "$0.005" not in PAGE  # old bulk per-record price gone


def test_page_watch_retainer_price_matches_constants():
    """The one paid product's price on the page is pinned to the watch constants."""
    assert f"${watch.WATCH_BASE_USD:.2f}/month" in PAGE          # $2.00/month
    assert f"${watch.WATCH_ENTITY_USD:.2f}" in PAGE              # $0.40 per entity


def test_page_diy_cost_still_shown_as_value_avoided():
    """The DIY cost stays on the page — now framed as the inference you avoid, for free."""
    assert f"${payments.DIY_COST_PER_FILING_USD}" in PAGE


def test_page_advertises_only_live_machine_surfaces():
    for path in ("/llms.txt", "/v1/meta", "/openapi.json",
                 "/v1/insider/sample", "/v1/signals/sample"):
        assert path in PAGE
        assert client.get(path).status_code == 200, f"{path} advertised but not live"


def test_llms_txt_states_free_data():
    txt = client.get("/llms.txt").text
    assert "FREE" in txt or "free" in txt
    assert "The data is FREE" in txt or "data is free" in txt.lower()
    assert "source_url" in txt
    # the data endpoints are advertised as free, and the retainer as the paid one
    assert "(free)" in txt
    assert "PAID" in txt


def test_meta_quickstart_and_products():
    m = client.get("/v1/meta").json()
    assert set(m["products"]) == {"edgar-form4-insider", "signals-cross-source", "watch-retainer"}
    assert any("402" in step for step in m["quickstart_for_agents"])
    assert m["endpoints"]["llms_txt"] == "/llms.txt"
    assert m["endpoints"]["signals_by_ticker"] == "/v1/signals/by-ticker/{ticker}"
