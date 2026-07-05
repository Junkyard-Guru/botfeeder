# 02 — Architecture

## Core principle: decouple production from serving

The single most important design decision. **Never fetch-and-compute live per request.** Instead:

```
  ┌─────────────┐      writes       ┌──────────────┐      reads       ┌─────────────┐
  │  PRODUCER   │  ───────────────▶ │  SNAPSHOT     │ ◀─────────────── │   SERVER     │
  │  (cron/poll)│   last-good +     │  STORE        │   serve last-    │  (FastAPI +  │
  │  EDGAR fetch│   archive append  │  (disk: JSON/  │   good only      │   x402)      │
  │  + parse    │                   │   Parquet)    │                  │              │
  └─────────────┘                   └──────────────┘                  └─────────────┘
```

Why this matters:
- An upstream hiccup (EDGAR slow, schema change, parse error) degrades to **slightly stale data**,
  never to a charged 500. We never bill an agent for a bad response.
- Serving is a cheap static read → low latency, trivial compute, easy to keep up.
- **Scaling is a config change, not a rewrite.** "Open the floodgates" = widen the universe and
  raise the producer cadence. The server doesn't change.

## Components

### 1. Producer (`producer/`)
- Polls the EDGAR daily-index every N minutes during filing hours (default N=5).
- Diffs against last-seen; fetches only new Form 4 / 4-A filings.
- Parses each filing (lxml) → normalized records (see `03-product-edgar-form4.md` for schema and
  the edge-case spec that is our moat).
- Writes two outputs atomically:
  - **`snapshot/latest.json`** — the current serving view (recent N transactions, indexed by
    ticker). Atomic write (temp file + rename) so the server never reads a half-written file.
  - **`archive/YYYY-MM-DD.parquet`** — append-only historical record. This accumulates into the
    asset that never goes stale.
- On any parse/fetch failure: log, keep the prior `latest.json`, alert if N consecutive failures.

### 2. Snapshot store (`data/`, gitignored)
- Plain disk. No database needed at flag scale.
- `snapshot/latest.json` — small, hot, read on every request.
- `archive/*.parquet` — columnar, cheap, grows ~MB/day.
- Storage budget: single-digit MB/day normalized; low GB/year. Negligible vs. VPS 176 GB free.

### 3. Server (`server/`)
- **FastAPI** app. Each paid route protected by the **x402** middleware decorator.
- Reads only from `snapshot/latest.json` (and archive for historical endpoints). Stateless.
- Returns HTTP **402 Payment Required** with price metadata until the agent pays USDC on Base;
  the facilitator validates the payment proof and the request is served. Overhead ≈ 300ms on Base.
- Free, unpaid routes for agent discovery: `/health`, `/v1/meta` (schema + pricing self-description).
- `/v1/meta` also exposes honest live counts (records, last fetch, parse-failure count) so the
  human-layer claims stay continuously true, not a one-time snapshot.

### 4. Human layer (`web/`, static)
- Clean, utilitarian, honest pages served by FastAPI at the root: landing + provenance + processing
  + pricing. Job: let a human selector trust and **audit** us in under a minute. No dark patterns.
- Data stays gated; only the docs/marketing are human-facing. Copy + the claim truth-audit live in
  `07-storefront-and-claims.md`. **No claim ships unless it's backed.**

### 5. Payment layer (x402)
- **Receiving wallet:** a dedicated Base wallet address (USDC). Private key sealed via the existing
  DPAPI/age secret pattern — never in repo, never printed.
- **Facilitator:** hosted (Coinbase CDP facilitator) to start — it validates proofs, settles
  on-chain, and pays gas (~$0.001/settlement on Base L2). Self-hosting deferred.
- **Network:** Base Sepolia (testnet) for the dry-run, then Base Mainnet for the live flag.

## Tech stack (locked for the flag)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Matches the fleet stack; EDGAR XML parsing is clean in Python (lxml) |
| Web | FastAPI + uvicorn | x402 middleware is a one-line decorator on FastAPI |
| Payments | x402 (`fastapi-x402` or `coinbase/x402` FastAPI middleware) | Decide tomorrow — see deploy plan §x402 |
| Storage | JSON (serving) + Parquet (archive) | No DB at flag scale; columnar archive is the durable asset |
| Scheduler | systemd timer (or cron) on the VPS | Simple, robust, no orchestration layer |
| Host | existing VPS (Ubuntu 24.04) | Has RAM/CPU/disk headroom; already wired for git/gh |
| Edge/DNS | `botfeeder.junkyard.guru` → VPS (own domain `junkyard.guru`) | Branded, isolated from any personal domain; put DNS on Cloudflare for TLS + shield |
| Human layer | Static pages served by FastAPI (landing + provenance/processing/pricing) | Utilitarian, honest; lets human selectors audit us. See `07-storefront-and-claims.md` |

## Failure modes & guards

| Failure | Guard |
|---|---|
| EDGAR schema change breaks parser | Producer keeps last-good `latest.json`; alert after N failures |
| Half-written snapshot read by server | Atomic temp-file + rename on every write |
| Agent charged for an error | Server returns errors on the *free* path; never gates a 500 behind payment |
| Wallet key leak | Sealed via DPAPI/age; CI/repo scanning; key never in env files committed |
| Runaway storage | Archive is columnar + rotates; monitored; bounded by design |
| Attention drift (the real risk) | Fully automated post-deploy; no ongoing decisions; revisit only on alert |
