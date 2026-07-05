"""MarineCadastre.gov AIS vessel tracking source module. Spec: the source-collection validation notes.

AIS (Automatic Identification System) is the VHF transponder data ships broadcast for
collision avoidance; NOAA/BOEM (National Oceanic and Atmospheric Administration / Bureau of
Ocean Energy Management) republish the U.S. Coast Guard's raw feed as CC0 (public-domain-
equivalent, no-rights-reserved) archive files.

Portal (live-checked 2026-07-03): https://marinecadastre.gov/ais/ 301-redirects to
https://hub.marinecadastre.gov/pages/vesseltraffic (an ArcGIS Hub page whose content is
client-rendered, so it doesn't yield download links via a plain HTML fetch). The actual file
archive lives at NOAA's own CDN and was found by following the well-known
coast.noaa.gov/htdata/CMSP/AISDataHandler/ path documented across NOAA's own AIS pages, then
confirmed live below.

Real path pattern, confirmed live:
    CURRENT (2024-present): daily national files, one CSV zipped per calendar day —
        https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/AIS_{year}_{mm}_{dd}.zip
        e.g. https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2024/AIS_2024_01_01.zip
        (curl 200, content-length 290340871 bytes for that one day — this is the CURRENT
        format, national in scope, filed once per day, not once per month/zone.)
    LEGACY (through ~2014-2020 vintages): monthly, per-UTM-zone files —
        https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/{mm}/Zone{n}_{year}_{mm}.zip
        e.g. https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2014/01/Zone20_2014_01.zip
        (curl 200, 469KB for that particular small zone/month.) Older-vintage zips are Esri
        File Geodatabase (.gdb) internally, NOT CSV — confirmed by downloading and unzipping
        a small (470KB) 2014 zone file live. Current-era zips ARE plain CSV (confirmed below).

This module targets the CURRENT daily-national format, since that's what's actually being
published going forward; the legacy monthly/zone layout is noted for completeness but not
polled (NOAA is not adding new files to it).

CSV schema inside the current zip (live-verified 2026-07-03 by partial-range HTTP download +
raw deflate decompression of just the first few MB of a real production file — no full
290MB download was needed to confirm the header): (real, exact column names, in order)
    MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign, VesselType,
    Status, Length, Width, Draft, Cargo, TransceiverClass

Scope of this build pass: raw AIS position records only — MMSI (Maritime Mobile Service
Identity, the vessel's transponder ID), timestamp, position, reported vessel name/type. There
is NO off-the-shelf MMSI -> company/ticker mapping in this pipeline yet (per the source-evaluation notes, none
exists as a free public dataset) — this ships as "vessel position records," NOT "ticker-tagged
trading signal." Any enrichment step is future work, not attempted here.

State: tracks the last fully-processed daily filename (e.g. "AIS_2024_01_01.zip") so fetch_new
finds the next undownloaded day. Cadence recommendation: daily-check-for-new-file (NOAA
publishes with a lag of roughly 1-2 months behind real time historically, so a daily poll is
cheap and safe — new files appear rarely from the poller's point of view, but checking is cheap).

Full-file note: a single day's zip is on the order of ~300MB uncompressing to a multi-million-
row CSV. This module's parser is correct against a real sample (see tests/fixtures/
marinecadastre_ais_sample.csv, extracted from a real live file), but a full day was not parsed
end-to-end in this pass — same judgment call as dol_h1b.py: correctness over exhaustive
first-run throughput.
"""
from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx

SOURCE_ID = "marinecadastre-ais"
LABEL = "MarineCadastre.gov AIS vessel tracking (CC0, NOAA/BOEM)"

USER_AGENT = "The Junkyard (botfeeder.junkyard.guru) - contact TBD"
BASE = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"

_FILENAME_RE = re.compile(r"AIS_(\d{4})_(\d{2})_(\d{2})\.zip")


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0)


def daily_file_url(d: date) -> str:
    return f"{BASE}/{d.year}/AIS_{d.year}_{d.month:02d}_{d.day:02d}.zip"


