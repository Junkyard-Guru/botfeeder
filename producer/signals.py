"""Data -> signal mapping layer. Spec: docs/13-signal-mapping.md.

Every active source's records get a uniform machine-readable `signal` envelope attached by
producer/runner.py (and Form 4 records are mapped at serve time by server/app.py, since the
Form 4 loop predates the runner). The envelope answers, for a trading agent, the four
questions raw records don't: WHAT kind of event is this, WHO does it touch (ticker / sector /
market), WHICH WAY does it lean, and HOW STALE is it.

Honesty rules (docs/07 applies at record level — these are load-bearing, not style):
  - direction "context" means "we are NOT claiming a direction", used wherever direction would
    be a guess (earnings 8-Ks, 13F holdings, macro prints). We never fabricate bullish/bearish
    where the primary source doesn't imply one.
  - strength is a coarse prior from the event TYPE (the source-evaluation strength ratings), never a
    backtested score. No signal here claims predictive power — it claims faithful mapping of
    a real-world event class.
  - lag_days is the DISCLOSURE lag (event date -> our fetch) where computable — agents must
    know a congress trade is ~30 days old and a 13F is ~45 days old.
  - A mapper returning None means "real data, no tradeable signal" (a healthy bank, a
    no-ticker asset, an ancient drug approval). The raw record still serves/archives.
  - Mapper crashes are swallowed per-record by runner.py: a signal is enrichment; its failure
    must never cost a customer the underlying data.

Scope shapes: {"ticker": "NFLX"} | {"sector": "energy"} | {"market": "us_rates"} |
{"entity": "<name>"} (real event, no public security resolved — agent may map it itself).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from producer import tickermap

DIRECTIONS = ("bullish", "bearish", "neutral", "context")
STRENGTHS = ("high", "medium", "low")

_TAXONOMY_VERSION = "2026-07-03"


def _envelope(*, signal_type: str, event: str, direction: str, strength: str, scope: dict,
              lag_days: int | None = None, rationale: str | None = None) -> dict:
    assert direction in DIRECTIONS and strength in STRENGTHS
    out = {
        "taxonomy": _TAXONOMY_VERSION,
        "signal_type": signal_type,
        "event": event,
        "direction": direction,
        "strength": strength,
        "scope": scope,
    }
    if lag_days is not None:
        out["lag_days"] = lag_days
    if rationale:
        out["rationale"] = rationale
    return out


def _tickers() -> tickermap.TickerMap:
    """Injectable for tests: monkeypatch producer.signals._TICKERS to a fixture-built map."""
    global _TICKERS
    if _TICKERS is None:
        _TICKERS = tickermap.default_map()
    return _TICKERS


_TICKERS: tickermap.TickerMap | None = None


def _days_between(earlier: str | None, later: str | None) -> int | None:
    """Best-effort day count between two date-ish strings (ISO or MM/DD/YYYY)."""
    def parse(s):
        if not s:
            return None
        s = s[:10]
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None
    a, b = parse(earlier), parse(later)
    if a is None or b is None:
        return None
    return max((b - a).days, 0)


# --- SEC Form 4 (the flag product; mapped at serve time) ------------------------------------------

def map_form4(record: dict) -> dict | None:
    tx = record.get("transaction") or {}
    iss = record.get("issuer") or {}
    ticker = iss.get("ticker")
    if not ticker:
        return None
    code = tx.get("code")
    discretionary = bool(tx.get("discretionary"))
    plan = bool(tx.get("rule_10b5_1"))
    if code == "P":
        direction, strength, event = "bullish", "high", "insider_open_market_purchase"
    elif code == "S":
        direction, strength, event = "bearish", "medium", "insider_open_market_sale"
    else:
        direction, strength, event = "context", "low", f"insider_{tx.get('code_meaning') or 'transaction'}"
    if plan and strength != "low":
        strength = "low"  # pre-scheduled 10b5-1 trades are mechanical, not conviction
    return _envelope(
        signal_type="insider_trade", event=event, direction=direction, strength=strength,
        scope={"ticker": ticker},
        lag_days=_days_between(tx.get("transaction_date"), record.get("filed_at")),
        rationale=("discretionary insider trade" if discretionary and not plan
                   else "10b5-1 pre-scheduled plan trade" if plan else "mechanical/administrative"),
    )


# --- SEC 8-K: material events by legal definition --------------------------------------------------
#
# Item-code taxonomy: (event, direction, strength). Severity rank = table order (first match
# in _8K_SEVERITY wins as the filing's primary signal). Direction is only asserted where the
# item TYPE itself implies one (bankruptcy, restatement, delisting...); earnings and most
# governance items are honest "context".

_8K_ITEMS: dict[str, tuple[str, str, str]] = {
    "1.03": ("bankruptcy_or_receivership", "bearish", "high"),
    "4.02": ("financials_non_reliance_restatement", "bearish", "high"),
    "2.06": ("material_impairment", "bearish", "high"),
    "2.04": ("debt_acceleration_triggered", "bearish", "high"),
    "3.01": ("delisting_notice", "bearish", "high"),
    "5.01": ("change_in_control", "context", "high"),
    "2.02": ("earnings_results", "context", "high"),
    "2.01": ("acquisition_or_disposition_completed", "context", "medium"),
    "4.01": ("auditor_change", "bearish", "medium"),
    "2.05": ("exit_or_disposal_costs", "bearish", "medium"),
    "5.02": ("officer_or_director_change", "context", "medium"),
    "1.01": ("material_agreement", "context", "medium"),
    "1.02": ("material_agreement_terminated", "bearish", "medium"),
    "3.02": ("unregistered_share_sales", "bearish", "low"),
    "1.05": ("material_cybersecurity_incident", "bearish", "high"),
    "5.03": ("charter_or_bylaws_amendment", "context", "low"),
    "5.07": ("shareholder_vote_results", "context", "low"),
    "7.01": ("regulation_fd_disclosure", "context", "low"),
    "8.01": ("other_material_event", "context", "low"),
}
_8K_RANK = list(_8K_ITEMS)  # dict order IS the severity ranking


def map_8k(record: dict) -> dict | None:
    codes = [i.get("code") for i in record.get("items", []) if i.get("code")]
    known = [c for c in _8K_RANK if c in codes]
    if not known:
        return None  # exhibits-only / unrecognized items — data, not signal
    primary = known[0]
    event, direction, strength = _8K_ITEMS[primary]
    hit = _tickers().from_cik((record.get("issuer") or {}).get("cik"))
    scope = {"ticker": hit["ticker"]} if hit else {"entity": (record.get("issuer") or {}).get("name")}
    return _envelope(
        signal_type="material_event", event=event, direction=direction, strength=strength,
        scope=scope,
        rationale=f"8-K item {primary}" + (f" (+{len(known) - 1} more items)" if len(known) > 1 else ""),
    )


# --- SEC 13F-HR: institutional holdings (45-day lag, positioning context) --------------------------

def map_13f(record: dict) -> dict | None:
    h = record.get("holding") or {}
    hit = _tickers().from_name(h.get("name_of_issuer"))
    if not hit:
        return None  # unresolvable issuer name — refuse to guess (docs/13 conservative rule)
    put_call = (h.get("put_call") or "").strip().lower()
    event = f"institutional_{put_call}_position" if put_call in ("put", "call") \
        else "institutional_holding_disclosed"
    return _envelope(
        signal_type="institutional_holding", event=event, direction="context",
        strength="medium" if put_call else "low",
        scope={"ticker": hit["ticker"]},
        lag_days=_days_between(record.get("period_of_report"), record.get("filed_at")),
        rationale=(f"{(record.get('filer') or {}).get('name')} holds ${h.get('value'):,.0f}"
                   if h.get("value") else None),
    )


# --- CFTC Commitment of Traders: futures positioning ------------------------------------------------

_COT_SECTORS: list[tuple[tuple[str, ...], dict]] = [
    (("S&P", "NASDAQ", "DOW", "RUSSELL", "E-MINI", "MICRO E-MINI", "VIX"), {"market": "us_equity_index"}),
    (("TREASURY", "T-NOTE", "T-BOND", "ULTRA", "FED FUNDS", "SOFR"), {"market": "us_rates"}),
    (("EURO FX", "YEN", "POUND", "FRANC", "CANADIAN", "AUSTRALIAN", "PESO", "REAL",
      "USD INDEX", "DOLLAR INDEX"), {"market": "fx"}),
    (("BITCOIN", "ETHER"), {"market": "crypto"}),
    (("CRUDE", "GASOLINE", "HEATING OIL", "NATURAL GAS", "PROPANE", "ETHANOL"), {"sector": "energy"}),
    (("GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "ALUMINUM", "STEEL", "LITHIUM",
      "COBALT", "URANIUM"), {"sector": "metals_mining"}),
    (("WHEAT", "CORN", "SOYBEAN", "OATS", "RICE", "CATTLE", "HOG", "SUGAR", "COFFEE",
      "COCOA", "COTTON", "MILK", "BUTTER", "CHEESE", "LUMBER", "ORANGE JUICE"), {"sector": "agriculture"}),
]


def _cot_scope(name: str | None) -> dict | None:
    up = (name or "").upper()
    for keys, scope in _COT_SECTORS:
        if any(k in up for k in keys):
            return scope
    return None


def map_cot(record: dict) -> dict | None:
    scope = _cot_scope(record.get("commodity_name") or record.get("contract_market_name"))
    oi = record.get("open_interest")
    mm_long, mm_short = record.get("managed_money_long"), record.get("managed_money_short")
    if not scope or not oi or mm_long is None or mm_short is None:
        return None
    net = mm_long - mm_short
    ratio = net / oi
    if abs(ratio) < 0.05:
        return None  # flat positioning — no lean worth flagging
    crowded = abs(ratio) > 0.15
    return _envelope(
        signal_type="futures_positioning",
        event="managed_money_net_long" if net > 0 else "managed_money_net_short",
        direction="bullish" if net > 0 else "bearish",
        strength="medium" if crowded else "low",
        scope=scope,
        rationale=f"{record.get('contract_market_name')}: managed-money net "
                  f"{'long' if net > 0 else 'short'} {abs(net):,.0f} contracts "
                  f"({abs(ratio):.0%} of open interest{', crowded' if crowded else ''})",
    )


# --- Treasury auctions: demand for U.S. duration ----------------------------------------------------
#
# Fixed heuristic bands, documented in docs/13 (typical bid-to-cover: bills ~2.4-3.0,
# notes/bonds ~2.2-2.6). Calibration is heuristic, NOT a backtest — direction reflects
# textbook reading (weak demand -> yields up -> risk-asset headwind).

def map_treasury(record: dict) -> dict | None:
    btc = record.get("bid_to_cover_ratio")
    if btc is None:
        return None
    is_bill = (record.get("security_type") or "").lower() == "bill"
    strong, weak = (2.8, 2.4) if is_bill else (2.5, 2.2)
    if btc >= strong:
        event, direction, strength = "strong_auction_demand", "bullish", "medium"
    elif btc <= weak:
        event, direction, strength = "weak_auction_demand", "bearish", "medium"
    else:
        event, direction, strength = "average_auction_demand", "neutral", "low"
    return _envelope(
        signal_type="auction_demand", event=event, direction=direction, strength=strength,
        scope={"market": "us_rates"},
        rationale=f"{record.get('security_term')} {record.get('security_type')}: "
                  f"bid-to-cover {btc}",
    )


# --- USASpending: federal contract awards -----------------------------------------------------------

_AWARD_MIN_USD = 10_000_000     # below this, a federal award is routine procurement noise
_AWARD_BIG_USD = 1_000_000_000


def map_usaspending(record: dict) -> dict | None:
    amount = record.get("award_amount")
    if not amount or amount < _AWARD_MIN_USD:
        return None
    hit = _tickers().from_name(record.get("recipient_name"))
    if not hit:
        return None  # private / unresolvable recipient — no tradeable security identified
    return _envelope(
        signal_type="gov_contract_award", event="contract_award",
        direction="bullish", strength="high" if amount >= _AWARD_BIG_USD else "medium",
        scope={"ticker": hit["ticker"]},
        rationale=f"${amount:,.0f} from {record.get('awarding_agency')}",
    )


# --- openFDA Drugs@FDA: approvals -------------------------------------------------------------------

_FDA_RECENT_DAYS = 120  # the corpus is mostly decades of history; only fresh approvals signal


def map_openfda(record: dict) -> dict | None:
    subs = record.get("submissions") or []
    approved = [s for s in subs if s.get("submission_status") == "AP"
                and s.get("submission_status_date")]
    if not approved:
        return None
    latest = max(approved, key=lambda s: s["submission_status_date"])
    try:
        ap_date = datetime.strptime(latest["submission_status_date"], "%Y%m%d").date()
    except ValueError:
        return None
    if datetime.now(timezone.utc).date() - ap_date > timedelta(days=_FDA_RECENT_DAYS):
        return None
    hit = _tickers().from_name(record.get("sponsor_name"))
    if not hit:
        return None
    original = latest.get("submission_type") == "ORIG"
    return _envelope(
        signal_type="drug_approval",
        event="fda_original_approval" if original else "fda_supplemental_approval",
        direction="bullish", strength="high" if original else "low",
        scope={"ticker": hit["ticker"]},
        lag_days=(datetime.now(timezone.utc).date() - ap_date).days,
        rationale=f"{record.get('application_number')} approved {ap_date.isoformat()}",
    )


# --- FDIC bank financials: stress flags (signal on exception only) ----------------------------------

def map_fdic(record: dict) -> dict | None:
    assets, equity = record.get("assets"), record.get("equity_capital")
    net_income = record.get("net_income")
    flags = []
    ratio = (equity / assets) if assets and equity is not None else None
    if ratio is not None and ratio < 0.05:
        flags.append(("critical_capital_ratio", "high"))
    elif ratio is not None and ratio < 0.08:
        flags.append(("thin_capital_ratio", "medium"))
    if net_income is not None and net_income < 0:
        flags.append(("negative_net_income", "medium"))
    if not flags:
        return None  # a healthy bank is data, not a signal
    event, strength = flags[0]
    hit = _tickers().from_name(record.get("name"))
    scope = {"ticker": hit["ticker"]} if hit else {"entity": record.get("name")}
    why = f"equity/assets {ratio:.1%}" if ratio is not None else "negative net income"
    if len(flags) > 1:
        why += f"; +{len(flags) - 1} more flags"
    return _envelope(
        signal_type="bank_stress", event=event, direction="bearish", strength=strength,
        scope=scope, rationale=why,
    )


# --- House PTRs: congressional trades ---------------------------------------------------------------

def map_house_ptr(record: dict) -> dict | None:
    ticker = record.get("ticker")
    if not ticker:
        return None  # bonds/funds/unresolved assets — raw data only
    tt = record.get("transaction_type")
    event, direction = {
        "P": ("congress_purchase", "bullish"),
        "S": ("congress_sale", "bearish"),
        "E": ("congress_exchange", "neutral"),
    }.get(tt, (None, None))
    if event is None:
        return None
    low, high = record.get("amount_low"), record.get("amount_high")
    if low is not None and low >= 250_000:
        strength = "high"
    elif high is not None and high <= 15_000:
        strength = "low"  # the minimum band — index-fund-drip territory
    else:
        strength = "medium"
    return _envelope(
        signal_type="congress_trade", event=event, direction=direction, strength=strength,
        scope={"ticker": ticker},
        lag_days=_days_between(record.get("transaction_date"), record.get("notification_date")),
        rationale=f"{record.get('filer_name')} ({record.get('state_dst')}), "
                  f"${low:,.0f}-${high:,.0f}" if low is not None and high is not None else None,
    )


# --- FRED macro series (inactive until FRED_API_KEY is set; mapping ready) --------------------------

def map_fred(record: dict) -> dict | None:
    if record.get("value") is None:
        return None
    return _envelope(
        signal_type="macro_release", event="macro_observation", direction="context",
        strength="low", scope={"market": "us_macro"},
        rationale=f"{record.get('series_name') or record.get('series_id')} = "
                  f"{record.get('value')} ({record.get('date')})",
    )


# --- BIS Entity List (inactive until DATA_GOV_API_KEY is set; mapping ready) ------------------------

def map_bis(record: dict) -> dict | None:
    name = record.get("name")
    if not name:
        return None
    hit = _tickers().from_name(name)
    return _envelope(
        signal_type="sanction_listing", event="bis_entity_listed", direction="bearish",
        strength="high" if hit else "medium",
        scope={"ticker": hit["ticker"]} if hit else {"entity": name},
        rationale=f"export-control listing, {record.get('country') or 'country n/a'}",
    )


# --- registry ---------------------------------------------------------------------------------------

MAPPERS: dict[str, callable] = {
    "sec-8k": map_8k,
    "sec-13f-hr": map_13f,
    "cftc-cot": map_cot,
    "treasury-auctions": map_treasury,
    "usaspending-awards": map_usaspending,
    "openfda-drugsfda": map_openfda,
    "fdic-bank-financials": map_fdic,
    "house-ptr": map_house_ptr,
    "fred-series": map_fred,
    "bis-entity-list": map_bis,
}


def attach_signals(source_id: str, records: list[dict]) -> None:
    """In-place, best-effort enrichment: record['signal'] where a mapper produces one.
    A mapper failure on one record silently yields no signal for that record — enrichment
    must never cost the customer the underlying data (docs/13)."""
    mapper = MAPPERS.get(source_id)
    if mapper is None:
        return
    for r in records:
        try:
            sig = mapper(r)
        except Exception:  # noqa: BLE001
            sig = None
        if sig is not None:
            r["signal"] = sig
