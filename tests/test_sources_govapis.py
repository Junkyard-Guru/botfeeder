"""Normalization tests for the 4 government-API source modules (source-collection validation pass). Each test loads a
real fixture captured live 2026-07-03 and drives the module's normalization logic exactly the
way fetch_new would, without touching the network. No live HTTP calls in this suite.
"""
from __future__ import annotations

import json
from pathlib import Path

from producer.sources import cftc_cot, openfda_drugsfda, treasury_auctions, usaspending

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- CFTC Commitment of Traders ------------------------------------------------------------

def test_cftc_module_identity():
    assert cftc_cot.SOURCE_ID == "cftc-cot"
    assert "CFTC" in cftc_cot.LABEL


def test_cftc_normalize_shape():
    rows = _load("cftc_cot_sample.json")
    recs = [cftc_cot._normalize(row, "2026-07-03T00:00:00Z") for row in rows]
    assert len(recs) == len(rows)
    rec = recs[0]
    assert rec["report_date"] and len(rec["report_date"]) == 10
    assert rec["commodity_name"]
    assert rec["contract_market_name"]
    assert isinstance(rec["open_interest"], (int, float))
    assert rec["commercial_long"] is not None
    assert rec["managed_money_long"] is not None
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"
    assert rec["source_url"] == cftc_cot.BASE


def test_cftc_num_coercion_tolerates_blanks():
    assert cftc_cot._num("") is None
    assert cftc_cot._num(None) is None
    assert cftc_cot._num("123") == 123
    assert cftc_cot._num("12.5") == 12.5


def test_cftc_fetch_new_advances_cursor(monkeypatch):
    rows = _load("cftc_cot_sample.json")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    class FakeClient:
        def get(self, url, params=None):
            return FakeResp()

    new_records, state = cftc_cot.fetch_new({}, FakeClient())
    assert len(new_records) == len(rows)
    assert state["last_report_date"] is not None
    # every record's report_date <= cursor
    assert all((r["report_date"] or "") <= state["last_report_date"] for r in new_records)


# --- Treasury auctions ----------------------------------------------------------------------

def test_treasury_module_identity():
    assert treasury_auctions.SOURCE_ID == "treasury-auctions"
    assert "Treasury" in treasury_auctions.LABEL


def test_treasury_normalize_shape():
    rows = _load("treasury_auctions_sample.json")
    recs = [treasury_auctions._normalize(row, "2026-07-03T00:00:00Z") for row in rows]
    assert len(recs) == len(rows)
    rec = recs[0]
    assert rec["cusip"]
    assert rec["security_type"]
    assert rec["security_term"]
    assert rec["auction_date"] and len(rec["auction_date"]) == 10
    assert rec["fetched_at"] == "2026-07-03T00:00:00Z"


def test_treasury_dedup_by_cusip_and_date(monkeypatch):
    rows = _load("treasury_auctions_sample.json")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    class FakeClient:
        def get(self, url, params=None):
            return FakeResp()

    new_records, state = treasury_auctions.fetch_new({}, FakeClient())
    assert len(new_records) == len(rows)
    assert state["last_auction_date"] is not None

    # second run with same rows + persisted state should yield zero new records
    new_records_2, state_2 = treasury_auctions.fetch_new(state, FakeClient())
    assert new_records_2 == []


def test_treasury_days_since_backstops_to_max():
    assert treasury_auctions._days_since("2000-01-01") == treasury_auctions.MAX_DAYS


# --- USASpending awards ----------------------------------------------------------------------

def test_usaspending_module_identity():
    assert usaspending.SOURCE_ID == "usaspending-awards"
    assert "USASpending" in usaspending.LABEL


def test_usaspending_normalize_shape():
    payload = _load("usaspending_awards_sample.json")
    rows = payload["results"]
    recs = [usaspending._normalize(row, "2026-07-03T00:00:00Z") for row in rows]
    assert len(recs) == len(rows)
    rec = recs[0]
    assert rec["award_id"]
    assert rec["recipient_name"]
    assert rec["awarding_agency"] == "Department of Defense"
    assert rec["generated_internal_id"]
    # NAICS is legitimately null in this fixture — asserting the key exists, not its value
    assert "naics_code" in rec


def test_usaspending_fetch_new_paginates_and_dedupes(monkeypatch):
    payload = _load("usaspending_awards_sample.json")

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    call_count = {"n": 0}

    class FakeClient:
        def post(self, url, json=None):
            call_count["n"] += 1
            body = dict(payload)
            body["page_metadata"] = {"hasNext": False}
            return FakeResp(body)

    new_records, state = usaspending.fetch_new({}, FakeClient())
    assert len(new_records) == len(payload["results"])
    assert call_count["n"] == 1
    assert state["last_end_date"] == usaspending._today()
    assert len(state["seen_ids"]) == len(payload["results"])


def test_usaspending_cursor_does_not_advance_when_cap_hit(monkeypatch):
    payload = _load("usaspending_awards_sample.json")
    monkeypatch.setattr(usaspending, "PER_RUN_CAP", 1)

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def post(self, url, json=None):
            body = dict(payload)
            body["page_metadata"] = {"hasNext": True}
            return FakeResp(body)

    new_records, state = usaspending.fetch_new({}, FakeClient())
    assert "last_end_date" not in state  # cursor withheld — window not fully drained


# --- openFDA Drugs@FDA -----------------------------------------------------------------------

def test_openfda_module_identity():
    assert openfda_drugsfda.SOURCE_ID == "openfda-drugsfda"
    assert "openFDA" in openfda_drugsfda.LABEL


def test_openfda_normalize_shape():
    payload = _load("openfda_drugsfda_sample.json")
    rows = payload["results"]
    recs = [openfda_drugsfda._normalize(app, "2026-07-03T00:00:00Z") for app in rows]
    assert len(recs) == len(rows)
    rec = recs[0]
    assert rec["application_number"]
    assert rec["sponsor_name"]
    assert isinstance(rec["submissions"], list)
    assert isinstance(rec["products"], list)
    if rec["products"]:
        product = rec["products"][0]
        assert "active_ingredients" in product


def test_openfda_noop_when_last_updated_unchanged():
    payload = _load("openfda_drugsfda_sample.json")
    last_updated = payload["meta"]["last_updated"]

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    class FakeClient:
        def get(self, url, params=None):
            # meta-only probe (limit=1) — same meta regardless of params in this fake
            return FakeResp({"meta": payload["meta"], "results": payload["results"][:1]})

    state = {"last_updated": last_updated}
    new_records, out_state = openfda_drugsfda.fetch_new(state, FakeClient())
    assert new_records == []
    assert out_state == state


def test_openfda_full_pull_when_last_updated_changed():
    payload = _load("openfda_drugsfda_sample.json")
    rows = payload["results"]

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    call_log = []

    class FakeClient:
        def get(self, url, params=None):
            call_log.append(params)
            skip = int(params.get("skip", 0))
            if params.get("limit") == "1" and "skip" not in params:
                return FakeResp({"meta": payload["meta"], "results": rows[:1]})
            page = rows[skip:skip + openfda_drugsfda.PAGE_LIMIT]
            total = len(rows)
            return FakeResp({
                "meta": {**payload["meta"], "results": {"skip": skip, "limit": openfda_drugsfda.PAGE_LIMIT, "total": total}},
                "results": page,
            })

    new_records, state = openfda_drugsfda.fetch_new({"last_updated": "1999-01-01"}, FakeClient())
    assert len(new_records) == len(rows)
    assert state["last_updated"] == payload["meta"]["last_updated"]
