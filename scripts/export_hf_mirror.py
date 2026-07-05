"""Hugging Face dataset mirror — the lagged public slice of the archive.

Publishes archive days OLDER than LAG_DAYS (default 30) to a Hugging Face dataset repo.
The lag is the store-protection line: every paid tier sells the live edge (lookup/bulk reach
back ~9 days; by-date sells recent days) — a 30-day-old filing has no trading edge left, but
it's still good ML training data and an honest advertisement for the live feed. Mirroring old
data for free while selling fresh data is the thesis working, not a leak.

Idempotent: re-uploads are content-addressed by HF, so running weekly just adds newly-aged days.

Env:
  HF_TOKEN        - Hugging Face write token (required to upload; without it, stages only)
  FEEDFACE_HF_REPO- dataset repo id (default: the-junkyard/edgar-form4-insider-archive)
  FEEDFACE_DATA_DIR, FEEDFACE_HF_LAG_DAYS (default 30)
Exit: 0 = staged (and uploaded if token present); 1 = error.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "FEEDFACE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
LAG_DAYS = int(os.environ.get("FEEDFACE_HF_LAG_DAYS", "30"))
REPO_ID = os.environ.get("FEEDFACE_HF_REPO", "the-junkyard/edgar-form4-insider-archive")
STAGE = DATA_DIR / "hf_mirror"

CARD = """---
license: cc0-1.0
pretty_name: SEC Form 4 insider transactions, parsed (The Junkyard archive mirror)
tags:
  - finance
  - sec
  - edgar
  - insider-trading
  - public-domain
---

# SEC Form 4 insider transactions, parsed — archive mirror

Rolling mirror of [The Junkyard](https://botfeeder.junkyard.guru)'s SEC EDGAR Form 4 archive,
published {lag} days behind live. One file per EDGAR day. Records are parsed and classified:
transaction-code semantics, Rule 10b5-1 plan detection, footnote interpretation, indirect
ownership, amendments. Every record carries a `source_url` back to the primary SEC filing —
audit any row against the original.

**Why the lag:** the live edge is sold to trading agents per call over
[x402](https://botfeeder.junkyard.guru/llms.txt), priced below the buyer's own inference cost;
data past its trading edge is mirrored here for free. Parse once, never re-burn the compute:
see the running [compute-saved counter](https://botfeeder.junkyard.guru/v1/compute-saved).

**License:** underlying filings are U.S.-government public domain; our parsing/enrichment is
dedicated to the public domain under CC0. We sell data, not advice; nothing here is an
investment recommendation.

Live feed, samples, and prices: https://botfeeder.junkyard.guru · `GET /llms.txt`
"""


def main() -> int:
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=LAG_DAYS)).isoformat()
    archive = DATA_DIR / "archive"
    ready = sorted(p for p in archive.glob("*.*")
                   if p.suffix in (".jsonl", ".parquet") and p.stem <= cutoff)

    STAGE.mkdir(parents=True, exist_ok=True)
    (STAGE / "README.md").write_text(CARD.format(lag=LAG_DAYS), encoding="utf-8")
    data_dir = STAGE / "data"
    data_dir.mkdir(exist_ok=True)
    for p in ready:
        shutil.copy2(p, data_dir / p.name)
    print(f"[hf-mirror] staged {len(ready)} day-file(s) older than {cutoff} -> {STAGE}")

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("[hf-mirror] HF_TOKEN not set — staged only, no upload")
        return 0
    if not ready:
        print("[hf-mirror] nothing aged past the lag yet — no upload")
        return 0

    from huggingface_hub import HfApi  # lazy: only needed when actually uploading
    api = HfApi(token=token)
    api.create_repo(repo_id=REPO_ID, repo_type="dataset", exist_ok=True)
    api.upload_folder(repo_id=REPO_ID, repo_type="dataset", folder_path=str(STAGE),
                      commit_message=f"mirror: days through {cutoff}")
    print(f"[hf-mirror] uploaded to https://huggingface.co/datasets/{REPO_ID}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
