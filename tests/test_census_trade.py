"""Census intltrade source module tests. Fixture tests/fixtures/census_intltrade_sample.json
is HAND-CONSTRUCTED to match the Census Bureau's documented timeseries API row-shape
convention (header row + parallel value rows) — not a captured live response (no
CENSUS_API_KEY was available at build time; see producer/sources/census_trade.py docstring)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from producer.sources import census_trade

FIXTURE = Path(__file__).parent / "fixtures" / "census_intltrade_sample.json"


def _mock_client(payload) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_key_returns_empty_and_logs_once(monkeypatch, capsys):
    monkeypatch.delenv(census_trade.API_KEY_ENV, raising=False)
    census_trade._warned = False
    records, state = census_trade.fetch_new({}, census_trade.client())
    assert records == []
    assert state == {}
    err = capsys.readouterr().err
    assert census_trade.API_KEY_ENV in err
    assert "api.census.gov" in err


def test_fetch_new_parses_header_value_rows(monkeypatch):
    monkeypatch.setenv(census_trade.API_KEY_ENV, "dummy-key")
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with _mock_client(payload) as c:
        records, state = census_trade.fetch_new({}, c)

    assert len(records) == 1
    rec = records[0]
    assert rec["period"] == "2026-05"
    assert rec["commodity_level"] == "HS0"
    assert rec["value_usd"] == "215678901234"
    assert state["last_period"]


def test_next_period_rolls_year():
    assert census_trade._next_period("2026-12") == "2027-01"
    assert census_trade._next_period("2026-05") == "2026-06"


def test_months_since_no_cursor_pulls_current_only():
    assert census_trade._months_since(None, "2026-07") == ["2026-07"]


def test_months_since_backfills_gap():
    assert census_trade._months_since("2026-04", "2026-07") == ["2026-05", "2026-06", "2026-07"]


def test_fetch_new_skips_header_only_response(monkeypatch):
    monkeypatch.setenv(census_trade.API_KEY_ENV, "dummy-key")
    empty_payload = [["ALL_VAL_MO", "COMM_LVL", "time"]]

    with _mock_client(empty_payload) as c:
        records, state = census_trade.fetch_new({}, c)

    assert records == []
    assert "last_period" not in state
