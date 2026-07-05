"""The Junkyard API + storefront. Spec: docs/02, docs/03, docs/07.

Free routes (discovery): /health, /v1/meta, and the static human pages.
Paid routes (x402): /v1/insider/*. Payment is required CONDITIONALLY — only after a
non-empty result is in hand (server/payments.py) — so a 500 or empty result is never
billed (docs/02 hard rule).

The server is stateless: it reads ONLY the last-good snapshot and the append-only archive the
producer writes (never re-derives or recomputes — see producer/writer.py for the full-fidelity
vs. flattened-analytics distinction between archive/<date>.jsonl and archive/<date>.parquet).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from producer import edgar, signals
from producer.main import REGISTRY, SNAPSHOT_CAP
from producer.writer import load_archive_day, load_archive_recent, load_snapshot
from server import ethos, mcp_server, payments, watch, watch_delivery, watch_store

DATA_DIR = Path(os.environ.get(
    "FEEDFACE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_LIMIT = 500


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # The MCP sub-app's session manager must run inside the parent lifespan —
    # FastAPI mounts don't propagate lifespans to mounted apps.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(
    title="The Junkyard",
    description="medium-quality data, fresh-squeezed from primary sources",
    lifespan=_lifespan,
)


def _snapshot() -> dict:
    return load_snapshot(DATA_DIR) or {}


def _paywall_or_serve(request: Request, payload: dict, price: float | None = None,
                      discovery_key: str | None = None) -> JSONResponse | dict:
    """Charge only because there's data here; empty payloads never reach this.

    discovery_key tags any resulting 402 with Bazaar discovery metadata for that endpoint.
    """
    records = payload.get("records")
    n_records = len(records) if isinstance(records, list) else payload.get("count")
    try:
        payments.ensure_paid(
            request, price if price is not None else payments.PRICE_USD, discovery_key,
            records=n_records if isinstance(n_records, int) else None)
    except payments.PaymentRequired as e:
        return JSONResponse(status_code=402, content=e.body, headers=e.headers)
    return payload


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/compute-saved")
def compute_saved() -> dict:
    """The instrument behind the thesis: cumulative inference cost buyers avoided by
    buying instead of recomputing. Free, public, and deliberately conservative — see
    volume_store.compute_saved for the exclusions."""
    from . import volume_store
    counter = volume_store.compute_saved(
        payments.DIY_COST_PER_FILING_USD,
        exclude_payers=(payments.HEARTBEAT_PAYER,))
    return {
        "claim": "cumulative DIY inference cost avoided by agents that bought parsed records "
                 "instead of recomputing them",
        "methodology": [
            f"records delivered in settled paid responses x DIY cost "
            f"(${payments.DIY_COST_PER_FILING_USD}/record, cheapest capable model — "
            "the same audited figure as /v1/meta -> diy_comparison)",
            "our own heartbeat self-purchases are excluded by payer address "
            f"({payments.HEARTBEAT_PAYER})",
            "sales settled before record-count logging began (2026-07-05) count as 0 "
            "records — the counter undercounts, never overcounts",
            "net_saved subtracts what buyers actually paid us, so the number is the "
            "buyers' saving, not our revenue",
        ],
        **counter,
    }


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt() -> str:
    """Agent-readable plaintext overview (llms.txt convention). Generated from the same live
    constants as /v1/meta so it cannot drift from what's actually served (docs/07)."""
    return f"""# The Junkyard — bot food
> Parsed primary-source market data for trading agents, priced below your own inference cost.
> Payment: x402 (HTTP 402 -> USDC on Base, per call, no account/signup/API key).

## Why buy instead of DIY
Replicating our Form 4 parse accuracy costs ~${payments.DIY_COST_PER_FILING_USD}/filing
in inference + electricity alone — assuming the cheapest capable model, before enrichment or the
engineering to get the edge cases right.
Our lookup tier is ${payments.PRICE_USD}/record; bulk tiers drop to
${payments.BULK_PER_RECORD_USD} and ${payments.BULK_10K_PER_RECORD_USD}/record.
Live math with sources: GET /v1/meta -> diy_comparison.

## Ingredients (all public-domain / public-record primary sources, fetched direct)
SEC EDGAR Form 4 (insider trades) | SEC 8-K (material events) | SEC 13F-HR (institutional
holdings) | House STOCK-Act PTRs (congressional trades) | USASpending (federal contract
awards) | openFDA (drug approvals) | FDIC (bank financials) | CFTC CoT (futures positioning)
| Treasury auctions. Every record carries source_url — audit us against the original.

## The recipe
Fetch direct -> parse/normalize/classify -> attach a uniform signal envelope
{{signal_type, event, direction, strength, scope, lag_days}}. Honesty is structural:
direction="context" wherever a direction would be a guess; strength is an event-type prior,
never a backtested score; empty results are never billed.

## Endpoints
- GET /v1/meta — full self-description, live prices, tier ladder (free)
- GET /v1/compute-saved — running total of inference cost buyers avoided (free)
- GET /v1/insider/sample, /v1/signals/sample — schema proof (free)
- GET /v1/insider/latest | /v1/insider/{{ticker}} — ${payments.PRICE_USD}/record
- GET /v1/insider/bulk (${payments.BULK_PRICE_USD:.2f}) | bulk/10k (${payments.BULK_10K_PRICE_USD:.2f}) | by-date/{{date}} (${payments.DAILY_PRICE_USD:.2f})
- GET /v1/signals/latest | /v1/signals/by-ticker/{{ticker}} — ${payments.PRICE_USD}/record; /v1/signals/bulk (${payments.BULK_PRICE_USD:.2f})
- POST /v1/watch/subscribe — prepaid watchlist push (webhook + poll)
- GET /openapi.json — machine schema
- MCP (Model Context Protocol): streamable-HTTP server at /mcp — free tools for samples,
  live meta, the compute-saved counter, and payment instructions

## Maker's mark
{ethos.ETHOS_GLYPH}

{chr(10).join(f"- {p['expr']}  =  {p['principle']}" for p in ethos.PRINCIPLES)}

We sell data, not advice.
"""


