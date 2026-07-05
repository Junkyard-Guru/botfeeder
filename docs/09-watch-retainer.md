# Watch / Retainer product — design (2026-06-29)

A non-pay-per-call product line. A bot **prepays a retainer**, gives us a **watchlist** (issuers,
insiders, funds — resolved to SEC **CIK**s), and we **proactively deliver** matching Form 4 filings
with bounded freshness. Unlimited pushes within the paid window.

## Why it's a different shape
The pull product is stateless request/response. This is **push + state + prepaid term**:
- **State:** per-subscriber watchlist, delivery config, paid-through, delivery cursor → SQLite on the VPS.
- **Push:** we reach the bot, not the reverse → webhook (primary) + private poll-feed (fallback).
- **Term:** x402 has no recurring primitive → a retainer is a **prepaid renewable window** (one x402
  settlement buys N months; re-pay to renew).

## Freshness SLA (honest, bounded)
Producer polls every 5 min; parse is sub-second. Delivery is **within ~5 min of the filing appearing on
EDGAR (~2.5 min typical) + parse**, bounded below by EDGAR's own dissemination (not in our control).
NOT "maximum freshness" (banned superlative; auditability rule).

**Reliability:** the watch loop queries EDGAR **directly per watched CIK**, NOT the 40-cap firehose —
complete + scoped to exactly what they paid for, immune to the post-4pm-ET burst that can exceed
`POLL_LIMIT`. Watch by **CIK** (exact); resolve tickers/names → CIK at signup (names aren't unique).

## Pricing
`monthly = WATCH_BASE ($2.00) + n_entities × WATCH_ENTITY ($0.40)` per 30 days.
Base covers fixed per-subscriber overhead (record, webhook retries, SLA); per-entity covers the variable
watching cost and scales with value. Cheaper than equivalent daily polling past ~2 entities, near-pure
margin (EDGAR queries are free).

**Term prepay discounts** (longer commitment → deeper discount; upfront cash + lock-in):

| Term | Discount | 5-entity example ($4.00/mo) |
|------|----------|------------------------------|
| 1 month | 0% | $4.00 |
| 6 months | 20% | $19.20 (vs $24.00) |
| 12 months | 50% | $24.00 (vs $48.00) |

`term_price = monthly × months × (1 − discount)`. A 12-month prepay obligates the SLA for the full
year (delivery/refund owed on the remainder if we sunset).

## Components & build order
1. **`server/watch.py`** — pricing/term math (pure, TDD). ← this milestone
2. **`server/watch_store.py`** — SQLite: subscriptions, watch_entities, deliveries (dedup cursor). ← this milestone
3. **`POST /v1/watch/subscribe`** — body `{watchlist, months, webhook_url?}` → resolve→CIK → quote →
   x402 pay `term_price` → on settle, create sub (token, paid_through) → return token + quote. Provision
   ONLY after settlement.
4. **Watch loop** (producer cycle) — per active sub, per watched CIK: query EDGAR for new Form 4s since
   cursor → parse → deliver → advance cursor.
5. **Delivery** — webhook signed POST + retry/backoff (primary); `GET /v1/watch/{token}/new` free poll
   (fallback; they prepaid).
6. **Renew** — `POST /v1/watch/{token}/renew` → pay → extend paid_through.
7. **`/v1/meta`** — publish the `watch` tier + term table.

## Endpoints
| Method | Path | Paid | Purpose |
|--------|------|------|---------|
| POST | `/v1/watch/subscribe` | x402 (term_price) | create subscription |
| GET | `/v1/watch/{token}` | free (prepaid) | subscription status (paid_through, watchlist, counts) |
| GET | `/v1/watch/{token}/new` | free (prepaid) | poll undelivered matches |
| POST | `/v1/watch/{token}/renew` | x402 (term_price) | extend the window |

## Hard rules carried over
Public-domain only; never charge for a bad/empty response; every claim auditable; private key never on
the server. Webhook URLs are buyer-supplied — validate scheme/host, sign payloads, cap retries, never
follow redirects to internal addresses (SSRF guard).
