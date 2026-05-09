"""Thin client around the TrustKyat upstream API.

Every call carries a per-phone ``akmcchi`` device envelope built by
``app.devices.device_to_envelope``.
"""
from __future__ import annotations

from typing import Any

import httpx

from .codec import unwrap, wrap
from .config import BASE_URL, DCVNQ, HEADERS, VSWYD


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
    path: str,
    payload: dict[str, Any],
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """POST to ``<BASE_URL>/<path>`` with an encrypted body and decrypt the response."""
    body = wrap(build_envelope(payload, device_envelope))
    r = await client.post(f"{BASE_URL}/{path.lstrip('/')}", json=body, headers=HEADERS)
    r.raise_for_status()
    return unwrap(r.json())


async def apparatus_make(
    client: httpx.AsyncClient,
    device_envelope: dict[str, Any],
    *,
    android_id: str,
    gaid: str,
) -> str:
    """POST ``user/construct/apparatus-make`` — server returns the per-device ``osghu``.

    Mirrors ``BaseViewActivity.x()``: send android_id + GAID, get back ``jegglxrh``
    which the apk stores as ``yzpbc`` and replays as ``osghu`` on every later call.
    """
    payload = {
        "rwwlrr": "",
        "zehtw": android_id,
        "lawu": 0,            # limit-ad-tracking off
        "tfygfhbu": 1,        # GAID present
        "wmz": 0,             # use real GAID (not the all-zero fallback)
        "geq": gaid,
    }
    decoded = await call(client, "user/construct/apparatus-make", payload, device_envelope)
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
    phone: str,
    region: int,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """existence/verify-user-account — returns whether the phone is registered."""
    return await call(
        client,
        "existence/verify-user-account",
        {"yxzjgupo": phone, "rbqc": region},
        device_envelope,
    )


async def send_sms_code(
    client: httpx.AsyncClient,
    phone: str,
    channel: str,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """text-user/transfer — server sends an SMS verification code to ``phone``."""
    return await call(
        client,
        "text-user/transfer",
        {"yfckb": phone, "ptawbtaq": channel},
        device_envelope,
    )


async def verify_sms_code(
    client: httpx.AsyncClient,
    phone: str,
    code: str,
    password: str,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """register/clientSignUp — validates the SMS code and creates the account."""
    return await call(
        client,
        "register/clientSignUp",
        {"semvjnx": phone, "bnn": code, "xpuesdg": password},
        device_envelope,
    )
