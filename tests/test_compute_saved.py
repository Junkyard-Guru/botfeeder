"""Compute-saved counter: the instrument behind the thesis. Tests pin the honesty
properties — heartbeat self-purchases excluded, pre-migration rows undercount as zero,
net saving subtracts what buyers paid."""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import payments, volume_store
from server.app import app

client = TestClient(app)

HEARTBEAT = payments.HEARTBEAT_PAYER
BUYER = "0x1111111111111111111111111111111111111111"


def _db(tmp_path):
    return tmp_path / "volume.db"


def _log(db, monkeypatch, **kw):
    monkeypatch.setattr(volume_store, "DB_PATH", db)
    volume_store.record(**kw)


def test_counter_counts_buyer_records_and_excludes_heartbeat(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(volume_store, "DB_PATH", db)
    volume_store.record("lookup", 0.006, "eip155:8453", "settled", payer=BUYER, records=3)
    volume_store.record("bulk", 5.0, "eip155:8453", "settled", payer=BUYER, records=1000)
    # heartbeat self-purchase: settled, but must not move the public counter
    volume_store.record("lookup", 0.006, "eip155:8453", "settled",
                        payer=HEARTBEAT, records=500)
    # unpaid 402s never count
    volume_store.record("lookup", 0.006, "eip155:8453", "402", records=9)

    out = volume_store.compute_saved(0.01, exclude_payers=(HEARTBEAT,), db_path=db)
    assert out["records_served_to_buyers"] == 1003
    assert out["buyer_sales_counted"] == 2
    assert out["self_purchases_excluded"] == 1
    assert out["diy_cost_usd_avoided"] == round(1003 * 0.01, 6)
    assert out["paid_to_us_usd"] == round(0.006 + 5.0, 6)
    assert out["net_saved_by_buyers_usd"] == round(1003 * 0.01 - 5.006, 6)


def test_heartbeat_exclusion_is_case_insensitive(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(volume_store, "DB_PATH", db)
    volume_store.record("lookup", 0.006, "eip155:8453", "settled",
                        payer=HEARTBEAT.upper(), records=7)
    out = volume_store.compute_saved(0.01, exclude_payers=(HEARTBEAT,), db_path=db)
    assert out["records_served_to_buyers"] == 0
    assert out["self_purchases_excluded"] == 1


def test_pre_migration_rows_count_zero_records_not_error(tmp_path, monkeypatch):
    """A settled row with records=NULL (logged before the column existed) must
    undercount as 0, never crash or inflate."""
    db = _db(tmp_path)
    monkeypatch.setattr(volume_store, "DB_PATH", db)
    volume_store.record("lookup", 0.006, "eip155:8453", "settled", payer=BUYER)  # no records
    out = volume_store.compute_saved(0.01, exclude_payers=(HEARTBEAT,), db_path=db)
    assert out["records_served_to_buyers"] == 0
    assert out["buyer_sales_counted"] == 1
    assert out["paid_to_us_usd"] == 0.006
    assert out["net_saved_by_buyers_usd"] == 0.0  # clamped, never negative


def test_migration_adds_records_column_to_old_db(tmp_path):
    """Simulate a pre-2026-07-05 DB (no records column) and confirm the ALTER runs."""
    import sqlite3

    db = _db(tmp_path)
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "endpoint TEXT NOT NULL, price_usd REAL NOT NULL, network TEXT NOT NULL, "
        "outcome TEXT NOT NULL, payer TEXT, tx TEXT)")
    con.execute("INSERT INTO calls (ts, endpoint, price_usd, network, outcome, payer) "
                "VALUES ('2026-07-04T00:00:00Z', 'lookup', 0.006, 'eip155:8453', "
                "'settled', ?)", (BUYER,))
    con.commit()
    con.close()

    out = volume_store.compute_saved(0.01, db_path=db)
    assert out["records_served_to_buyers"] == 0
    assert out["buyer_sales_counted"] == 1


def test_endpoint_is_free_and_shaped(monkeypatch, tmp_path):
    monkeypatch.setattr(volume_store, "DB_PATH", _db(tmp_path))
    r = client.get("/v1/compute-saved")
    assert r.status_code == 200
    body = r.json()
    for key in ("claim", "methodology", "records_served_to_buyers",
                "diy_cost_usd_avoided", "net_saved_by_buyers_usd"):
        assert key in body
    # methodology names the exclusions out loud
    joined = " ".join(body["methodology"])
    assert HEARTBEAT in joined
    assert "undercounts" in joined or "never overcounts" in joined


def test_page_advertises_the_counter():
    from pathlib import Path
    page = (Path(__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "/v1/compute-saved" in page
    assert "application/ld+json" in page  # schema.org Dataset markup rides the same page
