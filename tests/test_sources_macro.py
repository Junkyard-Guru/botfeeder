"""Normalization tests for the 3 macro/reference-data source modules (source-collection validation pass): World Bank,
Eurostat, FDIC bank financials. Each test loads a real fixture captured live 2026-07-03 and
drives the module's normalization/decoding logic exactly the way fetch_new would, without
touching the network. No live HTTP calls in this suite.
"""
from __future__ import annotations

import json
from pathlib import Path

from producer.sources import eurostat, fdic_financials, worldbank

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- World Bank Open Data ---------------------------------------------------------------------

def test_worldbank_module_identity():
    assert worldbank.SOURCE_ID == "worldbank-indicators"
    assert "World Bank" in worldbank.LABEL


def test_worldbank_curated_lists_are_real_codes():
    assert "NY.GDP.MKTP.CD" in worldbank.INDICATORS
    assert "US" in worldbank.COUNTRIES
    assert len(worldbank.INDICATORS) >= 4
    assert len(worldbank.COUNTRIES) >= 8


def test_worldbank_normalize_shape():
    payload = _load("worldbank_sample.json")
    raw_points = payload[1]
    recs = worldbank.normalize_series(raw_points, "2026-07-03T00:00:00Z")
    assert len(recs) == len(raw_points)
    rec = recs[0]
    assert rec["country_code"] == "US"
    assert rec["country_name"] == "United States"
    assert rec["indicator_code"] == "NY.GDP.MKTP.CD"
    assert rec["indicator_name"]
    assert rec["date"]
    assert isinstance(rec["value"], (int, float))
    assert rec["source_url"]
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"


def test_worldbank_normalize_skips_null_values():
    raw_points = [
        {"indicator": {"id": "X", "value": "X"}, "country": {"id": "US", "value": "United States"},
         "date": "2026", "value": None, "unit": ""},
        {"indicator": {"id": "X", "value": "X"}, "country": {"id": "US", "value": "United States"},
         "date": "2025", "value": 1.0, "unit": ""},
    ]
    recs = worldbank.normalize_series(raw_points, "now")
    assert len(recs) == 1
    assert recs[0]["date"] == "2025"


def test_worldbank_fetch_new_tracks_state_per_series():
    payload = _load("worldbank_sample.json")
    raw_points = payload[1]

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url):
            return FakeResp([{}, raw_points])

    new_records, state = worldbank.fetch_new({}, FakeClient())
    n_series = len(worldbank.INDICATORS) * len(worldbank.COUNTRIES)
    assert len(new_records) == n_series * len(raw_points)
    assert len(state) == n_series
    newest_date = max(p["date"] for p in raw_points if p.get("value") is not None)
    assert state["US:NY.GDP.MKTP.CD"] == newest_date


def test_worldbank_fetch_new_noop_when_state_already_current():
    payload = _load("worldbank_sample.json")
    raw_points = payload[1]
    newest_date = max(p["date"] for p in raw_points if p.get("value") is not None)

    state = {f"{country}:{indicator}": newest_date
             for country in worldbank.COUNTRIES for indicator in worldbank.INDICATORS}

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url):
            return FakeResp([{}, raw_points])

    new_records, out_state = worldbank.fetch_new(state, FakeClient())
    assert new_records == []
    assert out_state == state


# --- Eurostat JSON-stat decoder -----------------------------------------------------------------

def test_eurostat_module_identity():
    assert eurostat.SOURCE_ID == "eurostat"
    assert "Eurostat" in eurostat.LABEL


def test_eurostat_curated_datasets_are_real_codes():
    assert "prc_hicp_manr" in eurostat.DATASETS
    assert "une_rt_m" in eurostat.DATASETS
    assert "namq_10_gdp" in eurostat.DATASETS
    assert len(eurostat.DATASETS) >= 3


def test_eurostat_strides_row_major():
    # Verified against the real namq_10_gdp fixture: size=[1,1,1,1,3,5] -> geo stride=5, time
    # stride=1 (last dimension varies fastest).
    assert eurostat._strides([1, 1, 1, 1, 3, 5]) == [15, 15, 15, 15, 5, 1]
    assert eurostat._strides([2, 3]) == [3, 1]
    assert eurostat._strides([5]) == [1]


def test_eurostat_decode_hicp_fixture_matches_real_shape():
    doc = _load("eurostat_sample.json")
    cells = eurostat.decode_jsonstat(doc)
    # Real fixture: size=[1,1,1,3,12] with US having no data (positions-with-no-data.geo=[2]),
    # so only DE+FR (2 geos) x 12 time periods = 24 populated cells, not the full 36.
    assert len(cells) == 24
    sample = cells[0]
    assert set(sample.keys()) >= {"freq", "unit", "coicop", "geo", "time", "value"}
    assert sample["geo"]["code"] in ("DE", "FR")
    assert sample["geo"]["label"] in ("Germany", "France")
    assert isinstance(sample["value"], (int, float))
    # No cell should ever decode to the US geo position (2) — it has no data in this fixture.
    assert all(c["geo"]["code"] != "US" for c in cells)


