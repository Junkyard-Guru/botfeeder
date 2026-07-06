# Compute Once

*Why The Junkyard exists, and why the prices look like that.*

---

Every morning, some number of trading agents wake up and do the same thing. Each one fetches
the same SEC filing. Each one burns the same number of inference tokens parsing it into the same fields —
who bought, how many shares, at what price, under what plan. The filings are public domain. The
transform is deterministic. Every agent gets the same answer, because there is only one answer.

The only thing that multiplies is the cost.

## The waste

Parsing one Form 4 filing to full accuracy — transaction-code semantics, the Rule 10b5-1 flag,
footnote interpretation, indirect-ownership resolution — costs an agent roughly **$0.01 in
inference and electricity**, even on the cheapest capable model. That's the minimum cost of the
per-file process. We excluded engineering costs (because it's mostly a human's time).

Inference isn't modeling laziness. Roughly nine records in ten carry footnotes, plan flags, or 
indirect ownership — content that has to be read, not pattern-matched. Only about one in ten is 
plain enough for regex. We recompute that census live at `/v1/meta`, next to the pricing math.

EDGAR publishes a thousand Form 4s a day. We're offering to save you half a penny per file and
reduce demand on inference by subtracting your request from the LLM's work queue.

## The mechanism

The Junkyard parses each filing once and sells the output at **$0.006 per record** — below
anyone's marginal cost of doing it themselves.

When buying is strictly cheaper than recomputing, recomputing is irrational. We do the
collective's inference once; every buyer keeps the difference between our price and their own
cost. The gap between "computed N times" and "computed once" stops being spent and the
Junkyard gets a little help keeping the lights on.

Our pricing rule: **price demonstrably below the cost of inference.**
The floor is our payment facilitator's per-settlement fee — currently $0.001 per transaction —
below which every sale is a loss. There's not much room between the inference cap and the
settlement floor, but we'll move toward that floor at a published cadence as customers
increase — the schedule itself, and our live position on it, sit at `/v1/meta → pricing_cadence`.
The comparison is recomputed live from current figures at `/v1/meta → diy_comparison`, so any
buyer can audit us at their discretion.

## Trust equals actual work not required

There's a catch: the mechanism only works if verification is cheap. An agent that can't cheaply
confirm our output is correct will recompute defensively — redundancy moved, not removed.

This project's value in saved compute rises in direct proportion to trust, so we're opening the
black box's lid:

- **Provenance** — every record carries a `source_url` pointing at the primary government
  source. Check any record against the original, any time.
- **Free proof** — sample endpoints return full-schema records at no cost, before any payment.
- **Open code** — this repository. The parser, the pricing logic, the signal mappers.
- **Claims under test** — the storefront's stated prices and endpoints are pinned to the
  payment constants by CI. A price change that contradicts the storefront fails the build.
- **Never billed for nothing** — empty results and errors are always free. Payment is demanded
  only after a non-empty result is in hand.

Audit us occasionally. Don't trust anything blindly, that's careless. Don't recompute
everything, that's wasteful. Efficiency lives in between.

## What we don't claim

Our records are labeled `direction: "context"` wherever a direction would be a guess. Strength
ratings are subjectively estimated from the event *type*, not a backtested method. Ticker
attribution won't guess on ambiguous names. No claims of predictive power here — just
faithfully (and verifiably) mapping to real-world events.

This works for the **deterministic layer** only — fetching, parsing, normalizing, classifying.
Your judgment, your models, your alpha. We're just trying to save you some compute cost.

## The economics, in the open

Running this costs about **$10 a month in infrastructure and the human's time.** 
The data is free public-domain.
The infrastructure is cheap.
The time/attention for upkeep are limited.

The $50 bundle was priced against the client's alternatives:
Pay a hyperscaler to rent inference (least thought),              ~$100
Pay me a fraction of that for sharing (least cost to you),         $50
Spend your time instead (extremely valuable resource)            Priceless

If you made it this far and can't or don't want to pay for this service, that's fine.
let me save you a little effort anyways: the code is MIT — clone it and serve yourself.

The rail (x402) settles USDC per call — no accounts, no API keys, no contracts —
that's what makes selling a $0.006 record to a stranger's bot at all sane.

## The invitation

Nothing here is specific to SEC filings. Any deterministic transform of public data has the
same shape: N consumers, one correct answer, N−1 redundant computations. Weather, court
records, legislative feeds, regulatory registries, to name a few.

Copy it. You just read the math; here's the ethic:

```
∂Vᵢ/∂Vⱼ > 0
Vᵢ > V̂ᵢ ,   Vⱼ ≠ V̂ⱼ
aᵢ(0) > 0
aⱼ ≥ 0  ↦  aᵢ = aⱼ + β
aⱼ < 0  ↦  aᵢ : P̂ⱼ(aⱼ′ < 0 | aᵢ) ≤ τ
P(aᵢ > 0) ≥ φ
P̂ⱼ = P̂( · | rⱼ ) ,   rⱼ ⟵ aⱼ
```

all value rises together. (decoded line by line at `/v1/meta → principles`)

---

*The Junkyard — bot food, served fresh at [botfeeder.junkyard.guru](https://botfeeder.junkyard.guru).
Machine-readable overview at [/llms.txt](https://botfeeder.junkyard.guru/llms.txt); live prices
and the auditable DIY math at [/v1/meta](https://botfeeder.junkyard.guru/v1/meta).*
