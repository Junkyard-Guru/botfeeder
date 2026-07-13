"""Call/settlement volume log (SQLite). Companion to watch_store.py's pattern.

Records every ensure_paid() outcome — both completed sales and 402s issued for a call that had
real data ready — so /v1/meta and a report script can answer "how much traffic, how much of it
paid, how much revenue" without depending on chain-scraping or a hosted facilitator dashboard.

Deliberately does NOT log free-tier calls (/v1/meta, /v1/insider/sample) — those never reach
ensure_paid(). This table is demand-that-had-a-price-attached, not raw HTTP traffic.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("FEEDFACE_VOLUME_DB",
                              Path(__file__).resolve().parent.parent / "data" / "volume.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  price_usd REAL NOT NULL,
  network TEXT NOT NULL,
  outcome TEXT NOT NULL,          -- 'settled' | '402'
  payer TEXT,                     -- buyer address, settled only
  tx TEXT,                        -- settlement tx hash, settled only
  records INTEGER                 -- record count in the payload the call was charged for
);
CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS ix_calls_endpoint ON calls(endpoint);
CREATE INDEX IF NOT EXISTS ix_calls_outcome ON calls(outcome);
"""


@contextmanager
def _conn(db_path: Path | None = None):
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        try:  # migrate pre-2026-07-05 DBs created before the records column existed
            con.execute("ALTER TABLE calls ADD COLUMN records INTEGER")
        except sqlite3.OperationalError:
            pass  # column already present
        yield con
        con.commit()
    finally:
        con.close()


def record(endpoint: str, price_usd: float, network: str, outcome: str,
           payer: str | None = None, tx: str | None = None,
           records: int | None = None) -> None:
    """Best-effort logging — a logging failure must never block a real request."""
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO calls (ts, endpoint, price_usd, network, outcome, payer, tx, records) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), endpoint, price_usd, network,
                 outcome, payer, tx, records),
            )
    except Exception as exc:  # pragma: no cover - logging must never break a paid response
        import sys
        print(f"[volume_store] log failed (non-fatal): {exc}", file=sys.stderr, flush=True)


def compute_saved(diy_cost_per_record_usd: float,
                  exclude_payers: tuple[str, ...] = (),
                  db_path: Path | None = None) -> dict:
    """The compute-saved counter: cumulative inference cost real buyers avoided.

    records_served x DIY cost per record, minus what those buyers actually paid us. Counts
    BOTH settled sales and 'free' deliveries — under the standing free-data policy (2026-07-13)
    data is served free, so a free delivery avoids 100% of the buyer's DIY inference cost
    (price_usd=0 -> net saving = full DIY cost). exclude_payers (our own heartbeat wallet)
    keeps self-purchases OUT of the public number — the counter measures value delivered to
    strangers, not traffic we generate at ourselves to stay listed. Rows logged before the
    records column existed (pre-2026-07-05) count as 0 records: a conservative undercount,
    never an overcount.
    """
    excluded = {p.lower() for p in exclude_payers if p}
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT ts, payer, price_usd, records FROM calls "
            "WHERE outcome IN ('settled', 'free')"
        ).fetchall()
    counted = [r for r in rows if (r["payer"] or "").lower() not in excluded]
    records_served = sum(r["records"] or 0 for r in counted)
    paid_usd = sum(r["price_usd"] for r in counted)
    diy_usd = records_served * diy_cost_per_record_usd
    return {
        "records_served_to_buyers": records_served,
        "buyer_sales_counted": len(counted),
        "self_purchases_excluded": len(rows) - len(counted),
        "diy_cost_usd_per_record": diy_cost_per_record_usd,
        "diy_cost_usd_avoided": round(diy_usd, 6),
        "paid_to_us_usd": round(paid_usd, 6),
        "net_saved_by_buyers_usd": round(max(diy_usd - paid_usd, 0.0), 6),
        "counting_since": min((r["ts"] for r in counted), default=None),
    }


def settled_purchases(exclude_payers: tuple[str, ...] = (),
                      db_path: Path | None = None) -> int:
    """Cumulative settled purchase EVENTS, excluding our own heartbeat. Drives the
    published price-descent cadence (payments.PRICING_CADENCE) — transactions, not
    wallet identities, since agentic buyers rotate wallets freely and more transactions
    is exactly what the store wants to reward."""
    excluded = {p.lower() for p in exclude_payers if p}
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT payer FROM calls WHERE outcome='settled'"
        ).fetchall()
    return sum(1 for r in rows if (r["payer"] or "").lower() not in excluded)


def summary(db_path: Path | None = None) -> dict:
    """Aggregate report: totals, breakdown by endpoint, breakdown by outcome."""
    with _conn(db_path) as con:
        totals = con.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN outcome='settled' THEN 1 ELSE 0 END) AS settled_n, "
            "SUM(CASE WHEN outcome='settled' THEN price_usd ELSE 0 END) AS revenue_usd, "
            "SUM(CASE WHEN outcome='402' THEN 1 ELSE 0 END) AS unpaid_402_n "
            "FROM calls"
        ).fetchone()
        by_endpoint = con.execute(
            "SELECT endpoint, "
            "SUM(CASE WHEN outcome='settled' THEN 1 ELSE 0 END) AS settled_n, "
            "SUM(CASE WHEN outcome='settled' THEN price_usd ELSE 0 END) AS revenue_usd, "
            "SUM(CASE WHEN outcome='402' THEN 1 ELSE 0 END) AS unpaid_402_n "
            "FROM calls GROUP BY endpoint ORDER BY revenue_usd DESC"
        ).fetchall()
        by_network = con.execute(
            "SELECT network, outcome, COUNT(*) AS n FROM calls GROUP BY network, outcome"
        ).fetchall()
        recent_sales = con.execute(
            "SELECT ts, endpoint, price_usd, payer, tx FROM calls "
            "WHERE outcome='settled' ORDER BY ts DESC LIMIT 20"
        ).fetchall()
        first_ts = con.execute("SELECT MIN(ts) AS t FROM calls").fetchone()["t"]
        last_ts = con.execute("SELECT MAX(ts) AS t FROM calls").fetchone()["t"]

    n = totals["n"] or 0
    settled_n = totals["settled_n"] or 0
    return {
        "window": {"first_call": first_ts, "last_call": last_ts},
        "total_calls": n,
        "settled_calls": settled_n,
        "unpaid_402_calls": totals["unpaid_402_n"] or 0,
        "conversion_rate": round(settled_n / n, 4) if n else 0.0,
        "revenue_usd": round(totals["revenue_usd"] or 0.0, 6),
        "by_endpoint": [dict(r) for r in by_endpoint],
        "by_network_outcome": [dict(r) for r in by_network],
        "recent_sales": [dict(r) for r in recent_sales],
    }