def test_eurostat_decode_empty_value_returns_empty_list():
    assert eurostat.decode_jsonstat({"id": ["geo"], "size": [3], "value": {}, "dimension": {}}) == []
    assert eurostat.decode_jsonstat({}) == []


def test_eurostat_normalize_hicp_records():
    doc = _load("eurostat_sample.json")
    recs = eurostat.normalize_dataset(doc, "prc_hicp_manr", "HICP inflation", "2026-07-03T00:00:00Z")
    assert len(recs) == 24
    rec = recs[0]
    assert rec["dataset_id"] == "prc_hicp_manr"
    assert rec["dataset_label"] == "HICP inflation"
    assert rec["country_code"] in ("DE", "FR")
    assert rec["country_name"]
    assert rec["time_period"]
    assert rec["unit"] == "RCH_A"
    assert isinstance(rec["value"], (int, float))
    assert rec["indicator_flags"]["coicop"] == "CP00"
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"


def test_eurostat_fetch_new_tracks_state_per_dataset():
    doc = _load("eurostat_sample.json")

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url):
            return FakeResp(doc)

    new_records, state = eurostat.fetch_new({}, FakeClient())
    n_datasets = len(eurostat.DATASETS)
    assert len(new_records) == 24 * n_datasets
    assert len(state) == n_datasets
    assert state["prc_hicp_manr"] == "2025-12"


def test_eurostat_fetch_new_skips_dataset_with_error():
    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url):
            return FakeResp({"error": [{"status": 400}]})

    new_records, state = eurostat.fetch_new({}, FakeClient())
    assert new_records == []
    assert state == {}


def test_eurostat_fetch_new_noop_when_state_already_current():
    doc = _load("eurostat_sample.json")
    state = {ds: "2025-12" for ds in eurostat.DATASETS}

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url):
            return FakeResp(doc)

    new_records, out_state = eurostat.fetch_new(state, FakeClient())
    assert new_records == []
    assert out_state == state


# --- FDIC BankFind financials --------------------------------------------------------------------

def test_fdic_module_identity():
    assert fdic_financials.SOURCE_ID == "fdic-bank-financials"
    assert "FDIC" in fdic_financials.LABEL


def test_fdic_normalize_page_shape():
    payload = _load("fdic_sample.json")
    recs = fdic_financials.normalize_page(payload, "2026-07-03T00:00:00Z")
    assert len(recs) == len(payload["data"])
    rec = recs[0]
    assert isinstance(rec["cert"], int)
    assert rec["name"]
    assert isinstance(rec["assets"], (int, float))
    assert isinstance(rec["deposits"], (int, float))
    assert isinstance(rec["equity_capital"], (int, float))
    assert isinstance(rec["net_income"], (int, float))
    assert rec["repdte"] == "20260331"
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"


def test_fdic_normalize_page_empty_data():
    assert fdic_financials.normalize_page({"data": []}, "now") == []
    assert fdic_financials.normalize_page({}, "now") == []


def test_fdic_quarter_end_dates_desc():
    from datetime import date

    dates = fdic_financials._quarter_end_dates_desc(date(2026, 7, 3), count=4)
    assert dates[0] == "20260630"
    assert dates[1] == "20260331"
    assert dates[2] == "20251231"
    assert dates[3] == "20250930"


def test_fdic_most_recent_reportable_quarter_respects_lag():
    from datetime import date

    # 3 days after Q2 2026 quarter-end -- too soon, reporting lag means it's not out yet.
    assert fdic_financials.most_recent_reportable_quarter(date(2026, 7, 3)) == "20260331"
    # 50 days after Q2 2026 quarter-end -- should now be reportable.
    assert fdic_financials.most_recent_reportable_quarter(date(2026, 8, 19)) == "20260630"


def test_fdic_fetch_new_paginates_and_advances_state():
    payload = _load("fdic_sample.json")
    all_rows = payload["data"]

    class FakeResp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def __init__(self):
            self.calls = []

        def get(self, url):
            self.calls.append(url)
            # Single page: total == number of rows in the fixture, well under _PAGE_LIMIT.
            return FakeResp({"meta": {"total": len(all_rows)}, "data": all_rows})

    fc = FakeClient()
    new_records, state = fdic_financials.fetch_new({}, fc)
    assert len(new_records) == len(all_rows)
    assert state["last_repdte"] == fdic_financials.most_recent_reportable_quarter(
        __import__("datetime").date.today()
    )
    assert len(fc.calls) == 1


def test_fdic_fetch_new_noop_when_state_already_current():
    target = fdic_financials.most_recent_reportable_quarter(
        __import__("datetime").date.today()
    )
    state = {"last_repdte": target}

    class FakeClient:
        def get(self, url):
            raise AssertionError("should not be called when state is already current")

    new_records, out_state = fdic_financials.fetch_new(state, FakeClient())
    assert new_records == []
    assert out_state == state


def test_fdic_fetch_new_handles_no_data_yet_without_crashing():
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"meta": {"total": 0}, "data": []}

    class FakeClient:
        def get(self, url):
            return FakeResp()

    new_records, state = fdic_financials.fetch_new({}, FakeClient())
    assert new_records == []
    assert "last_repdte" not in state
