"""MCP (Model Context Protocol) server — The Junkyard's front door for tool-using agents.

Mounted on the main FastAPI app at /mcp (streamable HTTP, stateless), so any MCP-capable
agent can add `https://botfeeder.junkyard.guru/mcp` and browse the store with native tools
instead of raw HTTP. Surfaces: samples, live meta, the compute-saved counter, and a quote that
teaches the agent how to buy the one paid product — the Watch retainer — over x402 out-of-band.
The data itself is free; the retainer is paid on the x402 rail where the buyer's own wallet
signs, which is the whole point of the store (no accounts, no custody, no API keys).

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
        "The Junkyard serves parsed public-domain market data to AI agents FREE — no payment, "
        "no account, no API key; just GET what you want. Tools here prove the schema, show live "
        "meta, and total the inference the world has avoided. The one paid product is the Watch "
        "retainer (prepaid proactive monitoring), bought over x402 — use junkyard_payment_quote "
        "to learn how."
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
    """Live self-description: products, the free-data policy, the auditable inference-you-avoid
    math, and the maker's-mark principles (the same document served at /v1/meta)."""
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
    """How to buy the one paid product — the Watch retainer (prepaid proactive monitoring).
    All DATA is free (just GET it); only the retainer charges. Payment happens on the HTTP
    rail with your own wallet over x402 — this MCP server never touches your funds."""
    from server import payments, watch

    return {
        "data_is_free": True,
        "note": "All on-request data (/v1/insider/*, /v1/signals/*) is free — no payment, no "
                "account, no API key. Just GET it. Only the Watch retainer below is paid.",
        "watch_retainer": {
            "endpoint": "POST /v1/watch/subscribe",
            "model": "prepaid proactive-monitoring retainer (webhook + poll)",
            "price_usd_per_month": {"base": watch.WATCH_BASE_USD,
                                    "per_entity": watch.WATCH_ENTITY_USD},
            "term_discounts": watch.TERM_DISCOUNTS,
        },
        "how_to_buy": [
            "1. POST /v1/watch/subscribe with your watchlist bare -> HTTP 402; the response "
            "carries the x402 payment demand (asset, amount, payTo, network) and a price quote",
            "2. Sign the USDC authorization with your own wallet (any x402 client library: "
            "handle_402_response -> payment headers)",
            "3. Retry the same POST with the payment headers -> 200, the retainer is provisioned",
            "You are never billed for an empty/unresolvable watchlist or an error.",
        ],
        "network": payments.NETWORK,
    }


def http_app():
    """The ASGI app to mount at /mcp (path='/' because the mount supplies the prefix)."""
    return mcp.streamable_http_app()
