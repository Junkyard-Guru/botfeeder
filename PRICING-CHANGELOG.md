# Pricing changelog

Every change to prices or the published descent cadence is logged here, with the reasoning.
The live commitment and our position on it: [`/v1/meta → pricing_cadence`](https://botfeeder.junkyard.guru/v1/meta).

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
