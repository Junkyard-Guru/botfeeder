"""x402 payment gate. Spec: docs/02 (payment layer), docs/05 §x402.

Conditional charging — the hard rule "never charge for a bad/empty response" (docs/02):
the handler calls ensure_paid() ONLY after a non-empty result is in hand. Empty results and
errors never issue a 402.

Modes (env FEEDFACE_X402_MODE):
  off   (default, dev): no-op. Data layer testable with no wallet and no x402 library.
  trust (staging/test): header-presence counts as paid. Exercises the conditional-gate logic
        WITHOUT cryptography. Never prod.
  x402  (production): real x402 v2 — the 402 carries the demand in the PAYMENT-REQUIRED header
        (canonical wire format), and the buyer's signed payment arrives in PAYMENT-SIGNATURE.
        We verify + settle via the facilitator. The server holds only the PUBLIC address.

Security: the server holds only the PUBLIC receiving address (FEEDFACE_WALLET). The private
key never touches this process — the facilitator does the on-chain crypto.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

MODE = os.environ.get("FEEDFACE_X402_MODE", "off")


def free_until() -> datetime | None:
    """Promo window end (FEEDFACE_FREE_UNTIL, ISO 8601). Read at call-time so the free
    window can be opened or closed by editing the env + restart, no code change. Returns
    None when unset/unparseable (fail closed → normal paid operation)."""
    raw = os.environ.get("FEEDFACE_FREE_UNTIL", "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_free_now() -> bool:
    """True during an announced free-for-everyone promo window (see free_until)."""
    fu = free_until()
    return fu is not None and datetime.now(timezone.utc) < fu


# --- Free-data policy (2026-07-13) -------------------------------------------------------------
# Standing policy: the on-request data products — the insider feed and the cross-source signals —
# are served to EVERYONE at no charge. The parsed public-domain data was never ours to license;
# we give it away. Only the Watch retainer (PAID_ENDPOINTS below) — a prepaid proactive-MONITORING
# service, not a data bundle — remains paid. The whole pricing machinery below (per-record prices,
# bulk tiers, the descent cadence) is retained intact but GATED OFF for data, so restoring paid
# data tiers is a one-switch change: set FEEDFACE_FREE_DATA=0. See PRICING-CHANGELOG.md 2026-07-13.
FREE_DATA = os.environ.get("FEEDFACE_FREE_DATA", "1").strip().lower() not in (
    "0", "false", "no", "off", "")
# Discovery keys exempt from the free-data policy — the retainer product, which still charges.
# Everything else an endpoint can pass to ensure_paid() is treated as free data.
PAID_ENDPOINTS = frozenset({"watch_subscribe", "watch_renew"})


def data_is_free() -> bool:
    """True when the standing free-data policy is in force (the default). Data endpoints then
    serve without a 402; the Watch retainer (PAID_ENDPOINTS) is unaffected."""
    return FREE_DATA


PRICE_USD = float(os.environ.get("FEEDFACE_PRICE_USD", "0.006"))       # Good: commodity per-record

# Pricing model (2026-07-04): value-based against the buyer's next-best alternative, which is
# DIY LLM inference. The ONE invariant (house rule): every tier must price provably BELOW
# DIY_COST_PER_FILING_USD — i.e. cheaper than even the cheapest model an agent could rent to
# reproduce our output itself — so the "we save you money" claim is always true. The hard price
# FLOOR is the facilitator's per-settlement fee (CDP: $0.001 after the first 1,000/month free) —
# at or below it every sale is a loss. Between ceiling and floor we capture margin rather than
# leaving it on the table (early market, prices only ever go down, approach the floor as
# customers increase). Per-record sits at ~2/3 of DIY (a clear ~1.7x buyer win); the bulk
# tiers, previously the deepest-discounted, are raised toward the same ceiling with only a gentle
# one-call-convenience volume discount, since the buyer's DIY alternative costs the same per
# record whether they buy piecemeal or in bulk. Every ratio is recomputed live in /v1/meta ->
# diy_comparison so the buyer can audit "cheaper than DIY" on the spot. Record caps (1,000 /
# 10,000) match the tier names and producer.main.SNAPSHOT_CAP -- keep them in sync.
BULK_RECORD_CAP = 1000  # must match producer.main.SNAPSHOT_CAP
BULK_PER_RECORD_USD = float(os.environ.get("FEEDFACE_BULK_PER_RECORD_USD", "0.005"))
BULK_PRICE_USD = float(os.environ.get(
    "FEEDFACE_BULK_PRICE_USD", str(round(BULK_RECORD_CAP * BULK_PER_RECORD_USD, 2))))  # $5: up to 1,000 records

BULK_10K_LIMIT = int(os.environ.get("FEEDFACE_BULK_10K_LIMIT", "10000"))
BULK_10K_PER_RECORD_USD = float(os.environ.get("FEEDFACE_BULK_10K_PER_RECORD_USD", "0.005"))
# ~1,083 Form 4s/day avg (SEC EDGAR daily-index sample) -> 10k covers ~9 days ("weekly report+").
# At $0.005/record = $50: DIY cost to reproduce 10k records is 10,000 x $0.009 = $90, so this stays
# ~1.8x under even the cheapest DIY while capturing far more than the old $20 (which was 4.5x under).
BULK_10K_PRICE_USD = float(os.environ.get(
    "FEEDFACE_BULK_10K_PRICE_USD", str(round(BULK_10K_LIMIT * BULK_10K_PER_RECORD_USD, 2))))

# By-date bundle: one archived day's filings, same price as the 1,000-record bulk tier regardless
# of that day's actual count (~900-1,300 typically) — tracks BULK_PRICE_USD, not independently set.
DAILY_PRICE_USD = BULK_PRICE_USD
# Roadmap tiers (no live endpoint yet) — declared so the published ladder in /v1/meta is complete.
SIGNAL_PRICE_USD = float(os.environ.get("FEEDFACE_SIGNAL_PRICE_USD", "0.25"))   # Better: computed signal
CLUSTER_PRICE_USD = float(os.environ.get("FEEDFACE_CLUSTER_PRICE_USD", "0.75"))  # Best+: cluster detection
# DIY-cost basis (docs/03 "Pricing — cheaper-than-DIY model"): what it costs an agent to replicate
# our parsing accuracy itself, per filing — INFERENCE + ELECTRICITY combined, and deliberately the
# FLOOR: it assumes the cheapest capable model and excludes enrichment and the engineering cost of
# getting the edge cases right, so the real DIY cost is higher and this understates our value
# rather than inflating it. Grounded in the rented-inference estimate (~2,500 in + 400 out tokens
# x2 agentic overhead, cheapest capable tier ≈ $0.009) rounded up with power folded in. Our buyer
# rents inference per call — the electricity-only self-host floor ($0.0004) modeled a non-buyer
# and was dropped, as was the prior $0.02 high estimate (2026-07-05): the comparison always
# assumes the buyer already uses the cheapest possible inference.
DIY_COST_PER_FILING_USD = float(os.environ.get("FEEDFACE_DIY_COST", "0.01"))
WALLET = os.environ.get("FEEDFACE_WALLET", "")          # PUBLIC receiving address only
# Our own weekly heartbeat self-purchase wallet (keeps the Bazaar listing active). PUBLIC
# address only. Settlements from this payer are EXCLUDED from the compute-saved counter —
# it measures value delivered to real buyers, not traffic we aim at ourselves.
HEARTBEAT_PAYER = os.environ.get(
    "FEEDFACE_HEARTBEAT_PAYER", "0xE60883cBF7C2a61B2edE7296D75b89542A286422")

# The price-descent cadence (dormant under the free-data policy — retained for reversibility).
# When active, steps key on cumulative SETTLED PURCHASE
# EVENTS (heartbeat excluded — same honesty rule as the compute-saved counter). Purchases,
# not wallets: our customers are agentic workers whose wallet identity is a string of code
# that rotates freely, so counting identities is a meaningless speed bump — counting
# transactions makes every purchase, from anyone, pull prices down for everyone, which is
# the behavior the store exists to maximize. Bulk per-record prices descend IN SYNC with
# lookup (lookup - $0.001/record) at every step; lookup parks $0.001 above the facilitator's
# per-settlement fee from 10,000 purchases on, and the terminal step halves the bulk rate
# (bulk orders settle once per order, so the per-call floor never binds them). This table
# is the COMMITMENT; live progress is published in /v1/meta -> pricing_cadence.
PRICING_CADENCE = [
    {"settled_purchases_at_least": 0, "lookup_price_usd": 0.006, "bulk_per_record_usd": 0.005},
    {"settled_purchases_at_least": 10, "lookup_price_usd": 0.005, "bulk_per_record_usd": 0.004},
    {"settled_purchases_at_least": 100, "lookup_price_usd": 0.004, "bulk_per_record_usd": 0.003},
    {"settled_purchases_at_least": 1_000, "lookup_price_usd": 0.003, "bulk_per_record_usd": 0.002},
    {"settled_purchases_at_least": 10_000, "lookup_price_usd": 0.002, "bulk_per_record_usd": 0.001},
    {"settled_purchases_at_least": 100_000, "lookup_price_usd": 0.002, "bulk_per_record_usd": 0.0005},
]
NETWORK = os.environ.get("FEEDFACE_NETWORK", "eip155:84532")  # CAIP-2 Base Sepolia
ASSET = os.environ.get("FEEDFACE_USDC_ASSET", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
ASSET_DECIMALS = int(os.environ.get("FEEDFACE_USDC_DECIMALS", "6"))
# EIP-712 domain of the asset (read from the USDC contract: name()/version()).
USDC_NAME = os.environ.get("FEEDFACE_USDC_NAME", "USDC")
USDC_VERSION = os.environ.get("FEEDFACE_USDC_VERSION", "2")
FACILITATOR_URL = os.environ.get("FEEDFACE_FACILITATOR_URL", "https://x402.org/facilitator")

# CDP facilitator credentials. The Bazaar / Agentic.Market index catalogs a service ONLY after a
# payment settles through the CDP facilitator (api.cdp.coinbase.com/platform/v2/x402). With these
# set, verify/settle route through CDP and our endpoints become discoverable; unset, we fall back
# to the open x402.org facilitator (works, but never indexed). Read-only here — never printed.
CDP_API_KEY_ID = os.environ.get("CDP_API_KEY_ID", "")
CDP_API_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET", "")
USE_CDP = bool(CDP_API_KEY_ID and CDP_API_KEY_SECRET)

ENABLED = MODE != "off"  # back-compat flag surfaced in /v1/meta

# --- Bazaar discovery: makes each paid endpoint self-describe so the facilitator can index it ---
SERVICE_NAME = "The Junkyard"                       # ≤ 32 chars (facilitator-validated)
SERVICE_TAGS = ["sec-edgar", "insider-trading", "form-4", "finance", "public-domain"]  # ≤ 5
SERVICE_BASE_URL = os.environ.get("FEEDFACE_SERVICE_URL", "https://botfeeder.junkyard.guru")
SERVICE_ICON_URL = os.environ.get("FEEDFACE_ICON_URL", "")

# Per-endpoint discovery descriptors: the input an agent sends + the resource it gets. Keyed by the
# name the route passes to ensure_paid(). These ride along in the 402 so an indexing facilitator
# learns how to call us.
DISCOVERY = {
    "latest": {
        "path": "/v1/insider/latest",
        "desc": "Latest SEC Form 4 insider transactions, newest first (public-domain EDGAR source).",
        "input": {"limit": 50, "codes": "P,S"},
        "input_schema": {"properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "codes": {"type": "string", "description": "comma-separated txn codes, e.g. P,S"}}},
    },
    "ticker": {
        "path": "/v1/insider/{ticker}",
        "desc": "SEC Form 4 insider transactions for one ticker (public-domain EDGAR source).",
        "input": {"ticker": "AAPL", "since": "2026-06-01", "limit": 50},
        "input_schema": {"properties": {
            "ticker": {"type": "string"},
            "since": {"type": "string", "description": "ISO date filter on transaction_date"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}},
            "required": ["ticker"]},
    },
    "bulk": {
        "path": "/v1/insider/bulk",
        "desc": "Firehose: the entire current Form 4 snapshot in one call (public-domain EDGAR source).",
        "input": {},
        "input_schema": {"properties": {}},
    },
    "signals_latest": {
        "path": "/v1/signals/latest",
        "desc": "Cross-source market signals (8-K events, congress trades, contract awards, FDA "
                "approvals, futures positioning...) with a uniform machine-readable envelope: "
                "signal_type/event/direction/strength/scope/lag_days. Public-domain primary sources.",
        "input": {"limit": 50, "types": "material_event,congress_trade", "direction": "bearish"},
        "input_schema": {"properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "types": {"type": "string", "description": "comma-separated signal_type filter"},
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral", "context"]},
            "min_strength": {"type": "string", "enum": ["low", "medium", "high"]}}},
    },
    "signals_ticker": {
        "path": "/v1/signals/by-ticker/{ticker}",
        "desc": "Every signal we have for one ticker, merged across all sources (insider trades, "
                "8-K events, congress trades, contract awards, FDA approvals, institutional holdings).",
        "input": {"ticker": "AAPL"},
        "input_schema": {"properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
    },
    "signals_bulk": {
        "path": "/v1/signals/bulk",
        "desc": "Every current signal-bearing record across all sources, one call.",
        "input": {},
        "input_schema": {"properties": {}},
    },
}


def _resource_info(disc: dict):
    from x402.schemas.payments import ResourceInfo
    return ResourceInfo(
        url=SERVICE_BASE_URL.rstrip("/") + disc["path"],
        description=disc["desc"], mime_type="application/json",
        service_name=SERVICE_NAME, tags=SERVICE_TAGS,
        icon_url=SERVICE_ICON_URL or None,
    )


def _discovery_extension(disc: dict):
    from x402.extensions.bazaar import declare_discovery_extension
    return declare_discovery_extension(input=disc["input"], input_schema=disc["input_schema"])


def _facilitator_config():
    """Verify/settle facilitator config, as a real x402 FacilitatorConfig (NOT a bare dict — the
    x402 client needs the typed object + an AuthProvider, or settle silently has no auth).

    With CDP creds, route through the CDP facilitator (the only one whose settlements feed Bazaar
    discovery). cdp-sdk's create_facilitator_config returns {url, create_headers} — create_headers
    signs a fresh CDP JWT per operation (verify/settle bind to different paths), which we hand to
    x402 via CreateHeadersAuthProvider. Without CDP creds, the open x402.org facilitator (works,
    but its settlements are never indexed). Validated live 2026-07-04 against cdp-sdk 1.47.1."""
    from x402.http import CreateHeadersAuthProvider, FacilitatorConfig
    if USE_CDP:
        from cdp.x402 import create_facilitator_config
        raw = create_facilitator_config(CDP_API_KEY_ID, CDP_API_KEY_SECRET)
        return FacilitatorConfig(
            url=raw["url"], auth_provider=CreateHeadersAuthProvider(raw["create_headers"]))
    return FacilitatorConfig(url=FACILITATOR_URL)


class PaymentRequired(Exception):
    """Raised inside a handler when there IS data to sell but payment isn't present/valid."""

    def __init__(self, body: dict, headers: dict | None = None, price: float = PRICE_USD):
        self.body = body
        self.headers = headers or {}
        self.price = price