@app.get("/v1/meta")
def meta() -> dict:
    """Self-description for agents AND the source of truth for the storefront's claims.

    Live counts come straight from the snapshot, so the human-layer numbers can't drift
    out of sync with reality (docs/07).
    """
    snap = _snapshot()
    records = snap.get("records", [])
    issuers = {r["issuer"].get("ticker") for r in records if r.get("issuer")} - {None}
    diy = payments.DIY_COST_PER_FILING_USD
    n = len(records)
    n10k = len(load_archive_recent(DATA_DIR, payments.BULK_10K_LIMIT))
    return {
        "service": "the-junkyard",
        "tagline": "bot food — parsed primary-source market data, priced below your own inference cost",
        "product": "edgar-form4-insider",  # back-compat; see `products`
        "products": {
            "edgar-form4-insider": "SEC Form 4 insider transactions, parsed + classified (/v1/insider/*)",
            "signals-cross-source": "uniform signal envelopes over every mapped feed (/v1/signals/*)",
            "watch-retainer": "prepaid proactive push for a watchlist (/v1/watch/*)",
        },
        "compute_saved": {
            "what": "running total of DIY inference cost avoided by buyers — the number "
                    "this service exists to grow",
            "endpoint": "/v1/compute-saved",
        },
        "quickstart_for_agents": [
            "1. GET /v1/insider/sample and /v1/signals/sample — free, full schema proof, no auth",
            "2. GET /v1/meta (this doc) — live prices, tiers, and the DIY-cost math",
            "3. Call any paid endpoint bare -> HTTP 402 with x402 payment demand "
            "(USDC on Base; header carries the demand, you reply with a signed payment)",
            "4. Empty results are always free — you are never billed for a miss or an error",
            "5. Machine schema: GET /openapi.json; plaintext overview: GET /llms.txt",
        ],
        "source": "SEC EDGAR (U.S. government public domain)",
        "provenance": "fetched direct from primary sources; every record carries source_url",
        # Maker's mark: the seal + its machine-actionable decode (server/ethos.py). An agent can
        # weigh these as part of its trust decision, not just render a sigil.
        "ethos_glyph": ethos.ETHOS_GLYPH,
        "principles": ethos.PRINCIPLES,
        "price_usd_per_record": payments.PRICE_USD,  # lookup tier: charged PER RECORD, not per call
        "bulk_price_usd_per_call": payments.BULK_PRICE_USD,
        "currency": "USDC",
        "network": payments.NETWORK,
        "market_average_usd_per_call": 0.30,
        "settlement_floor_usd_per_call": 0.001,
        "x402_enabled": payments.ENABLED,
        "x402_mode": payments.MODE,
        "live": {
            "record_count": len(records),
            "distinct_issuers": len(issuers),
            "generated_at": snap.get("generated_at"),
        },
        "endpoints": {
            "sample": "/v1/insider/sample",
            "latest": "/v1/insider/latest?limit=&codes=P,S",
            "by_ticker": "/v1/insider/{ticker}?since=YYYY-MM-DD&limit=",
            "bulk": "/v1/insider/bulk",
            "bulk_10k": "/v1/insider/bulk/10k",
            "by_date": "/v1/insider/by-date/{YYYY-MM-DD}",
            "signals_sample": "/v1/signals/sample",
            "signals_latest": "/v1/signals/latest?types=&direction=&min_strength=&limit=",
            "signals_by_ticker": "/v1/signals/by-ticker/{ticker}",
            "signals_bulk": "/v1/signals/bulk",
            "watch_subscribe": "POST /v1/watch/subscribe",
            "openapi": "/openapi.json",
            "llms_txt": "/llms.txt",
        },
        # Published price ladder. free=proof, then commodity feed, then computed/premium. Roadmap
        # tiers carry status="roadmap" so we never advertise an endpoint that isn't live (docs/07).
        "tiers": {
            "free_sample": {"price_usd": 0.0, "status": "live", "endpoint": "/v1/insider/sample",
                            "returns": "1 most-recent parsed record — schema + quality proof"},
            "lookup": {"price_usd_per_record": payments.PRICE_USD, "status": "live",
                       "endpoints": ["/v1/insider/latest", "/v1/insider/{ticker}"],
                       "returns": "parsed Form 4 records, filterable — priced PER RECORD returned "
                                  "(a 50-record pull costs 50x price_usd_per_record), not flat per "
                                  "call, so it can't undercut the bulk tiers at volume"},
            "bulk": {"price_usd": payments.BULK_PRICE_USD, "status": "live",
                     "endpoint": "/v1/insider/bulk",
                     "returns": f"up to {SNAPSHOT_CAP} most-recent records (rolling snapshot), one call"},
            "bulk_10k": {"price_usd": payments.BULK_10K_PRICE_USD, "status": "live",
                         "endpoint": "/v1/insider/bulk/10k",
                         "returns": f"up to {payments.BULK_10K_LIMIT} most-recent records from the "
                                    "full archive, one call — roughly a week at average filing volume"},
            "by_date": {"price_usd": payments.DAILY_PRICE_USD, "status": "live",
                        "endpoint": "/v1/insider/by-date/{YYYY-MM-DD}",
                        "returns": "every record filed on one specific date, from the archive — "
                                   "same flat price as the bulk tier regardless of that day's count"},
            "scored_insider_signal": {"price_usd": payments.SIGNAL_PRICE_USD, "status": "roadmap",
                                      "returns": "deduped, SCORED per-ticker insider signal (a computed "
                                                 "score, beyond the live signals_cross_source envelopes)"},
            "cluster": {"price_usd": payments.CLUSTER_PRICE_USD, "status": "roadmap",
                        "returns": "multi-insider cluster detection across a window"},
            "signals_cross_source": {
                "status": "live",
                "endpoints": {
                    "sample": "/v1/signals/sample (free)",
                    "latest": "/v1/signals/latest?types=&direction=&min_strength=&limit= "
                              f"(per record: {payments.PRICE_USD})",
                    "by_ticker": f"/v1/signals/by-ticker/{{ticker}} (per record: {payments.PRICE_USD})",
                    "bulk": f"/v1/signals/bulk (flat: {payments.BULK_PRICE_USD})",
                },
                "returns": "records from every mapped source with a uniform `signal` envelope: "
                           "{signal_type, event, direction, strength, scope, lag_days?, rationale?}",
                "signal_types": {
                    "insider_trade": "SEC Form 4 — insider open-market buys/sells per ticker; "
                                     "10b5-1 plan trades downgraded to low strength",
                    "material_event": "SEC 8-K — item-code taxonomy (bankruptcy, restatement, "
                                      "delisting, auditor change...) per ticker",
                    "congress_trade": "House STOCK Act PTRs — member trades per ticker, amount "
                                      "band, disclosure lag_days",
                    "gov_contract_award": "USASpending — federal awards >= $10M resolved to a "
                                          "public recipient",
                    "drug_approval": "openFDA — FDA approvals within 120 days, sponsor resolved "
                                     "to a ticker",
                    "institutional_holding": "SEC 13F-HR — manager holdings per ticker (~45-day "
                                             "lag, direction=context by design)",
                    "bank_stress": "FDIC quarterly financials — capital-ratio / negative-income "
                                   "flags, exception-only",
                    "futures_positioning": "CFTC CoT — managed-money net positioning per "
                                           "commodity/index, sector- or market-scoped",
                    "auction_demand": "Treasury auctions — bid-to-cover classified vs. "
                                      "documented heuristic bands, us_rates-scoped",
                    "macro_release": "FRED headline series (pending API key)",
                    "sanction_listing": "BIS Entity List additions (pending API key)",
                },
                "honesty": "direction='context' = no direction claimed; strength = event-type "
                           "prior (docs/10), never a backtested score; lag_days = disclosure "
                           "lag so stale signals can't masquerade as fresh",
            },
            "watch": {"status": "live", "model": "prepaid retainer (not pay-per-call)",
                      "endpoint": "/v1/watch/subscribe",
                      "price_usd_per_month": {"base": watch.WATCH_BASE_USD, "per_entity": watch.WATCH_ENTITY_USD},
                      "term_discounts": watch.TERM_DISCOUNTS,
                      "sla": "matches pushed within ~5 min of EDGAR publication (~2.5 typical) + parse; "
                             "bounded by EDGAR's own dissemination",
                      "returns": "proactive push of matching Form 4 filings for a watchlist (webhook + poll)"},
        },
        # For any agent weighing "scrape EDGAR myself" vs "buy from us": the answer, with the math.
        # diy_cost_usd_per_filing is what it costs an agent to fetch + LLM-parse ONE Form 4 filing
        # itself to our accuracy (rented inference, ~2,500in/400out tokens x2 agentic overhead,
        # cheapest capable model tier). Live tiers only — no savings claim for roadmap
        # endpoints that aren't purchasable yet. Full methodology: docs/03-product-edgar-form4.md.
        "diy_comparison": {
            "claim": "it is cheaper to buy this data from us than to scrape and parse EDGAR yourself",
            "methodology": "cost for an AI agent to fetch a Form 4 filing from EDGAR and parse it "
                            "to our accuracy (transaction-code semantics, 10b5-1 flag, footnotes, "
                            "indirect ownership) — inference + electricity, incl. agentic tool-call "
                            "round-trip overhead. This is the FLOOR: it assumes the cheapest capable "
                            "model and excludes enrichment and the engineering cost of the edge "
                            "cases, so real DIY cost is higher",
            "diy_cost_usd_per_filing": diy,
            "tiers": {
                "lookup": {
                    "our_price_usd": payments.PRICE_USD,
                    "cheaper_than_diy_by_x": round(diy / payments.PRICE_USD, 1),
                    "note": "priced per record, so this ratio holds at any volume pulled through "
                            "this tier — for a real volume discount on top of it, step up to the "
                            "bulk tiers below",
                },
                "bulk": {
                    "our_price_usd": payments.BULK_PRICE_USD,
                    "snapshot_record_count": n,
                    "diy_cost_usd_to_replicate_current_snapshot": round(n * diy, 2) if n else None,
                    "cheaper_than_diy_by_x": round((n * diy) / payments.BULK_PRICE_USD, 1) if n else None,
                    "note": f"computed live from the current snapshot record count (rolling "
                            f"window, capped at {SNAPSHOT_CAP} most-recent records)",
                },
                "bulk_10k": {
                    "our_price_usd": payments.BULK_10K_PRICE_USD,
                    "archive_record_count": n10k,
                    "diy_cost_usd_to_replicate": round(n10k * diy, 2) if n10k else None,
                    "cheaper_than_diy_by_x": round((n10k * diy) / payments.BULK_10K_PRICE_USD, 1) if n10k else None,
                    "note": f"computed live from actual archive depth, up to the "
                            f"{payments.BULK_10K_LIMIT}-record cap; fewer records available early "
                            "in the archive's life is reflected here, not hidden",
                },
                "by_date": {
                    "our_price_usd": payments.DAILY_PRICE_USD,
                    "typical_day_record_count": {"low": 930, "high": 1277},
                    "diy_cost_usd_for_a_typical_day": {
                        "low": round(930 * diy, 2), "high": round(1277 * diy, 2),
                    },
                    "cheaper_than_diy_by_x": {
                        "low": round((930 * diy) / payments.DAILY_PRICE_USD, 1),
                        "high": round((1277 * diy) / payments.DAILY_PRICE_USD, 1),
                    },
                    "note": "typical_day_record_count is a 3-day empirical sample from SEC EDGAR's "
                            "own daily index (2026-06-26/29/30), not a live count for any specific "
                            "date — query the endpoint itself for an exact day's count",
                },
            },
            "sources": [
                "https://www.spheron.network/blog/ai-inference-power-electricity-cost-2026/",
                "https://www.eia.gov/electricity/monthly/update/end-use.php",
            ],
        },
    }


