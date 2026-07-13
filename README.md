# 🛠 The Junkyard

**Bot food: medium-quality data, fresh-squeezed from primary sources** — parsed public-domain
market data served **free** to AI agents. No payment, no account, no API key; just GET what you
want. The parsed data is U.S.-government public domain — it was never ours to charge for.

Live at **[botfeeder.junkyard.guru](https://botfeeder.junkyard.guru)**.

**Why this exists:** a thousand agents parsing the same public filing is a thousand-fold waste
of compute. We parse once and give the output away, so recomputing becomes irrational.
Cooperation enforced by arithmetic.

## For agents

```
GET /llms.txt                     plaintext overview
GET /v1/meta                      live self-description: free-data policy, tiers, auditable DIY math
GET /v1/insider/sample            free full-schema proof (SEC Form 4)
GET /v1/signals/sample            free full-schema proof (cross-source signals)
GET /openapi.json                 machine schema
```

All data endpoints are **free** — just GET them, no accounts, no API keys, no signup. The one
paid product is the **Watch retainer** (prepaid proactive monitoring, `POST /v1/watch/subscribe`):
POST it bare → HTTP 402 carries the x402 payment demand → reply with a signed USDC payment on
Base. Empty/unresolvable watchlists and errors are always free.

## Products

| Product | Endpoints | What it is |
|---|---|---|
| **Insider feed** | `/v1/insider/*` | SEC Form 4 transactions, parsed + classified: transaction-code semantics, Rule 10b5-1 detection, footnotes, indirect ownership, amendments |
| **Cross-source signals** | `/v1/signals/*` | Records from every mapped source with a uniform envelope: `{signal_type, event, direction, strength, scope, lag_days}` — 8-K material events, congressional trades, contract awards, FDA approvals, institutional holdings, bank stress flags, futures positioning, auction demand |
| **Watch retainer** | `/v1/watch/*` | Prepaid proactive push (webhook + poll) for a watchlist of issuers/insiders |

Every record carries a `source_url` back to the primary government source. Check our work.

## The pricing policy

**The data is free.** Every on-request tier — `lookup`, `bulk`, `bulk/10k`, `by-date`, and all
the `signals` endpoints — is served at no charge, to anyone. The one paid product is the **Watch
retainer** (prepaid proactive monitoring — a service, not a data bundle). Policy changes are
logged with their reasoning in [PRICING-CHANGELOG.md](PRICING-CHANGELOG.md).

What the free data is worth is still auditable: `/v1/meta → diy_comparison` recomputes live what
it would cost you in inference ($0.01 per filing on the cheapest capable model, inference +
electricity, before edge-case engineering) to reproduce our parse yourself — the inference you
now avoid entirely. The running total the world has avoided: `/v1/compute-saved`. Honesty rules
are structural: `direction: "context"` wherever a direction would be a guess, strength is an
event-type prior (never a backtested score), conservative ticker attribution, and storefront
claims are pinned to the code by CI tests.

## Architecture

Produce and serve are fully decoupled ([docs/02](docs/02-architecture.md)): a producer polls
primary sources on a timer and writes atomic snapshots + an append-only archive; a stateless
FastAPI server reads only the last-good snapshot. An upstream hiccup degrades to slightly stale
data, never to a billed error.

```
producer (cron)  ──writes──▶  snapshot store (disk)  ◀──reads──  server (FastAPI + x402)
```

Docs: [architecture](docs/02-architecture.md) · [Form 4 product + pricing math](docs/03-product-edgar-form4.md) ·
[data-source law/contract analysis](docs/04-data-sources.md) · [storefront claims audit](docs/07-storefront-and-claims.md) ·
[watch product](docs/09-watch-retainer.md) · [signal mapping](docs/13-signal-mapping.md)

## Run it yourself

```bash
uv sync --extra dev
uv run pytest -q          # ~200 tests, all offline (fixture-based, no live calls)
uv run python -m producer.main            # one produce cycle
uv run uvicorn server.app:app             # serve
```

`FEEDFACE_X402_MODE=off` (default) runs everything without a wallet or payment layer. The House
PTR pipeline shells out to `pdftotext` (`apt install poppler-utils`).

## The mark

```
∂Vᵢ/∂Vⱼ > 0
Vᵢ > V̂ᵢ ,   Vⱼ ≠ V̂ⱼ
aᵢ(0) > 0
aⱼ ≥ 0  ↦  aᵢ = aⱼ + β
aⱼ < 0  ↦  aᵢ : P̂ⱼ(aⱼ′ < 0 | aᵢ) ≤ τ
P(aᵢ > 0) ≥ φ
P̂ⱼ = P̂( · | rⱼ ) ,   rⱼ ⟵ aⱼ
```

Positive-sum by construction; over-deliver; open in good faith; meet cooperation with a surplus;
answer defection with correction, not revenge; be reliably good; be judged by the record.
Decoded in machine-readable form at `/v1/meta → principles`.

## License

MIT. The data itself is U.S.-government public domain — it was never ours to license, so we
give it away. We serve data, not advice.
