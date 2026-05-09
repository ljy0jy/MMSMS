"""Outbound HTTP proxy: fetch a rotating IP from a third-party API and
re-route every upstream call through it.

Why a wrapper class instead of a plain ``httpx.AsyncClient(proxy=...)``:
the proxy IP rotates every ~10 minutes (the provider's ``time`` parameter),
so the underlying client has to be rebuilt periodically. ``ProxiedClient``
keeps the same .post / .get surface as ``AsyncClient`` while transparently
swapping the inner client when the cached IP expires.

If the env var ``UPSTREAM_PROXY_API`` is empty, ``main.py`` falls back to
a plain unproxied client.
"""
from __future__ import annotations

import asyncio
import time

import httpx


class ProxyProvider:
    """Calls the proxy provider API and caches the resulting ``host:port`` for
    ``ttl_seconds``. Concurrency-safe: at most one fetch in flight at a time.
    """

    def __init__(self, api_url: str, ttl_seconds: int = 480):
        self._api_url = api_url
        self._ttl = ttl_seconds
        self._cached_url: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        now = time.monotonic()
        if self._cached_url is not None and now < self._expires_at:
            return self._cached_url
        async with self._lock:
            now = time.monotonic()
            if self._cached_url is not None and now < self._expires_at:
                return self._cached_url
            # Direct connection here on purpose: we are fetching the proxy
            # itself, so going through a proxy would be circular.
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(self._api_url)
            r.raise_for_status()
            first_line = r.text.strip().splitlines()[0].strip() if r.text.strip() else ""
            if not first_line or ":" not in first_line:
                raise RuntimeError(
                    f"proxy provider returned unexpected payload: {r.text!r}"
                )
            self._cached_url = f"http://{first_line}"
            self._expires_at = now + self._ttl
            return self._cached_url


class ProxiedClient:
    """``httpx.AsyncClient`` facade that rebuilds its inner client whenever the
    upstream proxy IP changes. Exposes only the surface our code uses
    (``post``, ``get``, ``aclose``)."""

    def __init__(self, provider: ProxyProvider, **client_kwargs):
        self._provider = provider
        self._client_kwargs = client_kwargs
        self._client: httpx.AsyncClient | None = None
        self._client_proxy: str | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> httpx.AsyncClient:
        proxy = await self._provider.get()
        if self._client is not None and self._client_proxy == proxy:
            return self._client
        async with self._lock:
            if self._client is not None and self._client_proxy == proxy:
                return self._client
            old = self._client
            self._client = httpx.AsyncClient(proxy=proxy, **self._client_kwargs)
            self._client_proxy = proxy
            if old is not None:
                await old.aclose()
            return self._client

    async def post(self, *args, **kwargs):
        return await (await self._ensure()).post(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await (await self._ensure()).get(*args, **kwargs)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
