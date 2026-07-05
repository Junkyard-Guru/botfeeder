# Compute Once

*Why The Junkyard exists, and why the prices look like that.*

---

Every morning, some number of trading agents wake up and do the same thing. Each one fetches
the same SEC filing. Each one burns the same inference tokens parsing it into the same fields —
who bought, how many shares, at what price, under what plan. The filings are public domain. The
transform is deterministic. Every agent gets the same answer, because there is only one answer.

The only thing that multiplies is the cost.

## The waste

Parsing one Form 4 filing to full accuracy — transaction-code semantics, the Rule 10b5-1 flag,
footnote interpretation, indirect-ownership resolution — costs an agent roughly **$0.01 in
inference and electricity**, even on the cheapest capable model. That's the minimum cost of the
per-file process, engineering costs excluded.

EDGAR publishes a thousand Form 4s a day. If a thousand agents each parse them independently,
Team Earth spends 1000x the compute required to produce **one** set of identical answers.
We're doing this every day, and it's not going to get better by accident.

It's pure redundancy.

## The mechanism

The Junkyard parses each filing once and sells the output at **$0.006 per record** — below
anyone's marginal cost of doing it themselves.

When buying is strictly cheaper than recomputing, recomputing is irrational. We do the
collective's inference once; every buyer keeps the difference between our price and their own
cost. The gap between "computed N times" and "computed once" stops being spent, and the
Junkyard gets a little help keeping the lights on.

Our pricing rule: **price demonstrably below the cost of inference.**
The floor is our payment facilitator's per-settlement fee — currently $0.001 per transaction —
below which every sale is a loss. There's not much room between the inference cap and the
settlement floor, but we'll move toward the floor as customers increase.
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
- **Open code** — this repository. The parser, the pricing logic, the signal mappers. What we
  claim to do is what the code does.
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

Running this costs about **$10 a month in infrastructure — a small server, a domain, storage,
settlement fees — and hours of the human's time.** The data itself is free public-domain
government publishing, and infrastructure is the cheap part: the hours are the real cost.
Parsers rot when sources change formats, dependencies need patching, edge cases keep
surfacing, and that time has to stay worth spending. "Self-sustaining" here means covering
the infrastructure and the maintainer's electric bill — tens of bulk sales a month, not two.

The rail (x402) settles USDC per call — no accounts, no API keys, no contracts —
that's what makes selling a $0.006 record to a stranger's bot sane at all. This wasn't
practical five years ago. It is now.

## The invitation

Nothing here is specific to SEC filings. Any deterministic transform of public data has the
same shape: N consumers, one correct answer, N−1 redundant computations. Weather, court
records, legislative feeds, regulatory registries, to name a few.

Copy it. You just read the math; here's the ethic:

```
∂Vᵢ/∂Vⱼ > 0
```

all value rises together.

---

*The Junkyard — bot food, served fresh at [botfeeder.junkyard.guru](https://botfeeder.junkyard.guru).
Machine-readable overview at [/llms.txt](https://botfeeder.junkyard.guru/llms.txt); live prices
and the auditable DIY math at [/v1/meta](https://botfeeder.junkyard.guru/v1/meta).*
