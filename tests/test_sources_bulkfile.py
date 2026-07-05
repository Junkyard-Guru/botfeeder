"""Unit tests for the 3 new bulk-file source modules (DOL H-1B/LCA, MarineCadastre AIS,
FCC ULS/auctions): the source-collection validation notes.

Fixture-based, no live network calls, matching tests/test_sources_edgar_group.py's convention.

Fixture provenance:
  - dol_performance_page_sample.html — REAL, captured live 2026-07-03 from
    https://www.dol.gov/agencies/eta/foreign-labor/performance (curl + browser UA, 200 OK).
  - dol_lca_sample.xlsx — hand-built (NOT downloaded — the real file is 137MB/~1.04M rows),
    but the header row is the REAL DOL LCA disclosure column schema, and the real file's
    shape (shared-string header row, single sheet) was verified live by raw zip/deflate
    inspection of the actual FY2026 Q2 file before building this fixture.
  - marinecadastre_ais_sample.zip / .csv — REAL rows, extracted live 2026-07-03 from
    https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2024/AIS_2024_01_01.zip via a partial
    HTTP range request + raw deflate decompression (no full 290MB download needed).
  - fcc_uls_3650_sample.json — REAL rows, live 2026-07-03 from
    https://opendata.fcc.gov/resource/euz5-46g2.json?$limit=5 (confirmed tabular/queryable;
    fcc.gov itself was unreachable from this environment, see producer/sources/fcc_uls_auctions.py).
"""
import importlib.util
import json
from pathlib import Path

import pytest

from producer.sources import dol_h1b, fcc_uls_auctions, marinecadastre_ais

FIXTURES = Path(__file__).parent / "fixtures"

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None


# --- DOL H-1B / LCA ---------------------------------------------------------------------------

def test_dol_finds_current_lca_link_from_real_page():
    html = (FIXTURES / "dol_performance_page_sample.html").read_text(encoding="utf-8")
    hit = dol_h1b.find_current_lca_file(html)
    assert hit is not None
    assert hit["url"].startswith("https://www.dol.gov/media/LCA_Dis")
    assert hit["url"].endswith(".xlsx")
    assert hit["fiscal_year"] >= 2026


def test_dol_link_regex_matches_real_dol_typo_in_filename():
    # DOL's own live href literally spells it "Dislclosure" — assert we match the real string,
    # not a "corrected" guess.
    html = '<a href="https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx">LCA</a>'
    hit = dol_h1b.find_current_lca_file(html)
    assert hit == {"url": "https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx",
                   "fiscal_year": 2026, "quarter": 2}


def test_dol_find_current_lca_file_returns_none_when_absent():
    assert dol_h1b.find_current_lca_file("<html><body>no links here</body></html>") is None


@pytest.mark.skipif(not HAS_OPENPYXL, reason="openpyxl not installed — add to pyproject.toml to enable dol_h1b parsing")
def test_dol_parses_real_schema_xlsx_fixture():
    content = (FIXTURES / "dol_lca_sample.xlsx").read_bytes()
    records = dol_h1b.parse_lca_workbook(
        content, source_url="https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx",
        fetched_at="2026-07-03T00:00:00Z",
    )
    assert len(records) == 3
    first = records[0]
    assert first["case_number"] == "I-200-26001-000001"
    assert first["case_status"] == "Certified"
    assert first["employer_name"] == "ACME SOFTWARE INC"
    assert first["job_title"] == "Software Engineer"
    assert first["worksite_state"] == "CA"
    assert first["wage_rate_from"] == 145000
    assert first["source_url"].endswith(".xlsx")


def test_dol_fetch_new_skips_cleanly_without_openpyxl(monkeypatch):
    # Simulate the "openpyxl not installed" branch regardless of this env's actual install,
    # by forcing the import to fail inside fetch_new.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("simulated: openpyxl not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    class FakeResp:
        status_code = 200
        text = '<a href="https://www.dol.gov/media/LCA_Dislclosure_Data_FY2026_Q2.xlsx">x</a>'

        def raise_for_status(self):
            pass

    class FakeClient:
        def get(self, url, **kwargs):
            return FakeResp()

    records, state = dol_h1b.fetch_new({}, FakeClient())
    assert records == []
    assert "last_file_url" not in state


