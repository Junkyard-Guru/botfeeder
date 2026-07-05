# 07 — The Junkyard Storefront + Claim Truth-Audit

## Identity

- **Internal codename:** FEEDFACE (what we call the project).
- **Public storefront brand:** **The Junkyard** — `botfeeder.junkyard.guru`.
- **Why a separate domain (`junkyard.guru`, not a subdomain of an existing personal site):** a branded identity not
  attached to any person, isolating the storefront into a self-contained unit that can be lifted onto a
  bigger/different VPS or plan later without untangling it from the personal site. (Migration is
  unlikely — this is deliberate headroom, not a prediction.)
- The customer that *pays* is the agent (x402). The human-facing layer exists for the developers
  who *select* feeds for their agents, and for anyone auditing us.

## The two layers on one host

`botfeeder.junkyard.guru` serves both:
1. **Machine layer (primary):** the x402-gated JSON API — `/health`, `/v1/meta`, `/v1/insider/*`.
2. **Human layer (utilitarian):** clean, professional, honest static pages — landing +
   provenance + processing + pricing. No fluff, no dark patterns. Its whole job is to let a human
   trust and *audit* us in under a minute.

## Landing copy (draft — utilitarian, honest)

> # 🛠 The Junkyard
> ### `botfeeder.junkyard.guru` — machine-readable data for agents
>
> We serve **medium-quality data, fresh-squeezed from primary sources.**
>
> We're not the fanciest feed on the lot, and we don't pretend to be. Here's our whole pitch:
>
> - **Honest provenance.** Every record traces to its primary source. Click through and check it
>   against the original yourself.
> - **Real processing.** We parse, normalize, classify, and pack the raw filings so your agent
>   doesn't burn tokens doing it.
>
> **Audit us.** Provenance and pricing are laid out below — nothing hidden. (No separate
> "processing" section until there's real content to put in it — an empty placeholder promising
> detail we haven't written is exactly the kind of claim these guardrails exist to catch.)
> → [Provenance](#) · [Pricing](#)

Tone: a well-run salvage yard that knows exactly what it has, tells you the truth about it, and
charges fair. Scrappy, transparent, no-BS. Professional layout, plain language.

## Claim truth-audit — every public claim must be backed BEFORE it ships

This is the contract with ourselves: **do not post a claim we can't substantiate.** Each row maps
a marketing claim to the fact that must hold and the mechanism that makes it auditable.

| Public claim | What must be true | How we make it true + auditable |
|---|---|---|
| "**fresh-squeezed from primary sources**" | A claim about **directness, not latency**: pulled **straight from SEC EDGAR** (the authoritative origin), not from concentrate via a reseller/aggregator. No to-the-minute promise is made or implied | Provenance page names EDGAR + the exact endpoints; **every record carries `source_url` (the live SEC filing), `filed_at`, `fetched_at`** so anyone can click through and verify against SEC. We poll regularly and stamp `fetched_at` — no speed SLA, because we don't sell one |
| "**medium-quality** data" | We don't oversell. Provenance is high (primary source); processing depth is honest-medium (parse + structure, no analytics/guarantees). The brand sets the bar low on purpose | Methodology states plainly what we do and **don't** do; no "best / guaranteed / investment-grade" language |
| "audit our **provenance**" | Source is fully disclosed and per-record verifiable | Provenance page + per-record `source_url`; we name every upstream endpoint |
| "real **processing**" | We genuinely transform raw XML into something more useful than the raw filing | Processing page lists the actual steps: parse XML → normalize fields → classify transaction codes → flag Rule 10b5-1 → supersede amendments (4/A) → emit per-owner records → compact packing |
| "efficient **packing** / optimized for our customers" | Payloads are measurably leaner/faster than raw EDGAR | Compact schema, gzip, pagination, field-selection (`?fields=`), filter by ticker/code, served from pre-built snapshot (~300 ms). Publish a before/after size example |
| "**fair / very competitive** pricing" | Price beats DIY, not just the going rate — and the buyer can check the math themselves against the current rented-inference rate, not just take our word for it | Pricing page posts the per-record price (**$0.006/record**, `lookup` tier — cheaper than an agent's own DIY parsing cost of **~$0.009–$0.018/filing** via rented LLM inference at any volume) next to the **~$0.001** settlement floor, with volume tiers (`bulk`, `bulk/10k`, `by-date`) getting progressively cheaper per record. Open math, no hidden fees — "posted openly" carries the fairness claim; we don't also assert we're "not gouging," that's for the buyer to conclude from the numbers |

## Claims we will NOT make (guardrails against our own overreach)

We make **no freshness/latency SLA at all** — "fresh-squeezed" is about directness from the source,
and the junkyard brand already signals modest, take-it-as-it-comes. So there's nothing to disclaim
on speed. The only guardrails:

- ❌ "complete / every filing" → "**all Form 4/4-A filings we successfully parse**," with parse
  failures logged and disclosed in `/v1/meta` counts.
- ❌ "highest accuracy / best data" → we claim **correct, auditable, medium-quality**, not best.
- ❌ any investment advice or signal-quality promise → we sell **data**, not recommendations.

## What "good sources + real processing" obligates us to build

To keep the claims honest, the producer (see `02`/`03`) MUST actually:
1. Hit **EDGAR directly** (declared User-Agent, ≤10 req/s) — no intermediary, so "primary source"
   is literally true.
2. Stamp every record with `source_url` + `filed_at` + `fetched_at` — provenance is built-in, not
   asserted.
3. Perform the documented processing steps — so "real processing" is demonstrable, not decorative.
4. Pack efficiently (compact schema, gzip, pagination, field-select) — so "optimized" is measurable.
5. Expose honest counts in `/v1/meta` (records, last fetch, parse-failure count) — so the human
   page's claims are continuously, automatically true rather than a one-time marketing snapshot.

If any of these isn't implemented, the corresponding claim comes **off** the human page until it is.
