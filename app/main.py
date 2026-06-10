"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from . import devices, upstream
from .config import BASE_URL_FALLBACK, UPSTREAM_PROXY_API, UPSTREAM_VERIFY
from .db import UpstreamLog, get_config, init_schema, make_engine_and_session, seed_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine, session_factory = make_engine_and_session()
    await init_schema(engine)
    await seed_config(engine, "upstream_base_url", BASE_URL_FALLBACK)
    app.state.db_engine = engine
    app.state.db_session = session_factory
    upstream.configure_logging(session_factory)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="MMSMS proxy", version="0.10.0", lifespan=lifespan)


@asynccontextmanager
async def _client_for_phone(phone: str):
    """Yield an ``httpx.AsyncClient`` whose outgoing IP is bound to *phone*.

    When ``UPSTREAM_PROXY_API`` is set, the proxy is resolved via
    ``devices.acquire_proxy_for_phone`` (cached per phone, rotated on expiry).
    When unset, a direct-connection client is yielded so local dev still works.
    """
    proxy_url: str | None = None
    if UPSTREAM_PROXY_API:
        proxy_url = await devices.acquire_proxy_for_phone(
            app.state.db_session, phone, UPSTREAM_PROXY_API
        )
    # connect=5s so a dead proxy fails fast (the retry path then rotates and tries
    # again); read=15s leaves the actual upstream call enough headroom.
    kwargs: dict[str, Any] = dict(
        http2=False,
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
        verify=UPSTREAM_VERIFY,
    )
    if proxy_url:
        kwargs["proxy"] = proxy_url
    async with httpx.AsyncClient(**kwargs) as client:
        yield client


class VerifyRequest(BaseModel):
    phone: str = Field(..., min_length=1, description="本地手机号，例如 098512515")
    region: int = Field(1, description="区号代码，默认 1（缅甸）")


class VerifyResponse(BaseModel):
    code: int
    msg: str
    exists: bool | None = None
    raw: dict[str, Any]


class SendCodeRequest(BaseModel):
    phone: str = Field(..., min_length=1, description="收短信的手机号")
    channel: str = Field("TEXT", description="发送渠道，默认 TEXT (短信)")


class VerifyCodeRequest(BaseModel):
    trace_id: str = Field(..., min_length=1, description="send-code 返回的 trace_id")
    code: str = Field(..., min_length=1, description="收到的验证码")


class VerifyCodeResponse(BaseModel):
    code: int    # 0=match, 7104=mismatch, -1=trace_id not found, -2=expired, -3=too many wrong tries
    msg: str
    success: bool
    phone: str | None = None  # echoed from the matched attempt; null when -1


class UpstreamResponse(BaseModel):
    code: int
    msg: str
    success: bool
    raw: dict[str, Any]


class SendCodeResponse(BaseModel):
    code: int
    msg: str
    success: bool
    trace_id: str | None = None   # null when upstream issued no fresh code (e.g., dedup)
    raw: dict[str, Any]


def _wrap(decoded: dict[str, Any]) -> UpstreamResponse:
    code = int(decoded.get("wjmgawm", -1))
    msg = str(decoded.get("yftkram", ""))
    return UpstreamResponse(code=code, msg=msg, success=code == 0, raw=decoded)


async def _resolve_base_url() -> str:
    sf = app.state.db_session
    async with sf() as session:
        return await get_config(session, "upstream_base_url", BASE_URL_FALLBACK)


