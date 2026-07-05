"""BIS Entity List source module tests. Fixture tests/fixtures/bis_entity_list_sample.json is
HAND-CONSTRUCTED to match the Consolidated Screening List API's documented response schema —
not a captured live response (no DATA_GOV_API_KEY was available at build time). The 'source'
field value used to filter to BIS's own list ("Entity List") is an assumption flagged in
producer/sources/bis_entity_list.py's module docstring TODO, unverified against a live sample."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from producer.sources import bis_entity_list as bis

FIXTURE = Path(__file__).parent / "fixtures" / "bis_entity_list_sample.json"


def _mock_client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_key_returns_empty_and_logs_once(monkeypatch, capsys):
    monkeypatch.delenv(bis.API_KEY_ENV, raising=False)
    bis._warned = False
    records, state = bis.fetch_new({}, bis.client())
    assert records == []
    assert state == {}
    err = capsys.readouterr().err
    assert bis.API_KEY_ENV in err
    assert "api.data.gov" in err


def test_fetch_new_filters_to_entity_list_source(monkeypatch):
    monkeypatch.setenv(bis.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # single page: fewer results than PAGE_SIZE so the loop stops after one call
    assert len(payload["results"]) < bis.PAGE_SIZE

    with _mock_client(payload) as c:
        records, state = bis.fetch_new({}, c)

    # 2 of the 3 fixture rows are source == "Entity List"; the SDN row is filtered out
    assert len(records) == 2
    names = {r["name"] for r in records}
    assert "Example Sanctioned Entity LLC" in names
    assert "Unrelated SDN Entry" not in names


def test_fetch_new_normalizes_address_fields(monkeypatch):
    monkeypatch.setenv(bis.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        records, _state = bis.fetch_new({}, c)

    rec = next(r for r in records if r["name"] == "Example Sanctioned Entity LLC")
    assert rec["address"] == "123 Example Street"
    assert rec["country"] == "China"
    assert rec["federal_register_notice"] == "91 FR 12345"
    assert rec["effective_date"] == "2026-01-15"


def test_fetch_new_sets_last_full_pull(monkeypatch):
    monkeypatch.setenv(bis.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        _records, state = bis.fetch_new({}, c)

    assert "last_full_pull" in state


def test_fetch_new_skips_refresh_when_recently_pulled(monkeypatch):
    monkeypatch.setenv(bis.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    state = {"last_full_pull": recent}

    with _mock_client(payload) as c:
        records, new_state = bis.fetch_new(state, c)

    assert records == []
    assert new_state["last_full_pull"] == recent


def test_fetch_new_refreshes_when_stale(monkeypatch):
    monkeypatch.setenv(bis.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    state = {"last_full_pull": stale}

    with _mock_client(payload) as c:
        records, new_state = bis.fetch_new(state, c)

    assert len(records) == 2
    assert new_state["last_full_pull"] != stale