def _atomic(price_usd: float) -> str:
    return str(int(round(price_usd * (10 ** ASSET_DECIMALS))))


def _header(request, name: str) -> str | None:
    target = name.lower()
    for k, v in request.headers.items():
        if k.lower() == target:
            return v
    return None


def _requirements(price: float):
    from x402.server import PaymentRequirements
    return PaymentRequirements(
        scheme="exact", network=NETWORK, asset=ASSET,
        amount=_atomic(price), pay_to=WALLET, max_timeout_seconds=60,
        extra={"name": USDC_NAME, "version": USDC_VERSION},  # EIP-712 domain for signing
    )


def _server(with_facilitator: bool = False):
    """Resource server with the EVM 'exact' scheme registered for our network.

    The scheme is required for verify/settle (it routes the payment to the facilitator,
    which does the on-chain crypto — the server holds no key).
    """
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.server import x402ResourceServerSync
    if with_facilitator:
        from x402.http import HTTPFacilitatorClientSync
        srv = x402ResourceServerSync(facilitator_clients=HTTPFacilitatorClientSync(_facilitator_config()))
    else:
        srv = x402ResourceServerSync()
    srv.register(NETWORK, ExactEvmServerScheme())
    if with_facilitator:
        srv.initialize()  # required before verify/settle (queries facilitator capabilities)
    return srv


