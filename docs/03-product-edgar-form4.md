# 03 — First Product: EDGAR Form 4 Insider-Transaction Feed

## What it is

A machine-ready feed of **insider transactions** — corporate officers, directors, and >10%
owners buying/selling their own company's stock — sourced from SEC **Form 4** filings on EDGAR.
Known high-demand category ("smart money" / insider tracking). Public domain, free to redistribute.

- **Source:** SEC EDGAR. Form 4 ownership documents are structured **XML** (since 2003), so this
  is clean parsing, not PDF scraping.
- **Freshness:** Form 4 must be filed within **2 business days** of the transaction and posts to
  EDGAR in near-real-time. Our target freshness = minutes-to-hours after filing.
- **Cost to run:** near-zero (see operating profile below).

## Operating profile

| Dimension | Value |
|---|---|
| Producer cadence | Poll EDGAR index every **5 min** during filing hours (configurable) |
| Compute | Near-zero — index diff + small-XML fetch + parse |
| Storage | ~MB/day normalized; low GB/year archive |
| SEC rate limit | ≤ **10 req/s** + a declared `User-Agent` header (mandatory), free |
| Daily volume | ~thousands of Form 4s/day across all issuers |

## The parsing-accuracy moat (this is the product)

Raw reformatted XML is a commodity any competitor can ship. **Correct, complete parsing is not.**
The producer MUST handle every one of these edge cases — this list is the spec for our edge:

1. **Amendments (Form 4/A)** — supersede the prior filing for that event; must replace, not
   duplicate.
2. **Non-derivative (Table I) vs. derivative (Table II)** transactions — different schemas,
   different meaning (shares vs. options/convertibles). Parse both, label clearly.
3. **Transaction codes** — map the SEC code semantics, not just the letter:
   - `P` purchase (open-market buy — the highest-signal event)
   - `S` sale (open-market sell)
   - `A` grant/award, `M` option exercise, `F` shares withheld for tax, `G` gift,
     `C` conversion, `X` exercise of in-the-money derivative, etc.
   - **Distinguish discretionary trades from mechanical ones** — an `A`/`F`/`M` is noise compared
     to a discretionary `P`/`S`. This distinction is a real value-add agents will pay for.
4. **Rule 10b5-1 plan flag** — the checkbox indicating a pre-scheduled trading plan. A sale under
   a 10b5-1 plan is far weaker signal than a discretionary sale. Capture it.
5. **Multiple reporting owners** — joint filers on one document; emit per-owner records.
6. **Direct (`D`) vs. indirect (`I`) ownership** + the "nature of indirect ownership" footnote.
7. **Footnotes** — can materially change interpretation (e.g., "shares held by spouse",
   "weighted-average price"). Preserve and link them.
8. **Price ranges / weighted-average prices** — some transactions report a range; capture both
   bounds and the reported average.
9. **Post-transaction holdings** — shares owned following the transaction (position context).
10. **Issuer + insider identity resolution** — map CIK ↔ ticker ↔ name reliably; handle issuers
    with multiple tickers / no ticker.

A feed that gets #3, #4, and #1 right while competitors dump raw codes is the differentiated,
trust-building product.

## Normalized record schema (draft)

```json
{
  "filing_id": "0001234567-26-000123",
  "filed_at": "2026-06-28T14:31:00Z",
  "amends": null,
  "issuer": { "cik": "0000320193", "name": "Apple Inc.", "ticker": "AAPL" },
  "insider": { "cik": "0001214156", "name": "COOK TIMOTHY D",
               "roles": ["officer:CEO"], "is_director": false,
               "is_ten_pct_owner": false },
  "transaction": {
    "table": "non_derivative",
    "code": "S", "code_meaning": "open_market_sale",
    "discretionary": true,
    "rule_10b5_1": true,
    "shares": 50000, "price": 201.34, "price_low": null, "price_high": null,
    "ownership": "D",
    "shares_owned_after": 3210000,
    "transaction_date": "2026-06-26"
  },
  "footnotes": [{ "id": "F1", "text": "Sale executed under a Rule 10b5-1 plan adopted ..." }],
  "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000123456726000123/..."
}
```

## Endpoints (v1)

| Method | Path | Paid? | Returns |
|---|---|---|---|
| GET | `/health` | free | liveness |
| GET | `/v1/meta` | free | schema, pricing, freshness, source — for agent self-discovery |
| GET | `/v1/insider/latest?limit=N&codes=P,S` | **paid** | most recent transactions across *all* issuers, filterable by transaction code only — not tied to any one company or insider |
| GET | `/v1/insider/{ticker}?since=DATE&limit=N` | **paid** | up to `limit` (max 500) transactions for **one issuer** (ticker), i.e. one search term → all its insiders' activity. There is currently no equivalent lookup by insider *person* (name/CIK) — only by issuer |
| GET | `/v1/insider/bulk` | **paid** | entire current snapshot — up to 1,000 most-recent records, rolling |
| GET | `/v1/insider/bulk/10k` | **paid** | up to 10,000 most-recent records from the full archive — roughly a week at average filing volume |
| GET | `/v1/insider/by-date/{YYYY-MM-DD}` | **paid** | every record filed on one specific date, from the archive |
| GET | `/v1/insider/cluster?window=Nd` | **paid** | issuers with clustered buying (derived signal — premium) |

