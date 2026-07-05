"""Producer entry point — the poll loop. Run by a systemd timer (deploy/).

Each tick: recent filings -> resolve XML -> parse -> merge into the rolling snapshot +
append to the archive. On ANY per-filing failure: skip it, keep going. On a total
failure: the previous snapshot stays in place (last-good) and the server keeps serving.
An error here must NEVER reach the paid path.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from producer import edgar
from producer.parser import parse_form4
from producer.runner import run_source
from producer.writer import append_archive, load_snapshot, write_snapshot
from producer.sources import (
    bis_entity_list,
    cftc_cot,
    fdic_financials,
    form_13f,
    form_8k,
    fred,
    house_ptr,
    openfda_drugsfda,
    treasury_auctions,
    usaspending,
)

POLL_LIMIT = int(os.environ.get("FEEDFACE_POLL_LIMIT", "40"))
SNAPSHOT_CAP = int(os.environ.get("FEEDFACE_SNAPSHOT_CAP", "1000"))
SEEN_CAP = 8000

# docs/13-signal-mapping.md — only sources with a real data->signal mapping run. Each is fully
# isolated by producer.runner.run_source: a failure in one never blocks another or the Form 4
# path above. fred/bis_entity_list are keyless no-ops until their env keys are set (see deploy/feedface.env.example).
#
# SHELVED (built, tested, NOT polled — no signal mapping earns them a slot; see docs/13):
# form_d, worldbank, eurostat, census_trade, dol_h1b, fcc_uls_auctions, marinecadastre_ais.
REGISTRY = [
    form_8k, form_13f,
    cftc_cot, treasury_auctions, usaspending, openfda_drugsfda,
    fdic_financials, fred, bis_entity_list,
    house_ptr,
]


def _data_dir() -> Path:
    return Path(os.environ.get(
        "FEEDFACE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _load_seen(data_dir: Path) -> list[str]:
    f = data_dir / "seen.json"
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_seen(seen: list[str], data_dir: Path) -> None:
    (data_dir / "seen.json").write_text(json.dumps(seen[-SEEN_CAP:]), encoding="utf-8")


def run_once(limit: int = POLL_LIMIT, data_dir: Path | None = None) -> dict:
    data_dir = data_dir or _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(data_dir)
    seen_set = set(seen)
    now = datetime.now(timezone.utc).isoformat()

    new_records: list[dict] = []
    processed = errors = 0
    c = edgar.client()
    try:
        filings = edgar.recent_form4_filings(limit, c)
        for f in filings:
            if f["accession"] in seen_set:
                continue
            try:
                xurl = edgar.primary_form4_xml_url(f["cik"], f["acc_nodash"], c)
                if not xurl:
                    continue
                recs = parse_form4(
                    edgar.fetch(xurl, c),
                    source_url=xurl,
                    filing_id=f["accession"],
                    filed_at=f.get("filed_at"),
                    fetched_at=now,
                )
                new_records.extend(recs)
                seen.append(f["accession"])
                seen_set.add(f["accession"])
                processed += 1
            except Exception as e:  # one bad filing must not stop the run
                errors += 1
                print(f"[producer] skip {f['accession']}: {e}", file=sys.stderr)
    finally:
        c.close()

    if new_records:
        prev = load_snapshot(data_dir) or {}
        merged = (new_records + prev.get("records", []))[:SNAPSHOT_CAP]
        view = {
            "generated_at": now,
            "source": "SEC EDGAR (U.S. government public domain)",
            "product": "edgar-form4-insider",
            "count": len(merged),
            "records": merged,
        }
        write_snapshot(view, data_dir)
        archived = append_archive(new_records, data_dir, datetime.now(timezone.utc).date())
        _save_seen(seen, data_dir)
    else:
        archived = ""

    # Watch product: deliver per-subscriber matches. Fully isolated — a failure here must never
    # touch the snapshot path above or the paid pull endpoints.
    watch_summary: dict = {}
    try:
        from producer.watch_loop import run_watch_cycle
        watch_summary = run_watch_cycle(now=now)
    except Exception as e:  # noqa: BLE001
        print(f"[producer] watch cycle failed: {e}", file=sys.stderr)

    # New ingredient sources (source-collection validation pass): each isolated by run_source — one bad source never
    # blocks another or the Form 4 path above.
    sources_summary = [run_source(mod, data_dir / "sources", now) for mod in REGISTRY]

    result = {
        "filings_seen": len(filings),
        "processed": processed,
        "errors": errors,
        "new_records": len(new_records),
        "archived_to": archived,
        "watch": watch_summary,
        "sources": sources_summary,
    }
    print(f"[producer] {result}", file=sys.stderr)
    return result


if __name__ == "__main__":
    run_once()
