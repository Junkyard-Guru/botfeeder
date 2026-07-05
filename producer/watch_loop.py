"""The watch loop: per active subscription, per watched CIK, find new Form 4s and deliver them.

Runs inside the producer cycle (every ~5 min). Queries EDGAR directly per watched CIK (not the
40-cap firehose) for completeness, parses with the same moat parser, records the match (dedup), and
pushes the webhook once. The free poll endpoint serves whatever a sub hasn't picked up yet.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from producer import edgar
from producer.parser import parse_form4
from server import watch_delivery as delivery
from server import watch_store as store

WEBHOOK_SECRET = os.environ.get("FEEDFACE_WEBHOOK_SECRET", "")
PER_CIK_LIMIT = int(os.environ.get("FEEDFACE_WATCH_PER_CIK", "10"))


def _on_or_after(filed_at: str | None, created: str | None) -> bool:
    """Don't backfill history on day one: only deliver filings at/after the sub started."""
    if not filed_at or not created:
        return True
    try:
        return datetime.fromisoformat(filed_at) >= datetime.fromisoformat(created)
    except Exception:
        return True  # uncomparable -> don't suppress


def run_watch_cycle(now: str | None = None, deliver_webhook: bool = True) -> dict:
    now = now or datetime.now(timezone.utc).isoformat()
    subs = store.active_subscriptions(now=now)
    summ = {"subscriptions": len(subs), "new_matches": 0, "webhooks_ok": 0, "webhooks_fail": 0}
    for sub in subs:
        token, created, hook = sub["token"], sub["created_at"], sub.get("webhook_url")
        for ent in sub["entities"]:
            cik = ent["cik"]
            try:
                filings = edgar.recent_form4_for_cik(cik, limit=PER_CIK_LIMIT)
            except Exception:
                continue  # one bad CIK never sinks the cycle
            for f in filings:
                fid = f["accession"]
                if not _on_or_after(f.get("filed_at"), created) or store.has_match(token, fid):
                    continue
                try:
                    xml_url = edgar.primary_form4_xml_url(f.get("cik") or cik, f["acc_nodash"])
                    if not xml_url:
                        continue
                    records = parse_form4(edgar.fetch(xml_url), source_url=xml_url, filing_id=fid,
                                          filed_at=f.get("filed_at"), fetched_at=now)
                except Exception:
                    continue
                payload = {"matched_cik": cik, "label": ent.get("label"), "filing_id": fid,
                           "filed_at": f.get("filed_at"), "source_url": xml_url,
                           "count": len(records), "records": records}
                if not store.add_match(token, fid, payload, cik=cik, matched_at=now):
                    continue
                summ["new_matches"] += 1
                if deliver_webhook and hook:
                    ok, status = delivery.post_webhook(hook, payload, WEBHOOK_SECRET, timestamp=now)
                    store.set_webhook_status(token, fid, "ok" if ok else f"fail:{status}")
                    summ["webhooks_ok" if ok else "webhooks_fail"] += 1
    return summ
