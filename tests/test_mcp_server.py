"""MCP layer: the /mcp mount must speak Model Context Protocol (streamable HTTP,
stateless JSON mode) and expose exactly the free-surface tools — never paid data.

One module-scoped client: the SDK's StreamableHTTPSessionManager runs once per process
(matching production, where the lifespan starts once), so all tests share a lifespan.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from server.app import app

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _rpc(client, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    return client.post("/mcp/", json=body, headers=HEADERS)


def _initialize(client):
    return _rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # context manager runs the lifespan/session manager
        yield c


def test_mcp_initialize(client):
    r = _initialize(client)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "the-junkyard"


def test_mcp_lists_free_tools_only(client):
    _initialize(client)
    r = _rpc(client, "tools/list", id_=2)
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {
        "junkyard_overview", "junkyard_meta", "junkyard_insider_sample",
        "junkyard_signals_sample", "junkyard_compute_saved", "junkyard_payment_quote",
    }


def test_mcp_payment_quote_tool_returns_prices(client):
    _initialize(client)
    r = _rpc(client, "tools/call",
             {"name": "junkyard_payment_quote", "arguments": {}}, id_=3)
    assert r.status_code == 200, r.text
    content = r.json()["result"]["content"]
    text = " ".join(c.get("text", "") for c in content)
    payload = json.loads(text)
    assert payload["network"]
    assert any(v == 0.006 for v in payload["endpoints_usd"].values())
    assert "402" in " ".join(payload["how_to_buy"])


def test_mcp_meta_tool_matches_http_meta(client):
    _initialize(client)
    r = _rpc(client, "tools/call", {"name": "junkyard_meta", "arguments": {}}, id_=4)
    assert r.status_code == 200, r.text
    text = " ".join(c.get("text", "") for c in r.json()["result"]["content"])
    via_mcp = json.loads(text)
    via_http = client.get("/v1/meta").json()
    assert via_mcp["service"] == via_http["service"] == "the-junkyard"
    assert via_mcp["diy_comparison"]["diy_cost_usd_per_filing"] == \
        via_http["diy_comparison"]["diy_cost_usd_per_filing"]
