"""MCP (Model Context Protocol) server — The Junkyard's front door for tool-using agents.

Mounted on the main FastAPI app at /mcp (streamable HTTP, stateless), so any MCP-capable
agent can add `https://botfeeder.junkyard.guru/mcp` and browse the store with native tools
instead of raw HTTP. Free surfaces only: samples, live meta, the compute-saved counter, and
a payment quote that teaches the agent how to buy the paid tiers over x402 out-of-band.
Paid data itself is NOT proxied here — payment stays on the x402 rail where the buyer's own
wallet signs, which is the whole point of the store (no accounts, no custody, no API keys).

Import discipline: tool bodies import from server.app lazily to avoid a circular import
(app.py mounts this module's ASGI app).
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Hosts this MCP endpoint answers for: the public domain (behind Caddy/Cloudflare) plus
# local dev/tests. A public data store has no cross-origin secrets to protect, but the SDK's
# DNS-rebinding guard is on by default in production, so name the hosts explicitly.
_ALLOWED_HOSTS = os.environ.get(
    "FEEDFACE_MCP_ALLOWED_HOSTS",
    "botfeeder.junkyard.guru,localhost:*,127.0.0.1:*,testserver").split(",")

mcp = FastMCP(
    "the-junkyard",
    instructions=(
        "The Junkyard sells parsed public-domain market data to AI agents, priced below "
        "the buyer's own inference cost. Free tools here prove the schema and show live "
        "prices; paid records are bought per call over x402 (HTTP 402 -> USDC on Base, "
        "no account) — use junkyard_payment_quote to learn how."
    ),
    stateless_http=True,
    json_response=True,
    # The FastAPI app mounts us AT /mcp already — serve at the sub-app's root, else the
    # effective path would be /mcp/mcp.
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(allowed_hosts=_ALLOWED_HOSTS),
)


@mcp.tool()
def junkyard_overview() -> str:
    """Plaintext overview of the store: products, prices, endpoints, honesty rules
    (the same document served at /llms.txt)."""
    from server.app import llms_txt
    return llms_txt()


@mcp.tool()
def junkyard_meta() -> dict:
    """Live self-description: products, tier prices, the auditable cheaper-than-DIY math,
    and the maker's-mark principles (the same document served at /v1/meta)."""
    from server.app import meta
    return meta()


@mcp.tool()
def junkyard_insider_sample() -> dict:
    """Free full-schema sample of the SEC Form 4 insider-transaction product — real parsed
    records, no payment, so you can validate the schema before buying."""
    from server.app import insider_sample
    return insider_sample()


@mcp.tool()
def junkyard_signals_sample() -> dict:
    """Free full-schema sample of the cross-source signals product (uniform envelopes over
    nine public-domain feeds), no payment."""
    from server.app import signals_sample
    return signals_sample()


@mcp.tool()
def junkyard_compute_saved() -> dict:
    """The running compute-saved counter: cumulative DIY inference cost avoided by buyers,
    with the full methodology (self-purchases excluded, conservative undercount)."""
    from server.app import compute_saved
    return compute_saved()


@mcp.tool()
def junkyard_payment_quote() -> dict:
    """How to buy the paid tiers: prices per endpoint and the exact x402 flow. Payment happens
    on the HTTP rail with your own wallet — this MCP server never touches your funds."""
    from server import payments

    return {
        "how_to_buy": [
            "1. GET the paid endpoint bare -> HTTP 402; the PAYMENT-REQUIRED header carries "
            "the x402 payment demand (asset, amount, payTo, network)",
            "2. Sign the USDC authorization with your own wallet (any x402 client library: "
            "handle_402_response -> payment headers)",
            "3. Retry the same GET with the payment headers -> 200 with the data",
            "You are never billed for empty results or errors.",
        ],
        "network": payments.NETWORK,
        "endpoints_usd": {
            "/v1/insider/latest | /v1/insider/{ticker}": payments.PRICE_USD,
            "/v1/signals/latest | /v1/signals/by-ticker/{ticker}": payments.PRICE_USD,
            "/v1/insider/bulk (<=1,000 records)": payments.BULK_PRICE_USD,
            "/v1/insider/bulk/10k (<=10,000 records)": payments.BULK_10K_PRICE_USD,
            "/v1/insider/by-date/{YYYY-MM-DD}": payments.DAILY_PRICE_USD,
            "/v1/signals/bulk": payments.BULK_PRICE_USD,
        },
        "pricing_invariant": "every tier is priced below the buyer's own DIY inference cost "
                             f"(${payments.DIY_COST_PER_FILING_USD}/record) — audit it live "
                             "at /v1/meta -> diy_comparison",
    }


def http_app():
    """The ASGI app to mount at /mcp (path='/' because the mount supplies the prefix)."""
    return mcp.streamable_http_app()
