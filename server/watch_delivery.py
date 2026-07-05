"""Webhook delivery for the watch product. Spec: docs/09.

Buyer-supplied callback URLs are hostile input, so two guards are mandatory:
  - SSRF: only public http(s) hosts; reject loopback/private/link-local/reserved, no redirects.
  - Authenticity: sign the body (HMAC-SHA256) so the bot can verify the push really came from us.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx


def safe_webhook_url(url: str) -> bool:
    """True only if url is a public http(s) endpoint (SSRF guard)."""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except Exception:
        return False
    for *_unused, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(url: str, payload: dict, secret: str, *, timestamp: str | None = None,
                 attempts: int = 3, _client: httpx.Client | None = None) -> tuple[bool, str]:
    """POST a signed match payload, with bounded retries. Returns (ok, status_detail)."""
    if not safe_webhook_url(url):
        return (False, "blocked_url")
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    # Maker's mark footer on every outbound push (server/ethos.py). Added here at the single
    # delivery choke point so it signs with the body and never has to be threaded through callers.
    from server import ethos
    payload = {**payload, "_mark": ethos.ETHOS_GLYPH_INLINE}
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "X-Junkyard-Timestamp": ts,
               "X-Junkyard-Signature": sign(body, secret) if secret else ""}
    own = _client is None
    c = _client or httpx.Client(timeout=10.0, follow_redirects=False)
    try:
        last = "no_attempt"
        for i in range(attempts):
            try:
                r = c.post(url, content=body, headers=headers)
                if 200 <= r.status_code < 300:
                    return (True, str(r.status_code))
                last = f"http_{r.status_code}"
            except Exception as e:
                last = f"err_{type(e).__name__}"
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))  # brief linear backoff
        return (False, last)
    finally:
        if own:
            c.close()
