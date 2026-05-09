"""Pure-function side of outbound-proxy plumbing: hit the provider API,
parse one ``host:port`` line, return a usable ``http://host:port`` URL.

Per-phone caching + expiry lives in ``app.devices.acquire_proxy_for_phone``
because that's where ``phone_devices`` ownership lives. This module
intentionally has no state.
"""
from __future__ import annotations

import httpx


async def fetch_proxy_url(api_url: str) -> str:
    """Call the provider API, return a single ``http://host:port`` URL.

    The provider API used here returns plain text, one IP:port per line.
    Caller is responsible for caching/persisting; this is a one-shot fetch
    and is not routed through any proxy itself (would be circular).
    """
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(api_url)
    r.raise_for_status()
    text = r.text.strip()
    if not text:
        raise RuntimeError("proxy provider returned empty body")
    first_line = text.splitlines()[0].strip()
    if ":" not in first_line:
        raise RuntimeError(f"proxy provider returned unexpected payload: {text!r}")
    return f"http://{first_line}"