@app.get("/v1/insider/latest")
def insider_latest(
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    codes: str | None = Query(None, description="comma-separated transaction codes, e.g. P,S"),
):
    snap = _snapshot()
    records = snap.get("records", [])
    if codes:
        wanted = {c.strip().upper() for c in codes.split(",") if c.strip()}
        records = [r for r in records if r["transaction"].get("code") in wanted]
    records = records[:limit]
    if not records:  # nothing to sell -> free, no 402
        return {"count": 0, "as_of": snap.get("generated_at"), "records": []}
    # Per-record pricing (not flat-per-call): a batch of N costs PRICE_USD x N, charged for what's
    # actually returned. A flat per-call price here would let a 500-record pull undercut the bulk
    # tiers into irrelevance — see docs/03 "Per-tier math" for the reconciliation.
    return _paywall_or_serve(
        request, {"count": len(records), "as_of": snap.get("generated_at"), "records": records},
        price=payments.PRICE_USD * len(records), discovery_key="latest")


@app.get("/v1/insider/bulk")
def insider_bulk(request: Request):
    """The firehose: the entire current snapshot in one paid call (flat bulk price).

    No filters — bulk buyers want everything. Collapses thousands of per-lookup
    micropayments into a single transaction. Priced at BULK_PRICE_USD.
    """
    snap = _snapshot()
    records = snap.get("records", [])
    if not records:
        return {"count": 0, "as_of": snap.get("generated_at"), "records": []}
    return _paywall_or_serve(
        request,
        {"count": len(records), "as_of": snap.get("generated_at"), "records": records},
        price=payments.BULK_PRICE_USD, discovery_key="bulk",
    )


