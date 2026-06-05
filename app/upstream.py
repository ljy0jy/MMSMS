"""Thin client around the upstream SMS / account API.

Every call carries:
- a ``base_url`` (read at request time from app_config.upstream_base_url) and
- a per-phone ``akmcchi`` device envelope built by ``app.devices.device_to_envelope``.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .codec import unwrap, wrap
from .config import DCVNQ, HEADERS, VSWYD

# Set once at startup (main.lifespan) so call() can persist a row per upstream
# request. Left None in contexts that never configure it (e.g. unit tests), in
# which case logging is simply skipped.
_log_session_factory: Any = None


def configure_logging(session_factory: Any) -> None:
    """Wire up the session factory used by call() to persist upstream_logs rows."""
    global _log_session_factory
    _log_session_factory = session_factory


def _phone_of(payload: dict[str, Any]) -> str | None:
    """Best-effort pull the phone number out of a per-call payload.

    Different upstream endpoints name the phone field differently:
    yfckb (text-user/transfer), semvjnx (clientSignUp), yxzjgupo
    (verify-user-account). apparatus-make carries no phone.
    """
    for k in ("yfckb", "semvjnx", "yxzjgupo"):
        v = payload.get(k)
        if v:
            return str(v)
    return None


async def _log_call(
    *,
    path: str,
    payload: dict[str, Any],
    status: int | None,
    decoded: dict[str, Any] | None,
    raw_body: str | None,
    error: str | None,
    duration_ms: int,
) -> None:
    """Persist one upstream call. Best-effort — never raises into the caller."""
    if _log_session_factory is None:
        return
    try:
        from .db import UpstreamLog  # local import avoids an import cycle

        biz_code = None
        if isinstance(decoded, dict) and "wjmgawm" in decoded:
            try:
                biz_code = int(decoded["wjmgawm"])
            except (TypeError, ValueError):
                biz_code = None
        resp_text = (
            json.dumps(decoded, ensure_ascii=False)
            if decoded is not None
            else (raw_body or "")
        )
        async with _log_session_factory() as session:
            session.add(UpstreamLog(
                phone=_phone_of(payload),
                path=path,
                status=status,
                biz_code=biz_code,
                req=json.dumps(payload, ensure_ascii=False),
                resp=resp_text[:60000],
                error=error,
                duration_ms=duration_ms,
            ))
            await session.commit()
    except Exception:
        # Logging must never take down a real request.
        pass


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
    """POST to ``<base_url>/<path>`` with an encrypted body and decrypt the response.

    Every call (success or failure) is persisted to ``upstream_logs`` with the
    decrypted request/response so problems can be traced afterwards.
    """
    body = wrap(build_envelope(payload, device_envelope))
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    started = time.perf_counter()
    status: int | None = None
    raw_body: str | None = None
    decoded: dict[str, Any] | None = None
    error: str | None = None
    try:
        r = await client.post(url, json=body, headers=HEADERS)
        status = r.status_code
        raw_body = r.text
        r.raise_for_status()
        decoded = unwrap(r.json())
        return decoded
    except Exception as e:  # noqa: BLE001 — log then re-raise unchanged
        error = repr(e)
        raise
    finally:
        await _log_call(
            path=path, payload=payload, status=status, decoded=decoded,
            raw_body=raw_body, error=error,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


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


async def sign_up(
    client: httpx.AsyncClient,
    base_url: str,
    phone: str,
    code: str,
    password: str,
    device_envelope: dict[str, Any],
) -> dict[str, Any]:
    """POST ``register/clientSignUp`` — the upstream's real code check.

    This is the only upstream endpoint that validates a verification *code*:
    ``wjmgawm == 0`` means the code matched, ``7104`` means it was wrong. It is
    used as the fallback for ``/verify-code`` when ``/send-code`` issued no local
    code (upstream dedup — see main.send_code).

    SIDE EFFECT: on a correct code the upstream actually *registers* the account
    with ``password``. We only fall back to this when we genuinely have no local
    code to compare against, so a correct code already implies the caller intends
    to proceed with that number.

    Field mapping (from the apk, decrypted): bnn=code, semvjnx=phone,
    xpuesdg=password.
    """
    return await call(
        client,
        base_url,
        "register/clientSignUp",
        {"bnn": code, "semvjnx": phone, "xpuesdg": password},
        device_envelope,
    )


