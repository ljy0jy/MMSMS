"""Per-phone device fingerprint: generate locally, bootstrap ``osghu`` from upstream, persist.

Also keeps the last verification code issued by the upstream for the phone, so
``/verify-code`` can validate locally without re-hitting upstream (and without
inadvertently registering an account via ``register/clientSignUp``).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import upstream
from .config import HKC_CONST, STATIC_DEVICE_DEFAULTS
from .db import PhoneDevice

CODE_TTL_SECONDS = 600  # 10 min — local TTL on the issued twwxfuya


def _random_android_id() -> str:
    """16 hex chars, matches what Settings.Secure.ANDROID_ID looks like on stock Android."""
    return secrets.token_hex(8)


def _random_gaid() -> str:
    """UUID v4, matches AdvertisingIdClient.getId() output."""
    return str(uuid.uuid4())


def device_to_envelope(d: PhoneDevice) -> dict[str, Any]:
    """Build the ``akmcchi`` block exactly as ApiRequest.c() in the apk does."""
    return {
        "hkc": d.hkc,
        "feoknc": "android",
        "bhyi": "1.21",
        "gvgservh": 21,
        "wyb": "com.easy.bayarsantai",
        "gptkwex": "app",
        "osghu": d.osghu or "",
        "lwnxt": {
            "cstnxjv": d.brand,
            "vgbo": d.os_release,
            "dnb": d.sdk_int,
            "lpyblk": d.android_id,
            "mthwtv": d.gaid,
            "eaa": d.model,
        },
        "hbf": "",
        "sezwywc": 1,
        "pxh": 1,
        "kmbzxcbl": 1,
    }


async def get_or_create_device(
    session_factory: async_sessionmaker,
    http: httpx.AsyncClient,
    phone: str,
    base_url: str,
) -> dict[str, Any]:
    """Return a ready-to-use device envelope for *phone*.

    On first sight of a phone: generate android_id + GAID, persist a row,
    bootstrap ``osghu`` via ``user/construct/apparatus-make`` and persist it too.
    Subsequent calls just read the row.
    """
    async with session_factory() as session:
        row = (await session.execute(
            select(PhoneDevice).where(PhoneDevice.phone == phone)
        )).scalar_one_or_none()

        if row is None:
            row = PhoneDevice(
                phone=phone,
                android_id=_random_android_id(),
                gaid=_random_gaid(),
                osghu="",
                brand=STATIC_DEVICE_DEFAULTS["brand"],
                model=STATIC_DEVICE_DEFAULTS["model"],
                os_release=STATIC_DEVICE_DEFAULTS["os_release"],
                sdk_int=STATIC_DEVICE_DEFAULTS["sdk_int"],
                hkc=HKC_CONST,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

        if not row.osghu:
            envelope = device_to_envelope(row)
            osghu = await upstream.apparatus_make(
                http, base_url, envelope, android_id=row.android_id, gaid=row.gaid
            )
            row.osghu = osghu
            await session.commit()
            await session.refresh(row)

        return device_to_envelope(row)


async def record_code(session_factory: async_sessionmaker, phone: str, code: str) -> None:
    """Persist the latest twwxfuya issued by upstream so /verify-code can compare locally.

    No-op if the phone row doesn't exist (caller should have ensured it does).
    """
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        if row is None:
            return
        row.last_code = code
        row.last_code_at = datetime.utcnow()
        await session.commit()


async def match_code(
    session_factory: async_sessionmaker,
    phone: str,
    code: str,
    ttl_seconds: int = CODE_TTL_SECONDS,
) -> tuple[int, str]:
    """Compare *code* against the last issued twwxfuya.

    Returns ``(code, msg)`` where the int mirrors the upstream convention:
        0     — match
        7104  — mismatch (same as upstream's "wrong code" code)
        -1    — no code on file (caller never invoked /send-code)
        -2    — last code expired (older than ttl_seconds)
    """
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        if row is None or not row.last_code or row.last_code_at is None:
            return -1, "no verification code on file for this phone"
        age = datetime.utcnow() - row.last_code_at
        if age > timedelta(seconds=ttl_seconds):
            return -2, f"verification code expired ({int(age.total_seconds())}s old)"
        if str(code).strip() != row.last_code:
            return 7104, "verification code mismatch"
        return 0, "success"
