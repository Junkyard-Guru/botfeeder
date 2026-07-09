"""Weekly heartbeat self-purchase.

Buys the cheapest paid endpoint from our own storefront through the CDP facilitator, from the
dedicated heartbeat wallet. Two reasons, both operational:

1. CDP's Bazaar/discovery catalog drops services with no recent settlement activity (~30 days)
   and re-indexes on settlement — a weekly settled call keeps every listed endpoint alive and
   re-nudges indexing.
2. It is a full end-to-end production probe: storefront up, 402 demand well-formed, facilitator
   verify+settle working, USDC actually moves. A silent break in any of those fails loudly here.

Honesty coupling: the server EXCLUDES this wallet's settlements from the public compute-saved
counter (server/payments.py HEARTBEAT_PAYER) — heartbeats keep us listed; they never inflate
the number the thesis stands on.

Cost: price of one lookup ($0.006) + facilitator fee ($0.001 beyond the free 1,000/mo) per week.
Env: FEEDFACE_HEARTBEAT_BUYER_KEY (0x-prefixed private key of the heartbeat wallet),
     FEEDFACE_HEARTBEAT_URL (default https://botfeeder.junkyard.guru/v1/insider/latest?limit=1).
Exit codes: 0 = settled 200 with data; 1 = anything else (systemd surfaces the failure).
"""
from __future__ import annotations

import os
import sys

import httpx
from eth_account import Account
from x402 import x402ClientSync
from x402.http import x402HTTPClientSync
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import register_exact_evm_client

URL = os.environ.get("FEEDFACE_HEARTBEAT_URL",
                     "https://botfeeder.junkyard.guru/v1/insider/latest?limit=1")
NETWORK = os.environ.get("FEEDFACE_NETWORK", "eip155:8453")  # Base mainnet


def main() -> int:
    # Self-heal across a free-week promo: during an announced free window every endpoint
    # serves without payment, so a settlement is impossible and a 200-without-payment is
    # EXPECTED, not a fault. Skip cleanly (regardless of key config) and let the timer resume
    # normal beats once the window closes — no manual toggle, no false alarm.
    meta_base = URL.split("/v1/")[0]
    try:
        promo = httpx.get(f"{meta_base}/v1/meta", timeout=30.0).json().get("promotion", {})
        if promo.get("free_for_everyone"):
            print(f"[heartbeat] free week active (until {promo.get('free_until')}) — "
                  "skipping self-purchase")
            return 0
    except Exception as e:  # noqa: BLE001 — meta probe is best-effort; fall through to normal beat
        print(f"[heartbeat] promo check failed (proceeding): {e}", file=sys.stderr)

    key = os.environ.get("FEEDFACE_HEARTBEAT_BUYER_KEY", "")
    if not key:
        print("[heartbeat] FEEDFACE_HEARTBEAT_BUYER_KEY not set", file=sys.stderr)
        return 1

    signer = EthAccountSigner(Account.from_key(key))
    client = x402ClientSync()
    register_exact_evm_client(client, signer, networks=NETWORK)
    http_client = x402HTTPClientSync(client)

    with httpx.Client(timeout=60.0) as c:
        first = c.get(URL)
        if first.status_code == 200:
            # Payment layer off or endpoint free — that's a config problem worth alarming on:
            # no settlement happened, so the listing was NOT refreshed.
            print("[heartbeat] got 200 without payment — x402 layer looks OFF", file=sys.stderr)
            return 1
        if first.status_code != 402:
            print(f"[heartbeat] unexpected status {first.status_code}: {first.text[:200]}",
                  file=sys.stderr)
            return 1

        pay_headers, _payload = http_client.handle_402_response(
            dict(first.headers), first.content)
        second = c.get(URL, headers=pay_headers)

    if second.status_code == 200 and second.json().get("records"):
        print(f"[heartbeat] OK — settled purchase of {URL}")
        return 0
    print(f"[heartbeat] paid retry failed: {second.status_code} {second.text[:200]}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
