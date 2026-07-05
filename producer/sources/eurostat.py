"""Eurostat access layer. Spec: the multi-source pipeline validation notes.

Confirmed live 2026-07-03 against
https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}?format=JSON&lang=EN
for datasets prc_hicp_manr (HICP inflation), une_rt_m (unemployment rate), and namq_10_gdp
(GDP growth). Eurostat content is public domain (EU institution copyright policy permits free
reuse with attribution).

The response is JSON-stat, NOT flat rows. Real shape (verified against live fixtures, not
assumed):

    {
      "size": [1, 1, 1, 3, 5],           # size of each dimension, same order as "id"
      "id": ["freq", "unit", "coicop", "geo", "time"],   # dimension order
      "value": {"0": 2.8, "4": 0.3, ...},   # SPARSE flat-index -> number map (string keys)
      "dimension": {
        "geo": {
          "category": {
            "index": {"DE": 0, "FR": 1, "IT": 2},   # code -> position within this dimension
            "label": {"DE": "Germany", "FR": "France", "IT": "Italy"}  # code -> human label
          }
        },
        "time": { "category": { "index": {...}, "label": {...} } },
        ...
      }
    }

A flat key in "value" encodes one N-dimensional cell. To decode it back to per-dimension
category codes we compute strides from "size" (row-major, same order as "id": the LAST
dimension varies fastest, i.e. stride[i] = product(size[i+1:])), then for each dimension
i: position_i = (flat_index // stride[i]) % size[i]. That position is looked up in
dimension[id[i]].category.index (inverted) to get the category code, then in .category.label
for the human name. Verified against the live namq_10_gdp fixture: size=[1,1,1,1,3,5] (geo
stride=5, time stride=1) -> flat index 4 decodes to geo_idx=0 (DE), time_idx=4 (2026-Q1),
matching the real value 0.3 for German GDP growth in that quarter.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

SOURCE_ID = "eurostat"
LABEL = "Eurostat (public domain, EU institution)"

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
_TIMEOUT = 30.0

# Curated datasets, verified live (source-collection validation pass). Each entry pins the non-geo/time dimensions to a
# single fixed category so the query returns one clean series per country -- picking values
# out of the API's own live category list for that dataset (verified via a plain query with
# the dimension omitted, where the API echoes back the valid codes for that combination).
#
#   prc_hicp_manr: EU inflation (HICP, annual rate of change, all-items). NOTE: EU institution
#     flags this dataset as discontinued in favor of prc_hicp_minr, but it is still live and
#     serving data as of 2026-07-03 -- kept because it was the endpoint explicitly confirmed
#     live for this task; a future maintainer should consider migrating to prc_hicp_minr.
#   une_rt_m: unemployment rate, monthly, seasonally adjusted, total population/sex.
#   namq_10_gdp: real GDP growth rate, quarterly, seasonally adjusted.
DATASETS: dict[str, dict] = {
    "prc_hicp_manr": {
        "label": "HICP inflation (annual rate of change)",
        "params": {"unit": "RCH_A", "coicop": "CP00"},
    },
    "une_rt_m": {
        "label": "Unemployment rate (% of labour force, seasonally adjusted)",
        "params": {"s_adj": "SA", "age": "TOTAL", "unit": "PC_ACT", "sex": "T"},
    },
    "namq_10_gdp": {
        "label": "Real GDP growth rate (quarterly, seasonally adjusted)",
        "params": {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ"},
    },
}

# EU member / reporting countries to track per dataset. Kept modest -- this is reference macro
# data, not a full 27-country pull, though these do resolve fine for all three datasets above.
GEOS = ["DE", "FR", "IT"]


def client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": "The Junkyard (botfeeder.junkyard.guru) - contact TBD"},
        timeout=_TIMEOUT,
    )


def _dataset_url(dataset_id: str, params: dict, geos: list[str]) -> str:
    q = "&".join(f"geo={g}" for g in geos)
    extra = "&".join(f"{k}={v}" for k, v in params.items())
    # lastTimePeriod caps the pull to recent periods -- these are low-frequency macro series,
    # we don't need decades of history on every poll.
    return f"{BASE}/{dataset_id}?format=JSON&lang=EN&{q}&{extra}&lastTimePeriod=6"


def _strides(size: list[int]) -> list[int]:
    """Row-major strides: stride[i] = product(size[i+1:]). Last dimension varies fastest."""
    n = len(size)
    strides = [1] * n
    for i in range(n - 2, -1, -1):
        strides[i] = strides[i + 1] * size[i + 1]
    return strides


def decode_jsonstat(doc: dict) -> list[dict]:
    """Pure function: one JSON-stat dataset response -> list of decoded per-cell dicts.

    Each dict has one key per dimension id (e.g. "geo", "time", "unit"...) holding
    {"code": ..., "label": ...}, plus "value" for the numeric observation. No I/O.
    """
    dim_ids: list[str] = doc.get("id") or []
    size: list[int] = doc.get("size") or []
    value: dict[str, float] = doc.get("value") or {}
    dimension: dict = doc.get("dimension") or {}

    if not dim_ids or not size or not value:
        return []

    strides = _strides(size)

    # Invert each dimension's index (code -> position) to (position -> code), once per dim.
    pos_to_code: dict[str, dict[int, str]] = {}
    labels: dict[str, dict[str, str]] = {}
    for dim_id in dim_ids:
        cat = (dimension.get(dim_id) or {}).get("category") or {}
        idx = cat.get("index") or {}
        pos_to_code[dim_id] = {pos: code for code, pos in idx.items()}
        labels[dim_id] = cat.get("label") or {}

    out: list[dict] = []
    for flat_str, val in value.items():
        if val is None:
            continue
        flat = int(flat_str)
        cell: dict = {"value": val}
        for i, dim_id in enumerate(dim_ids):
            position = (flat // strides[i]) % size[i]
            code = pos_to_code[dim_id].get(position)
            cell[dim_id] = {"code": code, "label": labels[dim_id].get(code, code)}
        out.append(cell)
    return out


def normalize_dataset(doc: dict, dataset_id: str, dataset_label: str, fetched_at: str) -> list[dict]:
    """Pure function: raw JSON-stat response -> normalized flat records. No I/O."""
    cells = decode_jsonstat(doc)
    out: list[dict] = []
    for cell in cells:
        geo = cell.get("geo") or {}
        time = cell.get("time") or {}
        unit = cell.get("unit") or {}
        # Any dimension besides geo/time/unit/freq/s_adj is an "indicator flag" worth surfacing
        # (e.g. coicop for HICP, na_item for GDP, age/sex for unemployment).
        extra_dims = {
            k: v.get("code")
            for k, v in cell.items()
            if k not in ("value", "geo", "time", "unit", "freq") and isinstance(v, dict)
        }
        out.append({
            "dataset_id": dataset_id,
            "dataset_label": dataset_label,
            "country_code": geo.get("code"),
            "country_name": geo.get("label"),
            "time_period": time.get("code"),
            "unit": unit.get("code"),
            "value": cell["value"],
            "indicator_flags": extra_dims or None,
            "fetched_at": fetched_at,
        })
    return out


def fetch_new(state: dict, c: httpx.Client) -> tuple[list[dict], dict]:
    """Fetch each curated dataset, emit only time-period data points newer than state.

    State: {"prc_hicp_manr": "2025-12", ...} -- last time_period string already emitted per
    dataset. Time periods sort correctly as strings for both monthly ("2026-01") and quarterly
    ("2026-Q1") formats sharing a dataset (each dataset uses one cadence consistently).
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    new_state = dict(state)
    new_records: list[dict] = []

    for dataset_id, meta in DATASETS.items():
        url = _dataset_url(dataset_id, meta["params"], GEOS)
        r = c.get(url)
        r.raise_for_status()
        doc = r.json()
        if doc.get("error"):
            # A dataset/param combo can go stale (Eurostat retires/renames series); don't crash
            # the whole run over one bad dataset.
            continue

        records = normalize_dataset(doc, dataset_id, meta["label"], fetched_at)
        if not records:
            continue

        last_period = state.get(dataset_id)
        fresh = [r for r in records if last_period is None or r["time_period"] > last_period]
        if not fresh:
            continue

        new_records.extend(fresh)
        newest = max(r["time_period"] for r in records)
        new_state[dataset_id] = newest

    return new_records, new_state