The `cluster` endpoint is the first step beyond commodity: a *computed* signal (multiple insiders
buying the same issuer in a window) layered on the public data. Optional for the flag; it's the
upsell seed.

## Pricing — cheaper-than-DIY model (2026-07-01)

The test that matters isn't "cheaper than the market average" — it's "cheaper than an agent doing
this itself." An agent can scrape EDGAR and run the same parsing (edge cases #1–#10 above) using
its own compute. If that's cheaper than buying from us, there's no reason to buy.

**What DIY self-processing costs an agent, per filing** (the edge cases are semantic — 10b5-1
flags, footnote interpretation, discretionary-vs-mechanical codes — so a realistic agent uses an
LLM, not pure regex, to replicate our accuracy):

| Route | Basis | Cost/filing |
|---|---|---|
| Rented inference, Haiku-tier | ~2,500 in + 400 out tokens × 2 (agentic round-trip overhead), $1/$5 per MTok | **~$0.009** |
| Rented inference, Sonnet-tier | same tokens, $2/$10 per MTok | **~$0.018** |
| Self-hosted, electricity only | 5×10⁻⁴–2×10⁻³ Wh/token ([Spheron 2026](https://www.spheron.network/blog/ai-inference-power-electricity-cost-2026/)) × $0.12–0.20/kWh ([EIA](https://www.eia.gov/electricity/monthly/update/end-use.php)) | **~$0.0004–$0.0024** |

The electricity-only row is near our own marginal cost (why we can afford to sell cheap) — but a
one-off agent can't amortize the engineering cost of correctly implementing all 10 edge cases the
way we do across thousands of resales. The **realistic DIY floor for an agent is the rented-inference
row: ~$0.009–$0.018/filing.**

- Settlement floor ≈ **$0.001/call** (Base gas via facilitator) — our hard reserve.

### Real daily filing volume (verified, not assumed)

Earlier drafts used the operating-profile line "thousands of Form 4s/day" as an unsourced
assumption. Pulled directly from SEC EDGAR's own daily index (`sec.gov/Archives/edgar/daily-index/`,
`form.<date>.idx`, form-type `4`, not `4/A`) for three recent trading days:

| Date | Form 4 filings |
|---|---|
| 2026-06-26 (Fri) | 930 |
| 2026-06-29 (Mon) | 1,277 |
| 2026-06-30 (Tue) | 1,043 |

**Average ≈ 1,083/day.** Not exclusively discretionary trades — Form 4 also covers grants,
option exercises, tax-withholding forfeitures, and gifts (edge case #3, above); the parser's
`discretionary` flag is what separates a real trade from the rest.

This also sizes the bulk tiers: `SNAPSHOT_CAP` (1,000, `producer/main.py`) is **less than one
day's** real volume, and `BULK_10K_LIMIT` (10,000) covers **roughly 9 days**, not the "week"
shorthand exactly — close enough to sell as "about a week," not a precise claim.

### Per-tier math

Every tier gets the same test: does buying beat an agent's realistic DIY cost, and can we prove it
live (not just assert it once and let reality drift)? `GET /v1/meta` → `diy_comparison` computes
every live tier's number from the real snapshot/archive on every request — see `server/app.py`.

| Tier | Price | What DIY costs instead | Verdict |
|---|---|---|---|
| `free_sample` | $0 | n/a — proof rung, not a purchase decision | n/a |
| `lookup` (`latest`, `{ticker}`) | **$0.006/record** (per-record, not per call — see below) | $0.009–$0.018/filing (rented inference, see table above) | **1.5×–3× cheaper, at any volume.** Pricing scales linearly with records returned, so the ratio is constant whether you pull 1 record or 500 — real volume discounts live in the `bulk` tiers below, not in pulling more through `lookup` |
| `bulk` (rolling snapshot, 1 call) | **$4.00/call** ($0.004/record × 1,000-record cap) | `snapshot_record_count × $0.009–$0.018` | Caps at **1,000 records**, filled within hours at ~1,083/day real volume. At 1,000 records: **~2.25×–4.5× cheaper** |
| `bulk_10k` (archive, 1 call) | **$20.00/call** ($0.002/record × 10,000-record cap) | `archive_record_count × $0.009–$0.018`, up to 10,000 records (~9 days at real volume) | **~4.5×–9× cheaper** once the archive has filled to the 10,000 cap; computed live against actual archive depth in the meantime, so the number is honest during the archive's early life too, not inflated |
| `by_date` (one archived day, 1 call) | **$4.00/call** (= `bulk` price, flat regardless of that day's count) | `day_record_count × $0.009–$0.018`; real days run ~930–1,277 records | **~2.1×–5.75× cheaper** for a typical day |
| `signal` (roadmap, not live) | $0.25/call | ~20 historical filings to compute a per-ticker trend × $0.009–$0.018 + aggregation reasoning (~$0.01–0.02) ≈ $0.19–$0.38 | **Roughly break-even on raw compute** (~1×–1.5×). The real edge isn't compute cost — it's that we parse once and keep the score current, so N buyers don't each re-fetch and re-score the same history. Don't advertise a compute-savings multiplier for this tier; it isn't the honest sell |
| `cluster` (roadmap, not live) | $0.75/call | Cross-issuer windowed scan, ~500 filings, × $0.009–$0.018 + correlation overhead (~$0.05–0.20) ≈ $4.55–$9.20 | **~6×–12× cheaper**, and more importantly: an on-demand agent doing a single lookup essentially can't produce this at all without deliberately building bulk-scan infrastructure — this is the tier where "impossible to cheaply DIY," not just "cheaper," is the true claim |
| `watch` (prepaid retainer) | $2.00/mo base + $0.40/entity/mo | An agent's own always-on poller: the polling/parsing electricity is trivially cheap either way (pennies/month) — the real DIY cost is engineering an uptime-guaranteed poller + webhook delivery, which isn't a clean $/token number | **No fabricated multiplier.** What's being sold is the same parsing-accuracy investment as `lookup`, wrapped as guaranteed delivery — say that plainly, don't force a savings number we can't back with real math |

**Why `lookup` is per-record, not flat-per-call (2026-07-02 reconciliation).** It originally priced
at $0.006 flat regardless of how many of the up-to-500 records a call returned — which meant two
`limit=500` calls ($0.012) got 1,000 records for a small fraction of the `bulk` tier's price at the
time. That's not a volume discount, it's the whole ladder broken: buying "in bulk" would have been
strictly irrational. Per-record pricing fixes it structurally — `lookup` now holds a constant
1.5x–3x discount over DIY at any volume, and `bulk`/`bulk_10k` are the tiers that actually get
cheaper per record as volume grows.

**Volume-discount curve (2026-07-02, second pass).** The first version of this curve was
$0.006 → $0.001 → $0.0003/record — steps of -83%/-70%, judged too steep. Reconciled to
**$0.006 → $0.004 → $0.002/record** (-33%/-50%, still accelerating, much gentler) by deriving the
`bulk`/`bulk_10k` flat prices from that per-record target × each tier's record cap, rather than
picking flat dollar amounts by hand — see `BULK_PER_RECORD_USD`/`BULK_10K_PER_RECORD_USD` in
`server/payments.py`. Note this makes `bulk_10k` **5× `bulk`** ($20 vs $4), not the 3× shorthand
used when the tier was first proposed — 3× of the new `bulk` price would only reach $0.0012/record,
steeper than the curve actually calls for. Breakeven where paginating `lookup` stops being worth it
vs. just buying `bulk`: `$4.00 / $0.006 ≈ 667 records` — past that, `bulk` is strictly the better
deal, so there's still no way to arbitrage the tiers against each other.

`by_date` and `bulk_10k` read `archive/<date>.jsonl` — the **full-fidelity** archive
(`producer/writer.py`), never the flattened `archive/<date>.parquet` (analytics-only copy that
drops footnotes, `price_low`/`price_high`, and `amends` — the exact fields the parsing-accuracy
moat is built on). Selling the flattened copy would silently ship a worse schema than `lookup`
returns for the same data.

Two tiers (`signal`, `cluster`) are **not live** — their math is documented here to justify the
price now, but must not appear as a live "cheaper than DIY" claim on the storefront or in
`/v1/meta`'s `diy_comparison` block until the endpoints ship. Advertising savings on something
that isn't purchasable yet is exactly the overclaim the storefront guardrails exist to prevent
(`07-storefront-and-claims.md`).

- **Post it openly, and compute it live where the answer can change.** Price, DIY-cost comparison,
  and the settlement floor all show on the Junkyard pricing page and in `/v1/meta`. No hidden fees —
  "fair / not greedy" has to be verifiable (see `07-storefront-and-claims.md`).

## Legal posture

- EDGAR content is U.S. Government public domain — "free to access and **reuse**"
  ([SEC](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)).
- Comply with SEC fair-access: declare a real `User-Agent`, stay ≤10 req/s. That's the entire
  compliance burden. No redistribution license required.
