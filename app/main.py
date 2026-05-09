"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import devices, upstream
from .config import BASE_URL_FALLBACK, UPSTREAM_PROXY_API, UPSTREAM_VERIFY
from .db import get_config, init_schema, make_engine_and_session, seed_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine, session_factory = make_engine_and_session()
    await init_schema(engine)
    await seed_config(engine, "upstream_base_url", BASE_URL_FALLBACK)
    app.state.db_engine = engine
    app.state.db_session = session_factory
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="MMSMS proxy", version="0.7.2", lifespan=lifespan)


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
    code: int    # 0=match, 7104=mismatch, -1=trace_id not found, -2=expired
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
    async def _do(http: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
        device = await devices.get_or_create_device(
            app.state.db_session, http, req.phone, base_url
        )
        await upstream.verify_user_account(http, base_url, req.phone, 1, device)
        return await upstream.send_sms_code(
            http, base_url, req.phone, req.channel, device
        )

    decoded = await _with_proxy_retry(req.phone, _do)

    issued = (decoded.get("atkjtu") or {}).get("twwxfuya")
    trace_id: str | None = None
    if issued is not None:
        trace_id = await devices.record_attempt(app.state.db_session, req.phone, str(issued))

    base = _wrap(decoded)
    return SendCodeResponse(
        code=base.code, msg=base.msg, success=base.success,
        trace_id=trace_id, raw=base.raw,
    )


@app.post("/verify-code", response_model=VerifyCodeResponse)
async def verify_code(req: VerifyCodeRequest) -> VerifyCodeResponse:
    """本地校验：用 send-code 返回的 trace_id 找到对应 attempt，比对 code。

    不调用任何上游接口，**不会真注册账号**。校验成功只代表"该 trace_id 在 10
    分钟内对应的验证码与传入 code 一致"，业务侧再决定要不要走真注册。
    """
    rc, msg, phone = await devices.match_by_trace(
        app.state.db_session, req.trace_id, req.code
    )
    return VerifyCodeResponse(
        code=rc, msg=msg, success=rc == 0,
        phone=phone or None,
    )
