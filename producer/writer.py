"""Snapshot + archive writer. Spec: docs/02-architecture.md.

snapshot/latest.json — the hot serving view, written ATOMICALLY (temp file + os.replace)
so the server never reads a half-written file. On failure the old file is left intact
(last-good) — the server keeps serving rather than erroring on a paid call.

archive/<date>.jsonl — append-only, FULL-FIDELITY history (the resale source of truth for the
by-date and 10k-bulk tiers: docs/03). archive/<date>.parquet is written alongside it, best-effort,
as a flattened columnar copy for internal analytics ONLY — it drops footnotes/price_low/price_high/
amends, so it must never be read back for a paid response.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path


def write_snapshot(view: dict, data_dir: Path) -> None:
    """Atomically replace snapshot/latest.json with the current serving view."""
    snap_dir = data_dir / "snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    target = snap_dir / "latest.json"
    tmp = snap_dir / ".latest.json.tmp"
    tmp.write_text(json.dumps(view, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)  # atomic on the same filesystem


def load_snapshot(data_dir: Path) -> dict | None:
    target = data_dir / "snapshot" / "latest.json"
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def append_archive(records: list[dict], data_dir: Path, on: date) -> str:
    """Append full-fidelity records to archive/<date>.jsonl (the resale source of truth) and,
    best-effort, a flattened archive/<date>.parquet for internal analytics. Returns the jsonl path.
    """
    if not records:
        return ""
    arch = data_dir / "archive"
    arch.mkdir(parents=True, exist_ok=True)

    jsonl_path = arch / f"{on.isoformat()}.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq_path = arch / f"{on.isoformat()}.parquet"
        table = pa.Table.from_pylist([_flatten(r) for r in records])
        if pq_path.exists():
            existing = pq.read_table(pq_path)
            table = pa.concat_tables([existing, table], promote_options="default")
        pq.write_table(table, pq_path)
    except ModuleNotFoundError:
        pass  # analytics copy only — full-fidelity jsonl above already landed

    return str(jsonl_path)


def load_archive_day(data_dir: Path, on: date) -> list[dict]:
    """Full-fidelity records filed on one date, from archive/<date>.jsonl. [] if none/missing."""
    path = data_dir / "archive" / f"{on.isoformat()}.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # one corrupt line must not fail the whole read
    return out


def load_archive_recent(data_dir: Path, max_records: int) -> list[dict]:
    """Most-recent-first records across all archived days, up to max_records.

    Walks archive/*.jsonl newest-date-first; within a day, records are in the order they were
    appended (oldest-fetched first within that day), matching how the day itself was populated.
    """
    arch = data_dir / "archive"
    if not arch.is_dir():
        return []
    dates = sorted((p.stem for p in arch.glob("*.jsonl")), reverse=True)
    out: list[dict] = []
    for d in dates:
        if len(out) >= max_records:
            break
        try:
            on = date.fromisoformat(d)
        except ValueError:
            continue
        out.extend(load_archive_day(data_dir, on))
    return out[:max_records]


def _flatten(r: dict) -> dict:
    """Flatten nested issuer/insider/transaction into columnar-friendly scalar fields."""
    iss, ins, tx = r.get("issuer") or {}, r.get("insider") or {}, r.get("transaction") or {}
    return {
        "filing_id": r.get("filing_id"),
        "filed_at": r.get("filed_at"),
        "fetched_at": r.get("fetched_at"),
        "source_url": r.get("source_url"),
        "document_type": r.get("document_type"),
        "is_amendment": r.get("is_amendment"),
        "issuer_ticker": iss.get("ticker"),
        "issuer_name": iss.get("name"),
        "issuer_cik": iss.get("cik"),
        "insider_name": ins.get("name"),
        "insider_cik": ins.get("cik"),
        "insider_roles": ",".join(ins.get("roles", [])) if ins else None,
        "table": tx.get("table"),
        "code": tx.get("code"),
        "code_meaning": tx.get("code_meaning"),
        "discretionary": tx.get("discretionary"),
        "rule_10b5_1": tx.get("rule_10b5_1"),
        "shares": tx.get("shares"),
        "price": tx.get("price"),
        "acquired_disposed": tx.get("acquired_disposed"),
        "ownership": tx.get("ownership"),
        "shares_owned_after": tx.get("shares_owned_after"),
        "transaction_date": tx.get("transaction_date"),
    }