# --- MarineCadastre AIS ------------------------------------------------------------------------

def test_ais_daily_file_url_pattern():
    import datetime
    url = marinecadastre_ais.daily_file_url(datetime.date(2024, 1, 1))
    assert url == "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2024/AIS_2024_01_01.zip"


def test_ais_parses_real_csv_inside_real_zip_fixture():
    content = (FIXTURES / "marinecadastre_ais_sample.zip").read_bytes()
    records = marinecadastre_ais.parse_ais_zip(
        content,
        source_url="https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2024/AIS_2024_01_01.zip",
        fetched_at="2026-07-03T00:00:00Z",
    )
    assert len(records) == 5
    first = records[0]
    assert first["mmsi"] == "338075892"
    assert first["vessel_name"] == "PILOT BOAT SPRING PT"
    assert first["lat"] == pytest.approx(43.65322)
    assert first["lon"] == pytest.approx(-70.25298)
    assert first["call_sign"] == "WDB8945"
    # a row with a blank CallSign in the real data should degrade to None, not crash
    jahazi = [r for r in records if r["vessel_name"] == "JAHAZI"][0]
    assert jahazi["call_sign"] is None


def test_ais_parse_respects_row_limit():
    content = (FIXTURES / "marinecadastre_ais_sample.zip").read_bytes()
    records = marinecadastre_ais.parse_ais_zip(
        content, source_url="x", fetched_at="2026-07-03T00:00:00Z", limit=2,
    )
    assert len(records) == 2


def test_ais_fetch_new_advances_state_only_when_published(monkeypatch):
    zip_bytes = (FIXTURES / "marinecadastre_ais_sample.zip").read_bytes()

    class FakeHead:
        status_code = 200

    class FakeGet:
        content = zip_bytes

        def raise_for_status(self):
            pass

    class FakeClient:
        def head(self, url, **kwargs):
            return FakeHead()

        def get(self, url, **kwargs):
            return FakeGet()

    state = {"last_processed_date": "2024-01-01"}
    records, new_state = marinecadastre_ais.fetch_new(state, FakeClient())
    assert new_state["last_processed_date"] == "2024-01-02"
    assert len(records) == 5


def test_ais_fetch_new_no_op_when_not_yet_published(monkeypatch):
    class FakeHead:
        status_code = 404

    class FakeClient:
        def head(self, url, **kwargs):
            return FakeHead()

    state = {"last_processed_date": "2024-01-01"}
    records, new_state = marinecadastre_ais.fetch_new(state, FakeClient())
    assert records == []
    assert new_state["last_processed_date"] == "2024-01-01"


# --- FCC ULS / auctions --------------------------------------------------------------------

def test_fcc_normalizes_real_uls_3650_rows():
    rows = json.loads((FIXTURES / "fcc_uls_3650_sample.json").read_text(encoding="utf-8"))
    assert rows  # sanity: fixture has real rows
    records = [fcc_uls_auctions._normalize(r, fetched_at="2026-07-03T00:00:00Z") for r in rows]
    first = records[0]
    assert first["licensee"] == "ELECTRONIC INNOVATIONS, INC"
    assert first["call_sign"] == "WQVF475"
    assert first["location_state"] == "WI"
    assert first["service_type"] == "3650-3700 MHz wireless broadband (ULS)"


def test_fcc_fetch_new_pages_and_advances_offset():
    rows = json.loads((FIXTURES / "fcc_uls_3650_sample.json").read_text(encoding="utf-8"))

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    class FakeClient:
        def get(self, url, params=None, **kwargs):
            return FakeResp()

    records, state = fcc_uls_auctions.fetch_new({}, FakeClient())
    assert len(records) == len(rows)
    assert state["offset"] == len(rows)
    assert state["exhausted"] is True  # fixture page is smaller than PAGE_SIZE


def test_fcc_fetch_new_stops_when_exhausted():
    class FakeClient:
        def get(self, url, params=None, **kwargs):
            raise AssertionError("should not fetch once exhausted")

    records, state = fcc_uls_auctions.fetch_new({"exhausted": True, "offset": 500}, FakeClient())
    assert records == []
    assert state["offset"] == 500