def parse_ais_zip(content: bytes, *, source_url: str, fetched_at: str, limit: int | None = None) -> list[dict]:
    """Parse one daily AIS zip (single CSV member) into normalized position records.

    `limit` caps rows parsed — a full day can be several million rows; the runner contract
    doesn't require exhaustively parsing an entire file in one pass (see module docstring).
    """
    records: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            return []
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            reader = csv.DictReader(text)
            for i, row in enumerate(reader):
                if limit is not None and i >= limit:
                    break
                records.append({
                    "mmsi": row.get("MMSI"),
                    "timestamp": row.get("BaseDateTime"),
                    "lat": _to_float(row.get("LAT")),
                    "lon": _to_float(row.get("LON")),
                    "sog_knots": _to_float(row.get("SOG")),
                    "cog_degrees": _to_float(row.get("COG")),
                    "heading_degrees": _to_float(row.get("Heading")),
                    "vessel_name": row.get("VesselName") or None,
                    "imo": row.get("IMO") or None,
                    "call_sign": row.get("CallSign") or None,
                    "vessel_type": row.get("VesselType") or None,
                    "status": row.get("Status") or None,
                    "length_m": _to_float(row.get("Length")),
                    "width_m": _to_float(row.get("Width")),
                    "draft_m": _to_float(row.get("Draft")),
                    "cargo": row.get("Cargo") or None,
                    "transceiver_class": row.get("TransceiverClass") or None,
                    "source_url": source_url,
                    "fetched_at": fetched_at,
                })
    return records


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


# Cap per fetch_new cycle — full days are multi-million rows; a poller shouldn't block for
# minutes on one cycle. Raise/remove once this is running as a dedicated batch job rather than
# the same poll loop as the REST sources.
ROWS_PER_CYCLE = 5000


def _find_latest_published_day(c: httpx.Client, today: date) -> date | None:
    """Exponential backward probe + binary search for the newest published daily file.

    Live-checked 2026-07-03: NOAA's real publishing lag turned out to be over a YEAR (2024
    files exist; 2025 files 404, as of a "today" in mid-2026) — far past the 45-day assumption
    this cold-start logic originally shipped with, which would have needed roughly 500 daily
    cron cycles just to walk the cursor from a wrong anchor point up to real data. A fixed-lag
    guess is fragile (the lag isn't contractually stable), so instead: double backward from a
    short lag until a HEAD succeeds, then binary-search forward to the exact newest good day.
    """
    step = 30
    hi = today  # known (assumed) not-yet-published upper bound
    lo = None
    for _ in range(12):  # 30, 60, 120, ... ~40000 days — plenty to find any realistic lag
        probe = today - timedelta(days=step)
        try:
            if c.head(daily_file_url(probe)).status_code == 200:
                lo = probe
                break
        except Exception:  # noqa: BLE001
            pass
        step *= 2
    if lo is None:
        return None  # nothing published within ~40000 days back — genuinely no data yet

    # lo is known-good, hi is known-bad (or unconfirmed) — binary search the boundary.
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) // 2
        try:
            ok = c.head(daily_file_url(mid)).status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            lo = mid
        else:
            hi = mid
    return lo


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """One poll cycle: check for the next day after state's last-processed date; if NOAA has
    published it (HEAD probe), download + parse (capped, see ROWS_PER_CYCLE) and advance state.
    NOAA publishes with a real-world lag, so most days this finds nothing new — cheap HEAD
    checks handle that case without downloading anything."""
    now = datetime.now(timezone.utc).isoformat()

    last_str = state.get("last_processed_date")
    if last_str:
        next_day = date.fromisoformat(last_str) + timedelta(days=1)
    else:
        # Cold start: find the actual newest published day via probe rather than assuming a
        # fixed lag (see _find_latest_published_day) — anchoring to a wrong guess could leave
        # the daily +1 cursor needing months/years of runs to reach real data.
        found = _find_latest_published_day(c, date.today())
        if found is None:
            return [], state  # nothing published within the probe window — try again next cycle
        next_day = found

    url = daily_file_url(next_day)
    try:
        head = c.head(url)
    except Exception as e:  # noqa: BLE001
        print(f"[producer:{SOURCE_ID}] HEAD failed for {url}: {e}", file=sys.stderr)
        return [], state

    if head.status_code != 200:
        return [], state  # not published yet (or never will be, e.g. future date) — try again next cycle

    try:
        dl = c.get(url)
        dl.raise_for_status()
        records = parse_ais_zip(dl.content, source_url=url, fetched_at=now, limit=ROWS_PER_CYCLE)
    except Exception as e:  # noqa: BLE001
        print(f"[producer:{SOURCE_ID}] download/parse failed for {url}: {e}", file=sys.stderr)
        return [], state

    state["last_processed_date"] = next_day.isoformat()
    return records, state
