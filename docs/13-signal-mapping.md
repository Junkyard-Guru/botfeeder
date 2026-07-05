# 13 — Data → Signal Mapping

Every active source's records now carry a uniform machine-readable `signal` envelope, attached
by the producer (`producer/signals.py` via `producer/runner.py`) and served cross-source at
`/v1/signals/*`. North star: **maps to reality in a manner useful to trading-agent customers.**

## What our customers already have, and what they don't

An LLM trading agent already knows public macro history, reads news, and can fetch any URL.
What it does NOT have cheaply: parsed, normalized, ticker-resolved **primary-source event
records** with honest semantics — that's the product. So the envelope answers exactly the four
questions a raw record leaves open:

| Field | Question | Values |
|---|---|---|
| `signal_type` / `event` | what kind of event is this? | taxonomy below |
| `scope` | who does it touch? | `{ticker}` \| `{sector}` \| `{market}` \| `{entity}` |
| `direction` | which way does it lean? | `bullish` / `bearish` / `neutral` / **`context`** |
| `strength` | how strong a prior is the event type? | `high` / `medium` / `low` |
| `lag_days` | how stale is it, honestly? | disclosure lag where computable |

**Honesty rules (load-bearing, same docs/07 spirit):**
- `direction: "context"` means *we are not claiming a direction*. Earnings 8-Ks, 13F holdings,
  macro prints get `context` — fabricating bullish/bearish there would be a false claim.
- `strength` is a coarse prior from the **event type** (the source-evaluation pass's strength ratings), never a
  backtested score. Nothing here claims predictive power; it claims faithful event mapping.
- A record with no `signal` key is data without a mapped signal (healthy bank, no-ticker
  asset, ancient approval) — still served/archived, never dressed up.
- Ticker resolution (`producer/tickermap.py`) is **conservative**: exact-match after
  normalization against EDGAR's own company_tickers.json; ambiguous names resolve to `None`
  rather than guess. A false ticker attribution is worse than none.

## Active mappings (10 sources + the Form 4 flag)

| Source | signal_type | Scope | Direction logic | Strength logic |
|---|---|---|---|---|
| **Form 4** (serve-time mapped) | `insider_trade` | ticker (native) | P→bullish, S→bearish, else context | P high / S medium; 10b5-1 plan trades → low (mechanical, not conviction) |
| **8-K** | `material_event` | ticker via CIK, else entity | Item-code taxonomy: bankruptcy/restatement/delisting/auditor-change/impairment/debt-acceleration → bearish; earnings/control/officer changes → context | Severity-ranked item table in `signals.py` (`1.03` bankruptcy outranks `9.01` exhibits); exhibits-only filings → no signal |
| **House PTR** | `congress_trade` | ticker (native) | P→bullish, S→bearish, E→neutral | ≥$250k low-band high; ≤$15k band low (index-drip noise); else medium. `lag_days` = notification − transaction |
| **USASpending** | `gov_contract_award` | ticker via recipient name | bullish (award = revenue) | ≥$1B high, ≥$10M medium, <$10M no signal (procurement noise); unresolved recipient → no signal |
| **openFDA** | `drug_approval` | ticker via sponsor name | bullish | ORIG approval high, supplemental low; only within 120 days (the corpus is decades of history — old approvals are not signals) |
| **13F-HR** | `institutional_holding` | ticker via issuer name | **context** (a static holding is positioning, not a trade) | low; put/call option positions → medium (`institutional_put_position` etc.). `lag_days` ≈ 45 (period → filed) |
| **FDIC** | `bank_stress` | ticker via bank name, else entity | bearish, **exception-only** — 4,300 healthy banks are data, not signals | equity/assets <5% high, <8% medium; negative net income medium |
| **CFTC CoT** | `futures_positioning` | commodity→sector map (energy/metals/ag) or market (us_equity_index/us_rates/fx/crypto) | managed-money net long→bullish / net short→bearish | net >15% of open interest = crowded → medium; 5–15% → low; <5% → no signal |
| **Treasury** | `auction_demand` | `{market: us_rates}` | strong demand→bullish, weak→bearish, avg→neutral | Documented heuristic bands (bills strong ≥2.8 / weak ≤2.4; notes/bonds ≥2.5 / ≤2.2) — calibration is heuristic, NOT a backtest |
| **FRED** *(pending key)* | `macro_release` | `{market: us_macro}` | context (no consensus data → no surprise direction) | low |
| **BIS Entity List** *(pending key)* | `sanction_listing` | ticker if resolved, else entity | bearish | high if public ticker, medium otherwise |

## Serving (machine-digestible, x402-priced)

- `GET /v1/signals/sample` — **free**: one signal-bearing record per source (schema proof,
  auditable via each record's `source_url`).
- `GET /v1/signals/latest?types=&direction=&min_strength=&limit=` — per-record price, merged
  across all sources, newest first.
- `GET /v1/signals/by-ticker/{ticker}` — per-record price: everything we know about one ticker
  (insider trades + 8-K events + congress trades + awards + approvals + holdings), one schema.
- `GET /v1/signals/bulk` — flat bulk price, every current signal-bearing record.
- `/v1/meta` `tiers.signals_cross_source` self-describes the taxonomy (agents discover it there).

Pricing reuses the existing ladder (`PRICE_USD` per record, `BULK_PRICE_USD` flat) — no new
payment mechanics. Empty results stay free (the docs/02 hard rule applies unchanged).

## Shelved — no signal mapping earns a poll slot (removed from REGISTRY, no more pulls)

Modules/tests stay in the repo (built, working, re-activatable by re-adding to REGISTRY).

| Source | Why it doesn't map |
|---|---|
| **Form D** | Filers are private companies — no tradeable security to scope a signal to. Resolving the rare public-parent case isn't worth a poll slot. |
| **World Bank** | Annual indicators published ~a year late. Agents already know macro history; nothing tradeable arrives here first. |
| **Eurostat** | EU monthly prints move markets via newswires agents already watch; ~zero incremental edge for US-listed-ticker customers. |
| **Census Int'l Trade** | Diffuse, 2-month-lagged aggregate macro; no per-company mapping. (Also keyless — key provisioning deferred.) |
| **DOL H-1B/LCA** | Visa filings as a hiring proxy: weak, quarterly-lagged, enormous volume for the signal value. Rated Low in the source-evaluation pass. |
| **FCC ULS/auctions** | Current module reaches only a frozen (2018) fallback dataset for one radio band — not live reality. Re-evaluate if real fcc.gov ULS/auction feeds are reachable from the VPS. |
| **MarineCadastre AIS** | Shelved 2026-07-03. Raw positions lack the MMSI→ticker mapping that would make them a signal; also the batch-size outlier of the source set. |

FRED and BIS Entity List remain **wired but dormant** (keyless no-ops costing zero requests)
with mappings ready — they self-activate when their env keys are set (see deploy/feedface.env.example).
Key provisioning is deferred (2026-07-03).

## Roadmap (real upgrades, deliberately not faked now)

- **13F quarter-over-quarter diffs** — new/exit/increase/decrease per manager beats static
  holdings; needs two archived quarters, so it unlocks with age.
- **CoT week-over-week positioning deltas** — change is spicier than level; needs prior-week
  state plumbed to the mapper.
- **FDIC deposit-flight flag** — QoQ deposit drop >5%; needs prior-quarter records.
- **FRED release-surprise direction** — needs a consensus-expectations source; without one,
  direction stays honestly `context`.
