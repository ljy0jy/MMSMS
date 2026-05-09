"""Per-phone device fingerprint + per-attempt verification record.

Two responsibilities:
1. Generate / look up the device envelope used in upstream calls (one row per phone).
2. Persist each issued verification code keyed by a fresh trace_id so /verify-code
   can validate locally without re-hitting upstream (and without inadvertently
   registering an account via ``register/clientSignUp``).
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
from .db import PhoneDevice, VerificationAttempt
from .proxy_provider import fetch_proxy_url

CODE_TTL_SECONDS = 600  # 10 min — local TTL on stored attempts
PROXY_TTL_SECONDS = 480  # 8 min — provider rotates every ~10 min, leave 2 min slack


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


async def _ensure_row(session_factory: async_sessionmaker, phone: str) -> None:
    """Insert a phone_devices row with a random fingerprint if one doesn't exist.
    Idempotent. Does not touch upstream — pure DB op so callers can pick a proxy
    before bootstrapping osghu via apparatus-make.
    """
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        if row is not None:
            return
        session.add(PhoneDevice(
            phone=phone,
            android_id=_random_android_id(),
            gaid=_random_gaid(),
            osghu="",
            brand=STATIC_DEVICE_DEFAULTS["brand"],
            model=STATIC_DEVICE_DEFAULTS["model"],
            os_release=STATIC_DEVICE_DEFAULTS["os_release"],
            sdk_int=STATIC_DEVICE_DEFAULTS["sdk_int"],
            hkc=HKC_CONST,
        ))
        await session.commit()


async def invalidate_proxy_for_phone(
    session_factory: async_sessionmaker,
    phone: str,
) -> None:
    """Force the next ``acquire_proxy_for_phone`` to fetch a fresh IP.

    Called after a request through the cached proxy fails at the connection
    layer (proxy died mid-TTL). Cheaper than waiting for the TTL to expire.
    """
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        if row is None:
            return
        row.proxy_url = ""
        row.proxy_expires_at = None
        await session.commit()


async def acquire_proxy_for_phone(
    session_factory: async_sessionmaker,
    phone: str,
    api_url: str,
    ttl_seconds: int = PROXY_TTL_SECONDS,
) -> str:
    """Return the cached proxy URL for *phone*; fetch a fresh one when missing
    or expired. Creates the phone_devices row first if needed so the new IP
    has somewhere to land.
    """
    await _ensure_row(session_factory, phone)
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        now = datetime.utcnow()
        if row.proxy_url and row.proxy_expires_at and row.proxy_expires_at > now:
            return row.proxy_url
        new_url = await fetch_proxy_url(api_url)
        row.proxy_url = new_url
        row.proxy_expires_at = now + timedelta(seconds=ttl_seconds)
        await session.commit()
        return new_url


async def get_or_create_device(
    session_factory: async_sessionmaker,
    http: httpx.AsyncClient,
    phone: str,
    base_url: str,
) -> dict[str, Any]:
    """Return a ready-to-use device envelope for *phone*.

    Assumes the row already exists (call ``acquire_proxy_for_phone`` or
    ``_ensure_row`` first). Bootstraps ``osghu`` via apparatus-make on first
    use; the call is routed through whatever ``http`` client the caller hands
    in (which should be the per-phone proxied client).
    """
    await _ensure_row(session_factory, phone)
    async with session_factory() as session:
        row = await session.get(PhoneDevice, phone)
        if not row.osghu:
            envelope = device_to_envelope(row)
            osghu = await upstream.apparatus_make(
                http, base_url, envelope, android_id=row.android_id, gaid=row.gaid
            )
            row.osghu = osghu
            await session.commit()
            await session.refresh(row)
        return device_to_envelope(row)


async def record_attempt(
    session_factory: async_sessionmaker, phone: str, code: str
) -> str:
    """Persist a verification attempt and return its trace_id (uuid4).

    Caller surfaces the trace_id in the /send-code response; /verify-code
    uses it to look the row back up.
    """
    trace_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(VerificationAttempt(trace_id=trace_id, phone=phone, code=code))
        await session.commit()
    return trace_id


async def match_by_trace(
    session_factory: async_sessionmaker,
    trace_id: str,
    code: str,
    ttl_seconds: int = CODE_TTL_SECONDS,
) -> tuple[int, str, str]:
    """Compare *code* against the attempt identified by *trace_id*.

    Returns ``(result_code, msg, phone)``:
        0     — match (phone is the one this trace_id was issued for)
        7104  — mismatch (mirrors upstream's "wrong code" code)
        -1    — trace_id not found (never issued, or wrong id)
        -2    — attempt expired (older than ttl_seconds)
    ``phone`` is "" when result_code is -1, otherwise echoes the stored phone.
    """
    async with session_factory() as session:
        row = await session.get(VerificationAttempt, trace_id)
        if row is None:
            return -1, "trace_id not found", ""
        age = datetime.utcnow() - row.created_at
        if age > timedelta(seconds=ttl_seconds):
            return -2, f"verification code expired ({int(age.total_seconds())}s old)", row.phone
        if str(code).strip() != row.code:
            return 7104, "verification code mismatch", row.phone
        return 0, "success", row.phone
