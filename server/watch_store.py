"""Watch subscription persistence (SQLite). Spec: docs/09.

First stateful piece of the system — the pull product is stateless. Kept deliberately small: just
what the subscribe route and the watch loop need. One file = one DB; the connection is opened per call
(SQLite is fine for this low write volume and avoids threading hazards under uvicorn).

Tables
  subscriptions(token PK, created_at, paid_through, webhook_url, status)
  watch_entities(token, cik, label)                       -- the resolved watchlist
  matches(token, cik, filing_id, payload, matched_at, webhook_status, polled_at)
                                                          -- dedup + poll queue; (token,filing_id) unique
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("FEEDFACE_WATCH_DB",
                              Path(__file__).resolve().parent.parent / "data" / "watch.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
  token TEXT PRIMARY KEY, created_at TEXT NOT NULL, paid_through TEXT NOT NULL,
  webhook_url TEXT, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS watch_entities (
  token TEXT NOT NULL, cik TEXT NOT NULL, label TEXT,
  UNIQUE(token, cik));
CREATE TABLE IF NOT EXISTS matches (
  token TEXT NOT NULL, cik TEXT, filing_id TEXT NOT NULL, payload TEXT NOT NULL,
  matched_at TEXT NOT NULL, webhook_status TEXT, polled_at TEXT,
  UNIQUE(token, filing_id));
CREATE INDEX IF NOT EXISTS ix_watch_cik ON watch_entities(cik);
CREATE INDEX IF NOT EXISTS ix_match_poll ON matches(token, polled_at);
"""


@contextmanager
def _conn(db_path: Path | None = None):
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_subscription(entities: list[dict], paid_through: str, *, webhook_url: str | None = None,
                        created_at: str | None = None, db_path: Path | None = None) -> str:
    """entities: [{"cik": "...", "label": "..."}]. Returns the subscription token."""
    token = secrets.token_hex(16)
    with _conn(db_path) as con:
        con.execute("INSERT INTO subscriptions(token, created_at, paid_through, webhook_url) "
                    "VALUES(?,?,?,?)", (token, created_at or _now_iso(), paid_through, webhook_url))
        con.executemany("INSERT OR IGNORE INTO watch_entities(token, cik, label) VALUES(?,?,?)",
                        [(token, e["cik"], e.get("label")) for e in entities])
    return token


def get_subscription(token: str, db_path: Path | None = None) -> dict | None:
    with _conn(db_path) as con:
        row = con.execute("SELECT * FROM subscriptions WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        ents = con.execute("SELECT cik, label FROM watch_entities WHERE token=?", (token,)).fetchall()
    return {**dict(row), "entities": [dict(e) for e in ents]}


def active_subscriptions(now: str | None = None, db_path: Path | None = None) -> list[dict]:
    """Subscriptions whose paid_through is still in the future and status active."""
    cutoff = now or _now_iso()
    with _conn(db_path) as con:
        rows = con.execute("SELECT token FROM subscriptions WHERE status='active' AND paid_through > ?",
                           (cutoff,)).fetchall()
    return [get_subscription(r["token"], db_path) for r in rows]


def extend(token: str, new_paid_through: str, db_path: Path | None = None) -> bool:
    with _conn(db_path) as con:
        cur = con.execute("UPDATE subscriptions SET paid_through=?, status='active' WHERE token=?",
                          (new_paid_through, token))
    return cur.rowcount > 0


def has_match(token: str, filing_id: str, db_path: Path | None = None) -> bool:
    with _conn(db_path) as con:
        row = con.execute("SELECT 1 FROM matches WHERE token=? AND filing_id=?",
                          (token, filing_id)).fetchone()
    return row is not None


def add_match(token: str, filing_id: str, payload: dict, *, cik: str | None = None,
              matched_at: str | None = None, db_path: Path | None = None) -> bool:
    """Record a matched filing for a sub. Idempotent on (token, filing_id) — returns True if newly
    added (so the caller pushes the webhook exactly once), False if it was already matched (dedup)."""
    with _conn(db_path) as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO matches(token, cik, filing_id, payload, matched_at) "
            "VALUES(?,?,?,?,?)",
            (token, cik, filing_id, json.dumps(payload), matched_at or _now_iso()))
    return cur.rowcount > 0


def unpolled(token: str, db_path: Path | None = None) -> list[dict]:
    """Matches not yet handed out via the poll endpoint (oldest first)."""
    with _conn(db_path) as con:
        rows = con.execute("SELECT filing_id, payload FROM matches WHERE token=? AND polled_at IS NULL "
                           "ORDER BY matched_at", (token,)).fetchall()
    return [{"filing_id": r["filing_id"], **json.loads(r["payload"])} for r in rows]


def mark_polled(token: str, filing_ids: list[str], at: str | None = None,
                db_path: Path | None = None) -> None:
    if not filing_ids:
        return
    stamp = at or _now_iso()
    with _conn(db_path) as con:
        con.executemany("UPDATE matches SET polled_at=? WHERE token=? AND filing_id=? AND polled_at IS NULL",
                        [(stamp, token, fid) for fid in filing_ids])


def set_webhook_status(token: str, filing_id: str, status: str, db_path: Path | None = None) -> None:
    with _conn(db_path) as con:
        con.execute("UPDATE matches SET webhook_status=? WHERE token=? AND filing_id=?",
                    (status, token, filing_id))
