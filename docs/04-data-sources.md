# 04 — Data Sources: law vs. contract vs. risk

## The one rule

**Prefer Layer-1-clean data** — sources the government affirmatively lets you reuse. That's the
strongest possible footing and removes contract risk entirely. Where we use a Layer-2 source, do
it eyes-open about the contract, not because the law forbids alternatives.

## Source map

Column meaning: **Law** = what statute actually permits/forbids. **Contract** = what an agreement
would bind you to *if accepted*. None of the rows below are "illegal" — the restrictions are
contractual.

| Source | Cost | Law | Contract | Verdict |
|---|---|---|---|---|
| **SEC EDGAR** (Form 4, 8-K, 13F, S-1, XBRL) | $0 | ✅ Public domain — gov grants reuse | none | **Primary. Ship it.** |
| **FRED** (Fed economic series) | $0 | ✅ Public domain | none | Clean expansion |
| **Congressional trades** (STOCK Act) | $0 | ✅ Public record | none | Same pipeline as Form 4 |
| **Polymarket** | $0 — public, no-auth API | ✅ On-chain public facts (Feist) | ToS may discourage; not binding if not accepted | Low-competition expansion; low risk |
| **Kalshi** | Free API | facts not protected | ToS may restrict — read it; operator's discretion | Possible |
| **CoinGecko (commercial)** | $35/mo | facts not protected | 🔒 license *permits* resale | Clean-by-contract crypto path |
| **Crypto direct** (Binance/Coinbase/Kraken) | Free | facts not protected | 🔒 ToS varies; resale may be barred | operator's call; not a law issue |
| **News/sentiment** (Alpha Vantage, Finnhub, EODHD) | paid | facts not protected | 🔒 license bars reselling *their feed* | Wrap the *primary* public source instead |
| **Alpaca / Yahoo / SIP equity** | ~$0 | facts not protected | 🔒 signed feed agreement bars resale; civilly enforced + audited | Don't resell — a real, money-backed *contract* (not a crime) |
| **Exchange direct / IEX** | $800–$20k+/mo | facts not protected | 🔒 redistribution allowed *for a fee* | Not at flag economics |

## Why the "equity wall" exists (and that it's contractual, not legal)

US exchanges own their feeds and are regulator-mandated to report to the consolidated tape (SIP).
They license that data down the whole chain — **$32 to $20,000+/month per exchange**,
vendor-agnostic. A price itself is a fact (not copyrightable, *Feist*) — **so the wall is not
copyright law.** It's that you only *get* the feed by signing a contract, and the contract binds
you (Layer 2). Alpaca/Yahoo forbid redistribution because *their* upstream contract forbids it.
EDGAR/FRED/on-chain sources have **no such contract** — that's the whole game.

> A derived-use license (what Databento negotiated) would let us resell exchange data — a hard,
> costly, scale-dependent commercial+legal negotiation, *achievable* but unnecessary while we
> operate in the public-domain lane. Revisit only if we outgrow it.

## Expansion sequence (all share the cron-produce / serve-last-good pipeline)

1. **EDGAR Form 4** — insider transactions (the flag). High demand, structured XML.
2. **Congressional trades** — same disclosure-data shape, strong public interest.
3. **EDGAR 8-K / 13F** — material events; quarterly institutional holdings.
4. **FRED** — macro catalyst series.
5. **Polymarket** — prediction-market outcomes; "the only signal that can move before price."

Each is a new producer module + a few endpoints; the server, payment layer, and architecture are
unchanged. That's the "floodgates" lever.
