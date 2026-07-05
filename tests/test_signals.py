"""Signal-mapping layer tests (docs/13). All offline: the ticker map is fixture-built and
injected via producer.signals._TICKERS — no network in the suite."""
from __future__ import annotations

import producer.signals as signals
from producer.tickermap import TickerMap, normalize_name

FIXTURE_ROWS = [
    {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."},
    {"ticker": "LMT", "cik_str": 936468, "title": "LOCKHEED MARTIN CORP"},
    {"ticker": "PFE", "cik_str": 78003, "title": "PFIZER INC"},
    {"ticker": "GOOGL", "cik_str": 1652044, "title": "Alphabet Inc."},
    {"ticker": "GOOG", "cik_str": 1652044, "title": "Alphabet Inc."},
    # Two DIFFERENT companies that normalize to the same name — must poison the key:
    {"ticker": "FAKE1", "cik_str": 1, "title": "Acme Widgets Inc"},
    {"ticker": "FAKE2", "cik_str": 2, "title": "ACME WIDGETS CORP"},
]


def setup_module():
    signals._TICKERS = TickerMap(FIXTURE_ROWS)


def teardown_module():
    signals._TICKERS = None


# --- tickermap -------------------------------------------------------------------------------

def test_normalize_strips_legal_noise():
    assert normalize_name("Apple Inc.") == "APPLE"
    assert normalize_name("LOCKHEED MARTIN CORP") == "LOCKHEED MARTIN"
    assert normalize_name("The Coca-Cola Company") == "COCA COLA"


def test_name_match_exact_after_normalization():
    m = TickerMap(FIXTURE_ROWS)
    assert m.from_name("APPLE INC")["ticker"] == "AAPL"
    assert m.from_name("Lockheed Martin Corporation")["ticker"] == "LMT"
    assert m.from_name("Some Unknown Startup LLC") is None


def test_ambiguous_name_refuses_to_guess():
    m = TickerMap(FIXTURE_ROWS)
    assert m.from_name("Acme Widgets Inc") is None  # FAKE1 vs FAKE2 collision -> poisoned


def test_same_company_share_classes_keep_first_ticker():
    m = TickerMap(FIXTURE_ROWS)
    assert m.from_name("Alphabet Inc.")["ticker"] == "GOOGL"  # GOOGL listed before GOOG


def test_cik_lookup():
    m = TickerMap(FIXTURE_ROWS)
    assert m.from_cik("0000320193")["ticker"] == "AAPL"
    assert m.from_cik(320193)["ticker"] == "AAPL"
    assert m.from_cik("999999999") is None


# --- Form 4 ----------------------------------------------------------------------------------

def _form4(code="P", plan=False, ticker="AAPL"):
    return {"issuer": {"ticker": ticker}, "filed_at": "2026-07-01",
            "transaction": {"code": code, "code_meaning": "open_market_purchase",
                            "discretionary": code in ("P", "S"), "rule_10b5_1": plan,
                            "transaction_date": "2026-06-28"}}


def test_form4_purchase_is_bullish_high():
    s = signals.map_form4(_form4("P"))
    assert (s["direction"], s["strength"]) == ("bullish", "high")
    assert s["scope"] == {"ticker": "AAPL"}
    assert s["lag_days"] == 3


def test_form4_10b51_plan_downgrades_strength():
    assert signals.map_form4(_form4("S", plan=True))["strength"] == "low"


def test_form4_no_ticker_no_signal():
    assert signals.map_form4(_form4(ticker=None)) is None


# --- 8-K -------------------------------------------------------------------------------------

def test_8k_bankruptcy_outranks_other_items():
    rec = {"issuer": {"cik": "320193", "name": "Apple Inc."},
           "items": [{"code": "9.01"}, {"code": "1.03"}, {"code": "8.01"}]}
    s = signals.map_8k(rec)
    assert s["event"] == "bankruptcy_or_receivership"
    assert (s["direction"], s["strength"]) == ("bearish", "high")
    assert s["scope"] == {"ticker": "AAPL"}


def test_8k_unresolved_cik_scopes_to_entity():
    rec = {"issuer": {"cik": "424242", "name": "Private Widgets LLC"},
           "items": [{"code": "2.02"}]}
    s = signals.map_8k(rec)
    assert s["scope"] == {"entity": "Private Widgets LLC"}
    assert s["direction"] == "context"  # earnings direction is never asserted from metadata


def test_8k_exhibits_only_is_not_a_signal():
    assert signals.map_8k({"issuer": {"cik": "320193"}, "items": [{"code": "9.01"}]}) is None


# --- 13F -------------------------------------------------------------------------------------

def test_13f_resolved_holding_is_context():
    rec = {"filer": {"name": "Big Fund LP"}, "filed_at": "2026-05-15",
           "period_of_report": "2026-03-31",
           "holding": {"name_of_issuer": "APPLE INC", "value": 1000000, "put_call": None}}
    s = signals.map_13f(rec)
    assert (s["direction"], s["strength"]) == ("context", "low")
    assert s["scope"] == {"ticker": "AAPL"}
    assert s["lag_days"] == 45


def test_13f_put_position_is_medium():
    rec = {"filer": {"name": "Big Fund LP"},
           "holding": {"name_of_issuer": "APPLE INC", "put_call": "Put"}}
    s = signals.map_13f(rec)
    assert s["event"] == "institutional_put_position"
    assert s["strength"] == "medium"


def test_13f_unresolved_name_no_signal():
    assert signals.map_13f({"holding": {"name_of_issuer": "OBSCURE FOREIGN CO"}}) is None


# --- CFTC CoT --------------------------------------------------------------------------------

def test_cot_crowded_net_short_maps_to_sector():
    rec = {"commodity_name": "WHEAT", "contract_market_name": "WHEAT-SRW",
           "open_interest": 100000, "managed_money_long": 10000, "managed_money_short": 30000}
    s = signals.map_cot(rec)
    assert (s["direction"], s["strength"]) == ("bearish", "medium")  # 20% of OI = crowded
    assert s["scope"] == {"sector": "agriculture"}


def test_cot_index_futures_scope_is_market():
    rec = {"commodity_name": "E-MINI S&P 500", "contract_market_name": "E-MINI S&P 500",
           "open_interest": 100000, "managed_money_long": 20000, "managed_money_short": 8000}
    assert signals.map_cot(rec)["scope"] == {"market": "us_equity_index"}


def test_cot_flat_positioning_no_signal():
    rec = {"commodity_name": "GOLD", "open_interest": 100000,
           "managed_money_long": 10000, "managed_money_short": 9000}
    assert signals.map_cot(rec) is None  # 1% of OI — below the 5% floor


# --- Treasury --------------------------------------------------------------------------------

def test_treasury_weak_bill_auction_is_bearish():
    s = signals.map_treasury({"security_type": "Bill", "security_term": "4-Week",
                              "bid_to_cover_ratio": 2.3})
    assert (s["event"], s["direction"]) == ("weak_auction_demand", "bearish")
    assert s["scope"] == {"market": "us_rates"}


def test_treasury_average_note_auction_is_neutral_low():
    s = signals.map_treasury({"security_type": "Note", "security_term": "10-Year",
                              "bid_to_cover_ratio": 2.35})
    assert (s["direction"], s["strength"]) == ("neutral", "low")


# --- USASpending -----------------------------------------------------------------------------

def test_usaspending_resolved_big_award_is_bullish():
    rec = {"recipient_name": "LOCKHEED MARTIN CORP", "award_amount": 50_000_000,
           "awarding_agency": "Department of Defense"}
    s = signals.map_usaspending(rec)
    assert (s["direction"], s["strength"]) == ("bullish", "medium")
    assert s["scope"] == {"ticker": "LMT"}


def test_usaspending_below_threshold_or_unresolved_no_signal():
    assert signals.map_usaspending(
        {"recipient_name": "LOCKHEED MARTIN CORP", "award_amount": 500_000}) is None
    assert signals.map_usaspending(
        {"recipient_name": "Bob's Paving LLC", "award_amount": 50_000_000}) is None


# --- openFDA ---------------------------------------------------------------------------------

def _fda(status_date, sub_type="ORIG", sponsor="PFIZER INC"):
    return {"application_number": "NDA000001", "sponsor_name": sponsor,
            "submissions": [{"submission_type": sub_type, "submission_status": "AP",
                             "submission_status_date": status_date}]}


def test_openfda_recent_original_approval_is_bullish_high():
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y%m%d")
    s = signals.map_openfda(_fda(recent))
    assert (s["event"], s["strength"]) == ("fda_original_approval", "high")
    assert s["scope"] == {"ticker": "PFE"}


def test_openfda_ancient_approval_no_signal():
    assert signals.map_openfda(_fda("20041027")) is None


# --- FDIC ------------------------------------------------------------------------------------

def test_fdic_thin_capital_flags_bearish():
    rec = {"cert": 1, "name": "Some Community Bank", "assets": 1000000,
           "equity_capital": 60000, "net_income": 500}
    s = signals.map_fdic(rec)
    assert (s["event"], s["direction"]) == ("thin_capital_ratio", "bearish")
    assert s["scope"] == {"entity": "Some Community Bank"}


def test_fdic_healthy_bank_no_signal():
    rec = {"cert": 1, "name": "Healthy Bank", "assets": 1000000,
           "equity_capital": 120000, "net_income": 5000}
    assert signals.map_fdic(rec) is None


# --- House PTR -------------------------------------------------------------------------------

def test_ptr_purchase_with_lag():
    rec = {"filer_name": "Hon. A", "state_dst": "GA12", "ticker": "AAPL",
           "transaction_type": "P", "transaction_date": "12/12/2025",
           "notification_date": "01/06/2026", "amount_low": 15001, "amount_high": 50000}
    s = signals.map_house_ptr(rec)
    assert (s["event"], s["direction"], s["strength"]) == ("congress_purchase", "bullish", "medium")
    assert s["lag_days"] == 25


def test_ptr_minimum_band_is_low_and_no_ticker_is_none():
    rec = {"ticker": "AAPL", "transaction_type": "S", "amount_low": 1001, "amount_high": 15000}
    assert signals.map_house_ptr(rec)["strength"] == "low"
    assert signals.map_house_ptr({"ticker": None, "transaction_type": "P"}) is None


# --- attach_signals plumbing -----------------------------------------------------------------

def test_attach_signals_enriches_in_place_and_survives_mapper_errors():
    records = [
        {"issuer": {"cik": "320193", "name": "Apple Inc."}, "items": [{"code": "1.03"}]},
        {"items": "NOT A LIST — would crash the mapper"},
    ]
    signals.attach_signals("sec-8k", records)
    assert records[0]["signal"]["event"] == "bankruptcy_or_receivership"
    assert "signal" not in records[1]  # crash swallowed, record intact


def test_attach_signals_unknown_source_is_noop():
    records = [{"anything": 1}]
    signals.attach_signals("no-such-source", records)
    assert records == [{"anything": 1}]
