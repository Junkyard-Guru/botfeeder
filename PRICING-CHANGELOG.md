# Pricing changelog

Every change to prices or policy is logged here, with the reasoning.
The live policy: [`/v1/meta → pricing`](https://botfeeder.junkyard.guru/v1/meta).

## 2026-07-13 — data is now free; only the Watch retainer is paid

- **Policy pivot: we no longer charge for data.** Every on-request tier — `lookup`, `bulk`,
  `bulk/10k`, `by-date`, and all the `signals` endpoints — is served **free** to everyone: no
  payment, no account, no API key. The parsed data is U.S.-government public domain; it was never
  ours to charge for, so we give it away. Anybody who wants the structured data can have it.
- **The one paid product is the Watch retainer** (`POST /v1/watch/subscribe`) — a prepaid
  proactive-monitoring service, not a data bundle. Its pricing is unchanged
  ($2.00/month base + $0.40/entity, term discounts to 35%).
- **Mechanics.** Implemented as an endpoint-scoped switch in `server/payments.py`
  (`FREE_DATA`, default on; Watch endpoints exempt via `PAID_ENDPOINTS`). The per-record prices,
  bulk tiers, and the descent cadence (`PRICING_CADENCE`) are retained but dormant — restoring
  paid data tiers is a one-switch change (`FEEDFACE_FREE_DATA=0`). `/v1/meta` now carries a
  `pricing` block (free data + the paid retainer) in place of `pricing_cadence`; the
  `diy_comparison` math is reframed from "cheaper than DIY" to "the inference you avoid, free."
- The compute-saved counter (`/v1/compute-saved`) now counts free deliveries as value delivered
  (a free delivery avoids 100% of the buyer's DIY inference cost), so the thesis instrument keeps
  growing under the free policy.
- **The descent cadence below is retired as a live commitment** — free is already past the floor
  it was walking toward. History kept for the record.

## 2026-07-05 — cadence rekeyed to purchase events; bulk descends in sync

- The descent cadence now steps on **cumulative settled purchase events**, not distinct
  wallets. Our customers are agentic workers whose wallet identity is a string of code that
  rotates freely — counting identities was a meaningless speed bump, and transactions are
  exactly the thing this store wants more of. Every purchase, from anyone, now moves
  everyone toward cheaper.
- Bulk per-record prices now descend **in lock-step** with the lookup price
  (lookup − $0.001/record) at every step, instead of waiting for the terminal step.
- The terminal step (100,000 purchases) softened from a 90% slash on non-lookup tiers to a
  **50% halving** — the sync-descent above already delivers most of the cut earlier.

## 2026-07-05 — descent cadence first published

- Thresholds at orders of magnitude: 10 → 100 → 1,000 → 10,000 → 100,000.
- Lookup steps $0.006 → $0.002, parking $0.001 above the facilitator's per-settlement fee —
  never on it, since a price at the fee makes every sale a loss.

## 2026-07-05 — initial public prices

- Lookup $0.006/record · bulk $5.00 (≤1,000 records) · bulk/10k $50.00 (≤10,000) ·
  by-date $5.00/day.
- Standing invariant: every tier prices below the buyer's own DIY inference cost
  ($0.01/filing on the cheapest capable model), recomputed live at `/v1/meta → diy_comparison`.