@app.get("/v1/insider/bulk/10k")
def insider_bulk_10k(request: Request):
    """The weekly-scale pull: up to BULK_10K_LIMIT (10,000) most-recent records, newest-day-first,
    assembled from the full-fidelity archive (not the 1,000-cap hot snapshot). Flat 3x-of-bulk
    price regardless of how many records are actually available (thin early history -> fewer than
    10,000, same price — see docs/03 for the daily-volume math this tier is sized against).
    """
    records = load_archive_recent(DATA_DIR, payments.BULK_10K_LIMIT)
    if not records:
        return {"count": 0, "records": []}
    return _paywall_or_serve(
        request, {"count": len(records), "records": records},
        price=payments.BULK_10K_PRICE_USD, discovery_key="bulk_10k",
    )


@app.get("/v1/insider/by-date/{on_date}")
def insider_by_date(request: Request, on_date: str):
    """One archived day's filings in full, at the same flat price as the 1,000-record bulk tier —
    regardless of that day's actual count (typically ~900-1,300; see docs/03). `on_date` is
    YYYY-MM-DD. Reads the full-fidelity archive/<date>.jsonl, never the flattened parquet.
    """
    try:
        on = date.fromisoformat(on_date)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "on_date must be YYYY-MM-DD"})
    records = load_archive_day(DATA_DIR, on)
    if not records:  # no filings that day (or day not yet archived) -> free, no 402
        return {"count": 0, "date": on_date, "records": []}
    return _paywall_or_serve(
        request, {"count": len(records), "date": on_date, "records": records},
        price=payments.DAILY_PRICE_USD, discovery_key="by_date",
    )


