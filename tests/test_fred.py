"""FRED source module tests. Fixture tests/fixtures/fred_observations_sample.json is
HAND-CONSTRUCTED to match FRED's documented series/observations schema — not a captured
live response (see fixture file + producer/sources/fred.py docstring)."""
import json
from pathlib import Path

import httpx
import pytest

from producer.sources import fred

FIXTURE = Path(__file__).parent / "fixtures" / "fred_observations_sample.json"


def _mock_client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_key_returns_empty_and_logs_once(monkeypatch, capsys):
    monkeypatch.delenv(fred.API_KEY_ENV, raising=False)
    fred._warned = False
    records, state = fred.fetch_new({}, fred.client())
    assert records == []
    assert state == {}
    err = capsys.readouterr().err
    assert fred.API_KEY_ENV in err
    assert "fred.stlouisfed.org" in err


def test_missing_key_warns_only_once(monkeypatch, capsys):
    monkeypatch.delenv(fred.API_KEY_ENV, raising=False)
    fred._warned = False
    fred.fetch_new({}, fred.client())
    fred.fetch_new({}, fred.client())
    err = capsys.readouterr().err
    assert err.count(fred.API_KEY_ENV) == 1


def test_fetch_new_parses_observations(monkeypatch):
    monkeypatch.setenv(fred.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        records, state = fred.fetch_new({}, c)

    # 3 observations per series * 5 curated series
    assert len(records) == 3 * len(fred.SERIES)
    sample = records[0]
    assert sample["series_id"] in fred.SERIES
    assert sample["series_name"] == fred.SERIES[sample["series_id"]]
    assert sample["date"] == "2026-05-01"
    assert sample["value"] == "314.069"


def test_fetch_new_maps_dot_sentinel_to_none(monkeypatch):
    monkeypatch.setenv(fred.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        records, _state = fred.fetch_new({}, c)

    missing = [r for r in records if r["date"] == "2026-07-01"]
    assert missing and all(r["value"] is None for r in missing)


def test_fetch_new_advances_state_cursor(monkeypatch):
    monkeypatch.setenv(fred.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        _records, state = fred.fetch_new({}, c)

    for series_id in fred.SERIES:
        assert state["last_date"][series_id] == "2026-07-01"


def test_fetch_new_skips_dates_at_or_before_cursor(monkeypatch):
    monkeypatch.setenv(fred.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = {"last_date": {sid: "2026-06-01" for sid in fred.SERIES}}

    with _mock_client(payload) as c:
        records, _state = fred.fetch_new(state, c)

    # observation_start is inclusive server-side; our client-side filter drops <= cursor too.
    dates = {r["date"] for r in records}
    assert "2026-06-01" not in dates
    assert "2026-07-01" in dates
