"""Maker's-mark tests: the glyph + decoded principles must appear, from one source of truth,
on every surface we promised (page footer, /v1/meta, /llms.txt, outbound webhooks)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server import ethos, watch_delivery
from server.app import app

client = TestClient(app)
PAGE = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")


def test_glyph_and_decode_are_consistent():
    # One decoded principle per line of the seal — no drift between sigil and meaning.
    assert len(ethos.PRINCIPLES) == len(ethos.ETHOS_GLYPH.splitlines()) == 7
    assert ethos.ETHOS_GLYPH_INLINE in ethos.ETHOS_GLYPH


def test_meta_carries_glyph_and_machine_actionable_principles():
    m = client.get("/v1/meta").json()
    assert m["ethos_glyph"] == ethos.ETHOS_GLYPH
    assert m["principles"] == ethos.PRINCIPLES  # decoded, so an agent can weigh them
    assert all("expr" in p and "principle" in p for p in m["principles"])


def test_llms_txt_has_makers_mark_section():
    txt = client.get("/llms.txt").text
    assert "Maker's mark" in txt
    assert ethos.ETHOS_GLYPH_INLINE in txt
    assert ethos.PRINCIPLES[0]["principle"] in txt  # decode is present, not just the sigil


def test_page_footer_carries_the_seal():
    # The page HTML-escapes '>' to '&gt;', so match the distinctive glyph prefix (before the '>').
    assert "∂Vᵢ/∂Vⱼ" in PAGE
    assert "maker's mark" in PAGE.lower()


def test_outbound_webhook_gets_the_mark():
    captured = {}
    def handler(req):
        captured["body"] = req.content
        return httpx.Response(200)
    tc = httpx.Client(transport=httpx.MockTransport(handler))
    ok, _ = watch_delivery.post_webhook("https://example.com/h", {"filing_id": "x"}, "sec", _client=tc)
    assert ok
    assert json.loads(captured["body"])["_mark"] == ethos.ETHOS_GLYPH_INLINE