@app.get("/v1/insider/sample")
def insider_sample() -> dict:
    """Free proof rung: the single most-recent parsed record, no payment.

    Lets an agent verify schema + parse quality before paying — it can follow source_url back to
    the SEC filing and check our work. One record is proof, not a feed; the paid tiers deliver
    volume, coverage, and computed signal. Parsing is the product, so we free a bounded *sample*
    of parsed output, never parsing wholesale.
    """
    snap = _snapshot()
    records = snap.get("records", [])[:1]
    return {"count": len(records), "tier": "free-sample",
            "as_of": snap.get("generated_at"), "records": records}


@app.get("/v1/insider/{ticker}")
def insider_by_ticker(
    request: Request,
    ticker: str,
    since: str | None = Query(None, description="ISO date; filters by transaction_date"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
):
    snap = _snapshot()
    tkr = ticker.strip().upper()
    records = [r for r in snap.get("records", []) if (r.get("issuer") or {}).get("ticker") == tkr]
    if since:
        records = [r for r in records if (r["transaction"].get("transaction_date") or "") >= since]
    records = records[:limit]
    if not records:  # unknown ticker / no activity -> free, no 402
        return {"count": 0, "ticker": tkr, "as_of": snap.get("generated_at"), "records": []}
    return _paywall_or_serve(  # per-record, same reasoning as insider_latest above
        request, {"count": len(records), "ticker": tkr, "as_of": snap.get("generated_at"),
                  "records": records},
        price=payments.PRICE_USD * len(records), discovery_key="ticker")


# TODO(phase: upsell): GET /v1/insider/cluster — computed multi-insider cluster signal (~$0.25).


# --- Cross-source signal product (docs/13): uniform envelopes over every mapped feed --------------

_STRENGTH_RANK = {"low": 0, "medium": 1, "high": 2}


def _signal_records() -> list[dict]:
    """Signal-bearing records from every active source snapshot, plus serve-time-mapped Form 4
    records (the Form 4 loop predates the runner, so its snapshot has no stored envelopes).
    Newest-fetched first. Reads last-good snapshots only — same serving contract as /v1/insider."""
    out: list[dict] = []
    for mod in REGISTRY:
        snap = load_snapshot(DATA_DIR / "sources" / mod.SOURCE_ID) or {}
        for r in snap.get("records", []):
            if r.get("signal"):
                out.append({**r, "source": mod.SOURCE_ID})
    for r in _snapshot().get("records", []):
        sig = signals.map_form4(r)
        if sig:
            out.append({**r, "signal": sig, "source": "edgar-form4-insider"})
    out.sort(key=lambda r: r.get("fetched_at") or "", reverse=True)
    return out


def _filter_signals(records: list[dict], types: str | None, direction: str | None,
                    min_strength: str | None) -> list[dict]:
    if types:
        wanted = {t.strip() for t in types.split(",") if t.strip()}
        records = [r for r in records if r["signal"]["signal_type"] in wanted]
    if direction:
        records = [r for r in records if r["signal"]["direction"] == direction]
    if min_strength in _STRENGTH_RANK:
        floor = _STRENGTH_RANK[min_strength]
        records = [r for r in records if _STRENGTH_RANK[r["signal"]["strength"]] >= floor]
    return records


@app.get("/v1/signals/sample")
def signals_sample() -> dict:
    """Free proof rung, same philosophy as /v1/insider/sample: one signal-bearing record per
    source so an agent can inspect the envelope schema (and follow source_url to audit the
    mapping) before paying."""
    seen: dict[str, dict] = {}
    for r in _signal_records():
        seen.setdefault(r["source"], r)
    return {"count": len(seen), "tier": "free-sample", "records": list(seen.values())}


@app.get("/v1/signals/latest")
def signals_latest(
    request: Request,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    types: str | None = Query(None, description="comma-separated signal_type filter"),
    direction: str | None = Query(None),
    min_strength: str | None = Query(None, description="low|medium|high floor"),
):
    records = _filter_signals(_signal_records(), types, direction, min_strength)[:limit]
    if not records:
        return {"count": 0, "records": []}
    return _paywall_or_serve(  # per-record, same reasoning as insider_latest
        request, {"count": len(records), "records": records},
        price=payments.PRICE_USD * len(records), discovery_key="signals_latest")


@app.get("/v1/signals/bulk")
def signals_bulk(request: Request):
    records = _signal_records()
    if not records:
        return {"count": 0, "records": []}
    return _paywall_or_serve(
        request, {"count": len(records), "records": records},
        price=payments.BULK_PRICE_USD, discovery_key="signals_bulk")


@app.get("/v1/signals/by-ticker/{ticker}")
def signals_by_ticker(request: Request, ticker: str,
                      limit: int = Query(100, ge=1, le=MAX_LIMIT)):
    """Everything we know about one ticker, across every mapped source, one envelope schema."""
    tkr = ticker.strip().upper()
    records = [r for r in _signal_records()
               if (r["signal"].get("scope") or {}).get("ticker") == tkr][:limit]
    if not records:  # nothing known -> free, no 402
        return {"count": 0, "ticker": tkr, "records": []}
    return _paywall_or_serve(
        request, {"count": len(records), "ticker": tkr, "records": records},
        price=payments.PRICE_USD * len(records), discovery_key="signals_ticker")


# --- Watch / retainer product (docs/09): prepaid, proactive, push delivery -----------------------

def _resolve_watchlist(watchlist: list) -> tuple[list[dict], list]:
    resolved, unresolved = [], []
    for item in watchlist:
        r = edgar.resolve_to_cik(str(item))
        (resolved.append({"cik": r["cik"], "label": r["label"]}) if r else unresolved.append(item))
    return resolved, unresolved


@app.post("/v1/watch/subscribe")
def watch_subscribe(request: Request, body: dict = Body(...)):
    """Buy or extend a retainer (one endpoint — no recurring billing means renew == pay-again).

    `token` present -> EXTEND that subscription (reuse its watchlist, keep the same token/cursor).
    `token` absent  -> CREATE a new subscription from `watchlist`.
    Either way: quote, charge term_price via x402, and provision ONLY after settlement. An
    unresolvable/empty watchlist (or unknown token) is a free 4xx — never charged.
    """
    months = int(body.get("months", 1))
    token = body.get("token")
    if months not in watch.TERM_DISCOUNTS:
        return JSONResponse(status_code=400,
                            content={"error": "unsupported term", "allowed": sorted(watch.TERM_DISCOUNTS)})

    if token:  # --- extend an existing subscription ---
        sub = watch_store.get_subscription(token)
        if not sub:
            return JSONResponse(status_code=404, content={"error": "unknown token"})
        q = watch.quote(len(sub["entities"]), months)
        try:
            payments.ensure_paid(request, q["price_usd"], discovery_key="watch_renew")
        except payments.PaymentRequired as e:
            return JSONResponse(status_code=402, headers=e.headers, content={**e.body, "quote": q})
        base = max(datetime.now(timezone.utc), datetime.fromisoformat(sub["paid_through"]))
        new_through = (base + timedelta(days=30 * months)).isoformat()
        watch_store.extend(token, new_through)
        return {"token": token, "mode": "extended", "paid_through": new_through,
                "watching": sub["entities"], "quote": q}

    # --- create a new subscription ---
    watchlist = body.get("watchlist") or []
    webhook_url = body.get("webhook_url")
    if not isinstance(watchlist, list) or not watchlist:
        return JSONResponse(status_code=400, content={"error": "watchlist required (tickers, CIKs, or names)"})
    if webhook_url and not watch_delivery.safe_webhook_url(webhook_url):
        return JSONResponse(status_code=400,
                            content={"error": "webhook_url rejected — must be a public http(s) URL"})
    resolved, unresolved = _resolve_watchlist(watchlist)
    if not resolved:
        return JSONResponse(status_code=400,
                            content={"error": "no watchlist items resolved", "unresolved": unresolved})
    q = watch.quote(len(resolved), months)
    try:
        payments.ensure_paid(request, q["price_usd"], discovery_key="watch_subscribe")
    except payments.PaymentRequired as e:
        return JSONResponse(status_code=402, headers=e.headers,
                            content={**e.body, "quote": q, "resolved": resolved, "unresolved": unresolved})
    paid_through = (datetime.now(timezone.utc) + timedelta(days=30 * months)).isoformat()
    new_token = watch_store.create_subscription(resolved, paid_through, webhook_url=webhook_url)
    return {"token": new_token, "mode": "created", "paid_through": paid_through, "watching": resolved,
            "unresolved": unresolved, "quote": q,
            "delivery": "webhook+poll" if webhook_url else "poll",
            "poll_endpoint": f"/v1/watch/{new_token}/new"}


@app.get("/v1/watch/{token}")
def watch_status(token: str):
    sub = watch_store.get_subscription(token)
    if not sub:
        return JSONResponse(status_code=404, content={"error": "unknown token"})
    return {"token": token, "status": sub["status"], "paid_through": sub["paid_through"],
            "watching": sub["entities"], "webhook": bool(sub["webhook_url"])}


@app.get("/v1/watch/{token}/new")
def watch_new(token: str):
    """Free poll (they prepaid): hand back matches not yet picked up, then mark them consumed."""
    sub = watch_store.get_subscription(token)
    if not sub:
        return JSONResponse(status_code=404, content={"error": "unknown token"})
    pending = watch_store.unpolled(token)
    watch_store.mark_polled(token, [p["filing_id"] for p in pending])
    return {"count": len(pending), "paid_through": sub["paid_through"], "matches": pending}


# MCP layer — streamable-HTTP server for tool-using agents (server/mcp_server.py). Mounted
# before the static catch-all; its session manager runs inside _lifespan above.
app.mount("/mcp", mcp_server.http_app())

# Human layer — static Junkyard pages mounted last so /v1 and /health win.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