async def _with_proxy_retry(phone: str, fn) -> Any:
    """Run *fn(http, base_url)* once. On a connection-layer failure (httpx
    RequestError — proxy unreachable / dead / timeout / etc.), invalidate the
    cached proxy for *phone* so the next acquire fetches a fresh IP, then run
    *fn* one more time. HTTP 4xx/5xx responses from upstream are *not* retried
    (rotating IPs won't fix a server-side rejection).
    """
    base_url = await _resolve_base_url()

    async def _attempt():
        async with _client_for_phone(phone) as http:
            return await fn(http, base_url)

    try:
        return await _attempt()
    except httpx.RequestError as first_err:
        await devices.invalidate_proxy_for_phone(app.state.db_session, phone)
        try:
            return await _attempt()
        except httpx.RequestError as retry_err:
            raise HTTPException(
                status_code=502,
                detail=f"upstream unreachable after proxy rotation: {retry_err!r}",
            ) from retry_err
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"upstream error: {e}") from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"upstream error: {e}") from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/upstream-logs")
async def upstream_logs(phone: str | None = None, path: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Recent upstream (third-party API) calls, decrypted, newest first.

    Filter by ``phone`` and/or ``path`` to chase a specific number's flow.
    Handy for "user got the right code but verify failed" — you can read exactly
    what text-user/transfer and register/clientSignUp returned for that phone.
    """
    limit = max(1, min(limit, 500))
    sf = app.state.db_session
    async with sf() as session:
        stmt = select(UpstreamLog).order_by(desc(UpstreamLog.id)).limit(limit)
        if phone:
            stmt = stmt.where(UpstreamLog.phone == phone)
        if path:
            stmt = stmt.where(UpstreamLog.path.like(f"%{path}%"))
        rows = (await session.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "logs": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "phone": r.phone,
                "path": r.path,
                "status": r.status,
                "biz_code": r.biz_code,
                "req": r.req,
                "resp": r.resp,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ],
    }


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    async def _do(http: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
        device = await devices.get_or_create_device(
            app.state.db_session, http, req.phone, base_url
        )
        return await upstream.verify_user_account(
            http, base_url, req.phone, req.region, device
        )

    decoded = await _with_proxy_retry(req.phone, _do)
    code = int(decoded.get("wjmgawm", -1))
    msg = str(decoded.get("yftkram", ""))

    exists: bool | None = None
    atkjtu = decoded.get("atkjtu")
    if isinstance(atkjtu, dict) and "dclogpot" in atkjtu:
        exists = atkjtu["dclogpot"] == 1

    return VerifyResponse(code=code, msg=msg, exists=exists, raw=decoded)


@app.post("/send-code", response_model=SendCodeResponse)
async def send_code(req: SendCodeRequest) -> SendCodeResponse:
    """触发上游 text-user/transfer 给手机号发一条短信验证码。

    Mimics the apk: call existence/verify-user-account first, then
    text-user/transfer. The pre-call is required for the upstream to actually
    deliver the SMS — without it the server returns success but silently drops
    the message. The twwxfuya returned by transfer is the verification code
    itself; we persist it as a verification_attempts row keyed by a fresh
    trace_id (uuid4), and surface that trace_id in the response so the caller
    can pass it back to /verify-code without re-supplying the phone number.

    All three upstream calls (apparatus-make if needed, verify-user-account,
    text-user/transfer) egress through the *same* phone-scoped proxy IP. If
    the connection drops at any point during the sequence (proxy died), the
    cached proxy is invalidated and the entire sequence is retried once with
    a fresh IP.
    """
    async def _do(http: httpx.AsyncClient, base_url: str) -> tuple[dict[str, Any], str]:
        device = await devices.get_or_create_device(
            app.state.db_session, http, req.phone, base_url
        )
        existence = await upstream.verify_user_account(
            http, base_url, req.phone, 1, device
        )
        # dclogpot == 1 → the number is already registered, so a fresh sign-up
        # would be rejected. Fall back to the password-reset flow instead: same
        # SMS-OTP UX, but the code is delivered/validated via the reset endpoints.
        atkjtu = existence.get("atkjtu")
        registered = isinstance(atkjtu, dict) and atkjtu.get("dclogpot") == 1
        if registered:
            await upstream.finished_check(http, base_url, req.phone, device)
            decoded = await upstream.send_reset_code(
                http, base_url, req.phone, req.channel, device
            )
            return decoded, "reset"
        decoded = await upstream.send_sms_code(
            http, base_url, req.phone, req.channel, device
        )
        return decoded, "register"

    decoded, flow = await _with_proxy_retry(req.phone, _do)

    base = _wrap(decoded)

    # Always hand back a usable trace_id when the upstream accepted the request,
    # even on dedup (no fresh twwxfuya). When a code was issued we store it for a
    # local compare; when it wasn't, we store an empty code so /verify-code knows
    # to fall back to the upstream check instead. The reset flow never leaks a
    # code (no twwxfuya), so it always stores "" and validates via userPass/refresh.
    trace_id: str | None = None
    if base.success:
        issued = (decoded.get("atkjtu") or {}).get("twwxfuya")
        # twwxfuya arrives as a JSON int, so a 4-digit code that starts with a
        # zero (e.g. 0820) loses its leading zero when stringified -> "820", and
        # then never matches the 4-digit code the user types off the SMS. Myanmar
        # OTPs are always 4 digits, so pad back to width 4.
        code_str = str(issued).zfill(4) if issued is not None else ""
        trace_id = await devices.record_attempt(
            app.state.db_session, req.phone, code_str, flow
        )
    return SendCodeResponse(
        code=base.code, msg=base.msg, success=base.success,
        trace_id=trace_id, raw=base.raw,
    )


@app.post("/verify-code", response_model=VerifyCodeResponse)
async def verify_code(req: VerifyCodeRequest) -> VerifyCodeResponse:
    """校验：用 send-code 返回的 trace_id 找到对应 attempt，比对 code。

    两条路径：
    - **本地比对**（默认）：send-code 当时拿到了验证码（twwxfuya），存了下来，这里
      纯本地比对，不打上游、**不会注册/重置账号**。
    - **上游校验**（回退）：本地没有可比对的码（register 流程 dedup 没下发新码，或
      reset 流程本就不下发 twwxfuya）。此时打上游让其判定验证码对错（wjmgawm 0=对 /
      7104=错）。两条 flow 都打 account/userPass/refresh 校验：
        - `register` → **码对会用随机密码刷新该号（已注册号 clientSignUp 不再返回 0，
          故统一走 userPass/refresh）**
        - `reset`    → **码对会用随机密码重置该号密码**
      只有在本地确实无码可比时才会走到这里。
    """
    rc, msg, phone = await devices.match_by_trace(
        app.state.db_session, req.trace_id, req.code
    )
    if rc != devices.VERIFY_UPSTREAM:
        return VerifyCodeResponse(
            code=rc, msg=msg, success=rc == 0,
            phone=phone or None,
        )

    # Fallback: no local code for this trace_id — ask upstream to judge the code.
    # Both flows validate via account/userPass/refresh (the apk's real "code →
    # token" step): it returns wjmgawm 0 for a correct code regardless of whether
    # the number is brand-new or already registered. We used to call
    # register/clientSignUp on the register flow, but that only returns 0 for a
    # never-seen number — once the number exists it stops returning success even
    # for a correct code ("接口没返回成功"). Both take a random throwaway password
    # and egress through the same phone-scoped proxy IP, behind the apk's
    # verify-user-account pre-call.
    password = devices.generate_password()
    flow = await devices.get_attempt_flow(app.state.db_session, req.trace_id)

    async def _do(http: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
        device = await devices.get_or_create_device(
            app.state.db_session, http, phone, base_url
        )
        await upstream.verify_user_account(http, base_url, phone, 1, device)
        if flow == "reset":
            return await upstream.reset_password(
                http, base_url, phone, req.code, password, device
            )
        return await upstream.verify_code_upstream(
            http, base_url, phone, req.code, password, device
        )

    decoded = await _with_proxy_retry(phone, _do)
    upstream_code = int(decoded.get("wjmgawm", -1))
    upstream_msg = str(decoded.get("yftkram", ""))
    frc, fmsg, fphone = await devices.finalize_upstream_result(
        app.state.db_session, req.trace_id, upstream_code, upstream_msg
    )
    return VerifyCodeResponse(
        code=frc, msg=fmsg, success=frc == 0,
        phone=fphone or None,
    )
