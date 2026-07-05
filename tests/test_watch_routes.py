"""Watch product: HTTP routes, the watch loop, and delivery guards. Spec: docs/09."""
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from producer import edgar, watch_loop
from server import payments, watch_delivery
from server import watch_store as store
from server.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "watch.db")
    monkeypatch.setattr(payments, "MODE", "off")  # subscribe provisions free unless a test flips it
    yield


def _aapl_resolver(q, c=None):
    return {"cik": "320193", "label": "AAPL", "name": "Apple Inc."} if str(q).upper() == "AAPL" else None


# --- routes ---

def test_subscribe_status_poll_roundtrip(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_to_cik", _aapl_resolver)
    r = client.post("/v1/watch/subscribe", json={"watchlist": ["AAPL", "ZZZNOPE"], "months": 6})
    assert r.status_code == 200
    j = r.json()
    assert j["watching"] == [{"cik": "320193", "label": "AAPL"}]
    assert j["unresolved"] == ["ZZZNOPE"] and j["quote"]["months"] == 6
    token = j["token"]

    assert client.get(f"/v1/watch/{token}").json()["status"] == "active"

    store.add_match(token, "acc-9", {"matched_cik": "320193", "records": []}, cik="320193")
    poll = client.get(f"/v1/watch/{token}/new").json()
    assert poll["count"] == 1 and poll["matches"][0]["filing_id"] == "acc-9"
    assert client.get(f"/v1/watch/{token}/new").json()["count"] == 0  # consumed


def test_subscribe_with_token_extends(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_to_cik", _aapl_resolver)
    first = client.post("/v1/watch/subscribe", json={"watchlist": ["AAPL"], "months": 1}).json()
    tok, first_through = first["token"], first["paid_through"]
    assert first["mode"] == "created"
    ext = client.post("/v1/watch/subscribe", json={"token": tok, "months": 6})
    assert ext.status_code == 200
    j = ext.json()
    assert j["mode"] == "extended" and j["token"] == tok and j["paid_through"] > first_through
    assert client.post("/v1/watch/subscribe", json={"token": "nope", "months": 1}).status_code == 404


def test_subscribe_rejects_bad_term(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_to_cik", _aapl_resolver)
    assert client.post("/v1/watch/subscribe", json={"watchlist": ["AAPL"], "months": 4}).status_code == 400


def test_subscribe_rejects_empty_watchlist():
    assert client.post("/v1/watch/subscribe", json={"watchlist": [], "months": 1}).status_code == 400


def test_subscribe_rejects_private_webhook(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_to_cik", _aapl_resolver)
    r = client.post("/v1/watch/subscribe",
                    json={"watchlist": ["AAPL"], "months": 1, "webhook_url": "http://127.0.0.1/hook"})
    assert r.status_code == 400


def test_subscribe_402_in_trust_mode(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_to_cik", _aapl_resolver)
    monkeypatch.setattr(payments, "MODE", "trust")
    r = client.post("/v1/watch/subscribe", json={"watchlist": ["AAPL", "AAPL"], "months": 12})
    assert r.status_code == 402
    assert r.json()["quote"]["months"] == 12  # the 402 carries the price quote


def test_meta_exposes_watch_tier():
    w = client.get("/v1/meta").json()["tiers"]["watch"]
    assert w["status"] == "live" and w["price_usd_per_month"]["base"] == 2.00
    assert w["term_discounts"]["12"] == 0.35


# --- watch loop ---

def test_watch_cycle_delivers_then_dedupes(monkeypatch):
    token = store.create_subscription([{"cik": "320193", "label": "AAPL"}],
                                      paid_through="2999-01-01T00:00:00+00:00",
                                      created_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(edgar, "recent_form4_for_cik", lambda cik, limit=10, c=None: [
        {"accession": "acc-1", "cik": "320193", "acc_nodash": "x", "form": "4",
         "filed_at": "2026-06-29T00:00:00+00:00"}])
    monkeypatch.setattr(edgar, "primary_form4_xml_url", lambda *a, **k: "https://sec.gov/x.xml")
    monkeypatch.setattr(edgar, "fetch", lambda url, c=None: b"<xml/>")
    monkeypatch.setattr(watch_loop, "parse_form4", lambda xml, **k: [{"issuer": {"ticker": "AAPL"}}])

    s = watch_loop.run_watch_cycle(now="2026-06-29T12:00:00+00:00", deliver_webhook=False)
    assert s["new_matches"] == 1 and store.has_match(token, "acc-1")
    s2 = watch_loop.run_watch_cycle(now="2026-06-29T12:05:00+00:00", deliver_webhook=False)
    assert s2["new_matches"] == 0  # dedup — never push the same filing twice


def test_watch_cycle_skips_pre_subscription_filings(monkeypatch):
    store.create_subscription([{"cik": "1", "label": "x"}], paid_through="2999-01-01T00:00:00+00:00",
                              created_at="2026-06-29T00:00:00+00:00")
    monkeypatch.setattr(edgar, "recent_form4_for_cik", lambda cik, limit=10, c=None: [
        {"accession": "old", "cik": "1", "acc_nodash": "x", "form": "4",
         "filed_at": "2026-06-01T00:00:00+00:00"}])  # before the sub started
    s = watch_loop.run_watch_cycle(now="2026-06-29T12:00:00+00:00", deliver_webhook=False)
    assert s["new_matches"] == 0  # no day-one backfill flood


# --- delivery guards ---

def test_ssrf_guard_blocks_private_allows_public():
    assert watch_delivery.safe_webhook_url("http://127.0.0.1/h") is False
    assert watch_delivery.safe_webhook_url("http://localhost/h") is False
    assert watch_delivery.safe_webhook_url("http://10.0.0.1/h") is False
    assert watch_delivery.safe_webhook_url("http://169.254.169.254/latest") is False  # cloud metadata
    assert watch_delivery.safe_webhook_url("ftp://example.com/h") is False
    assert watch_delivery.safe_webhook_url("https://93.184.216.34/hook") is True       # public literal IP


def test_webhook_signature_is_hmac_sha256():
    assert watch_delivery.sign(b"body", "secret") == hmac.new(b"secret", b"body", hashlib.sha256).hexdigest()
