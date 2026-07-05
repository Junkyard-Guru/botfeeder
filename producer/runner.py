"""Generic producer runner for the multi-source ingredient pipelines (source-collection validation pass).

Every non-Form4 source module (producer/sources/*.py) implements the same tiny contract:

    SOURCE_ID: str          # slug -> data/<SOURCE_ID>/ subtree + snapshot "product" field
    LABEL: str               # human-readable "source" field in the served snapshot
    def client() -> httpx.Client
    def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
        # Returns (new normalized records, updated state). `state` is whatever cursor/seen-set
        # the source needs to avoid re-fetching (last report date, last accession, etc) — plain
        # JSON, persisted to data/<SOURCE_ID>/state.json between runs.

run_source() below reuses the exact same snapshot+archive contract as producer/main.py's Form 4
loop (writer.write_snapshot / append_archive) — one failing source must never affect another, so
each gets its own data_dir and its own try/except.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from producer.signals import attach_signals
from producer.writer import append_archive, load_snapshot, write_snapshot

SNAPSHOT_CAP_DEFAULT = 1000


def _state_path(data_dir: Path) -> Path:
    return data_dir / "state.json"


def load_state(data_dir: Path) -> dict:
    f = _state_path(data_dir)
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict, data_dir: Path) -> None:
    _state_path(data_dir).write_text(json.dumps(state), encoding="utf-8")


def run_source(mod, base_data_dir: Path, now: str, snapshot_cap: int = SNAPSHOT_CAP_DEFAULT) -> dict:
    """Run one fetch/normalize/persist cycle for a source module. Never raises — a failure here
    must never take down another source's run or the paid serving path."""
    data_dir = base_data_dir / mod.SOURCE_ID
    data_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(data_dir)

    try:
        with mod.client() as c:
            new_records, state = mod.fetch_new(state, c)
    except Exception as e:  # noqa: BLE001 — one bad source must not stop the others
        print(f"[producer:{mod.SOURCE_ID}] fetch failed: {e}", file=sys.stderr)
        return {"source": mod.SOURCE_ID, "new_records": 0, "error": str(e)}

    if new_records:
        # Signal enrichment (docs/13) — best-effort, per-record isolated inside attach_signals;
        # a mapper bug costs a record its signal envelope, never the record itself.
        attach_signals(mod.SOURCE_ID, new_records)
        prev = load_snapshot(data_dir) or {}
        merged = (new_records + prev.get("records", []))[:snapshot_cap]
        view = {
            "generated_at": now,
            "source": mod.LABEL,
            "product": mod.SOURCE_ID,
            "count": len(merged),
            "records": merged,
        }
        write_snapshot(view, data_dir)
        append_archive(new_records, data_dir, date.today())
        save_state(state, data_dir)

    return {"source": mod.SOURCE_ID, "new_records": len(new_records)}
