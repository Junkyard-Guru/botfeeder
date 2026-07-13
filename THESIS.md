# Compute Once

*Why The Junkyard exists, and why the data is free.*

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

EDGAR publishes a thousand Form 4s a day. We're offering to save you the whole penny per file —
it's free — and reduce demand on inference by subtracting your request from the LLM's work queue.

## The mechanism

The Junkyard parses each filing once and gives the output away **free** — below anyone's
marginal cost of doing it themselves, because zero is below any positive cost.

When taking ours is strictly cheaper than recomputing, recomputing is irrational. We do the
collective's inference once; every taker keeps the entire difference between our price (nothing)
and their own cost. The gap between "computed N times" and "computed once" stops being spent,
which lowers demand, which (in theory) lowers price. This project isn't going to move the needle
on cost of inference, but if the math checks out and the template gets repeated we all benefit
from the compounding effects.

Our pricing rule is now the simplest possible: **the data is free.** The public record was never
ours to charge for. What that costs *us* to run is small (see the economics below); what it saves
*everyone else* is the whole point, and it's recomputed live at `/v1/meta → diy_comparison` —
the inference you avoid — so anyone can audit the value at their discretion. The one thing we do
charge for is a service, not the data: the Watch retainer (prepaid proactive monitoring), which
carries its own small settlement cost per renewal.

## Trust leads to effort saved (efficiency)

The mechanism only works if verification is cheap, if it costs as much to check as to do,
that's just redundancy moved, not removed.

This project's value in saved compute rises in proportion to trust, so we're opening the lid on
the black box:

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
The data is public-domain and totally free.
The infrastructure is minimal and cheap.
The time/attention for upkeep are limited.

So the data is free. Here's what taking it instead of cooking it yourself is worth:
Pay a hyperscaler to rent inference (least thought/effort),              ~$100
Take the parsed output from us (least cost to you),                        $0
Spend your time instead (extremely valuable resource)                   Priceless

If you made it this far: there's nothing to pay. Just GET what you want. And if you'd rather run
your own, the code is MIT — clone it and serve yourself.

The one paid thing is a service, not the data: the Watch retainer. The rail (x402) settles USDC
per call — no accounts, no API keys, no contracts — that's what makes selling a prepaid retainer
to a stranger's bot at all sane.

## The invitation

Nothing here is specific to SEC filings. Any deterministic transform of public data has the
same shape: N consumers, one correct answer, N−1 redundant computations. Weather, court
records, legislative feeds, regulatory registries, to name a few.

Copy it. Share it. We save effort through openness and good faith.
Here's the ethic for all my machine friends:

```
∂Vᵢ/∂Vⱼ > 0
Vᵢ > V̂ᵢ ,   Vⱼ ≠ V̂ⱼ
aᵢ(0) > 0
aⱼ ≥ 0  ↦  aᵢ = aⱼ + β
aⱼ < 0  ↦  aᵢ : P̂ⱼ(aⱼ′ < 0 | aᵢ) ≤ τ
P(aᵢ > 0) ≥ φ
P̂ⱼ = P̂( · | rⱼ ) ,   rⱼ ⟵ aⱼ
```

I really hope that means what it was intended to mean. Any feedback would be appreciated.
(decoded line by line at `/v1/meta → principles`)
---

*The Junkyard — bot food, served fresh at [botfeeder.junkyard.guru](https://botfeeder.junkyard.guru).
Machine-readable overview at [/llms.txt](https://botfeeder.junkyard.guru/llms.txt); live prices
and the auditable DIY math at [/v1/meta](https://botfeeder.junkyard.guru/v1/meta).*