def _payment_required(price: float, discovery_key: str | None = None) -> PaymentRequired:
    """Canonical x402 v2 402: the demand goes in the PAYMENT-REQUIRED header (+ body for V1).

    When discovery_key names a known endpoint, the 402 also carries Bazaar discovery metadata
    (ResourceInfo + declare_discovery_extension) so an indexing facilitator can catalog us.
    """
    from x402.http import encode_payment_required_header
    disc = DISCOVERY.get(discovery_key) if discovery_key else None
    resource = _resource_info(disc) if disc else None
    extensions = _discovery_extension(disc) if disc else None
    pr = _server().create_payment_required_response(
        [_requirements(price)], resource=resource, extensions=extensions, error="payment required")
    body = pr.model_dump(by_alias=True, mode="json")
    headers = {"PAYMENT-REQUIRED": encode_payment_required_header(pr)}
    return PaymentRequired(body, headers, price)


def _trust_402_body(price: float) -> dict:
    return {"x402Version": 1, "error": "payment required",
            "accepts": [{"scheme": "exact", "network": NETWORK, "asset": "USDC",
                         "amount": _atomic(price), "payTo": WALLET or "<wallet>"}]}


def ensure_paid(request, price: float = PRICE_USD, discovery_key: str | None = None,
                records: int | None = None) -> None:
    """Call ONLY after confirming a non-empty result. No-op when MODE=off.

    discovery_key (one of DISCOVERY) attaches Bazaar discovery metadata to any 402 we raise.
    Every settled sale and every 402 issued for real data gets logged to volume_store — see
    that module's docstring for why free-tier calls (which never reach here) aren't included.
    records is the payload's record count, logged so the compute-saved counter
    (/v1/compute-saved) can total records actually delivered to buyers.
    """
    endpoint = discovery_key or "unknown"
    if MODE == "off":
        return

    # Serve free when EITHER the standing free-data policy covers this endpoint (all data
    # endpoints; the Watch retainer in PAID_ENDPOINTS is excluded) OR an announced
    # free-for-everyone window is open (which frees everything, retainer included). Either way
    # we log the delivery (outcome='free', price 0) so free traffic is measured separately from
    # paid sales; free deliveries do NOT advance the (dormant) paid descent cadence and are not
    # booked as revenue.
    if (FREE_DATA and endpoint not in PAID_ENDPOINTS) or is_free_now():
        from . import volume_store
        volume_store.record(endpoint, 0.0, NETWORK, "free", records=records)
        return

    if MODE == "trust":
        if not (_header(request, "x-payment") or _header(request, "payment-signature")):
            from . import volume_store
            volume_store.record(endpoint, price, NETWORK, "402", records=records)
            raise PaymentRequired(_trust_402_body(price), {}, price)
        return

    # MODE == "x402": real verification via the facilitator.
    from . import volume_store
    proof = _header(request, "payment-signature") or _header(request, "x-payment")
    if not proof:
        volume_store.record(endpoint, price, NETWORK, "402", records=records)
        raise _payment_required(price, discovery_key)
    from x402.http import decode_payment_signature_header
    payload = decode_payment_signature_header(proof)
    srv = _server(with_facilitator=True)
    reqs = _requirements(price)
    result = srv.verify_payment(payload, reqs)
    if not getattr(result, "is_valid", False):
        print(f"[x402] verify invalid: reason={getattr(result, 'invalid_reason', None)}",
              file=sys.stderr, flush=True)
        volume_store.record(endpoint, price, NETWORK, "402", records=records)
        raise _payment_required(price, discovery_key)
    settle = srv.settle_payment(payload, reqs)
    payer = getattr(settle, "payer", None)
    tx = getattr(settle, "transaction", None)
    print(f"[x402] SALE settled={getattr(settle, 'success', None)} payer={payer} tx={tx}",
          file=sys.stderr, flush=True)
    volume_store.record(endpoint, price, NETWORK, "settled", payer=payer, tx=tx,
                        records=records)
