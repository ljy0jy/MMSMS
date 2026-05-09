"""Thin client around the upstream SMS / account API.

Every call carries:
- a ``base_url`` (read at request time from app_config.upstream_base_url) and
- a per-phone ``akmcchi`` device envelope built by ``app.devices.device_to_envelope``.
"""
from __future__ import annotations

from typing import Any

import httpx

from .codec import unwrap, wrap
from .config import DCVNQ, HEADERS, VSWYD


def build_envelope(payload: dict[str, Any], device_envelope: dict[str, Any]) -> dict[str, Any]:
    """Wrap the per-call ``vhhwl`` payload with the static + per-device fields."""
    return {
        "vhhwl": payload,
        "dcvnq": DCVNQ,
        "vswyd": VSWYD,
        "akmcchi": device_envelope,
    }


async def call(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    payload: dict[str, Any],
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """POST to ``<base_url>/<path>`` with an encrypted body and decrypt the response."""
    body = wrap(build_envelope(payload, device_envelope))
    r = await client.post(f"{base_url.rstrip('/')}/{path.lstrip('/')}", json=body, headers=HEADERS)
    r.raise_for_status()
    return unwrap(r.json())


async def apparatus_make(
    client: httpx.AsyncClient,
    base_url: str,
    device_envelope: dict[str, Any],
    *,
    android_id: str,
    gaid: str,
) -> str:
    """POST ``user/construct/apparatus-make`` — server returns the per-device ``osghu``."""
    payload = {
        "rwwlrr": "",
        "zehtw": android_id,
        "lawu": 0,            # limit-ad-tracking off
        "tfygfhbu": 1,        # GAID present
        "wmz": 0,             # use real GAID (not the all-zero fallback)
        "geq": gaid,
    }
    decoded = await call(client, base_url, "user/construct/apparatus-make", payload, device_envelope)
    code = int(decoded.get("wjmgawm", -1))
    if code != 0:
        raise RuntimeError(f"apparatus-make failed: code={code} msg={decoded.get('yftkram')!r} raw={decoded}")
    data = decoded.get("atkjtu") or {}
    osghu = data.get("jegglxrh") or ""
    if not osghu:
        raise RuntimeError(f"apparatus-make returned empty jegglxrh: {decoded}")
    return osghu


async def verify_user_account(
    client: httpx.AsyncClient,
    base_url: str,
    phone: str,
    region: int,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    return await call(
        client,
        base_url,
        "existence/verify-user-account",
        {"yxzjgupo": phone, "rbqc": region},
        device_envelope,
    )


async def send_sms_code(
    client: httpx.AsyncClient,
    base_url: str,
    phone: str,
    channel: str,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    return await call(
        client,
        base_url,
        "text-user/transfer",
        {"yfckb": phone, "ptawbtaq": channel},
        device_envelope,
    )


async def verify_sms_code(
    client: httpx.AsyncClient,
    base_url: str,
    phone: str,
    code: str,
    password: str,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    return await call(
        client,
        base_url,
        "register/clientSignUp",
        {"semvjnx": phone, "bnn": code, "xpuesdg": password},
        device_envelope,
    )
