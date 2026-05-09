"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import devices, upstream
from .config import BASE_URL_FALLBACK, UPSTREAM_VERIFY
from .db import get_config, init_schema, make_engine_and_session, seed_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(http2=False, timeout=20.0, verify=UPSTREAM_VERIFY)
    engine, session_factory = make_engine_and_session()
    await init_schema(engine)
    await seed_config(engine, "upstream_base_url", BASE_URL_FALLBACK)
    app.state.db_engine = engine
    app.state.db_session = session_factory
    try:
        yield
    finally:
        await app.state.http.aclose()
        await engine.dispose()


app = FastAPI(title="MMSMS proxy", version="0.3.0", lifespan=lifespan)


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
    phone: str = Field(..., min_length=1, description="手机号")
    code: str = Field(..., min_length=1, description="收到的验证码")
    password: str = Field(..., min_length=1, description="一并设置的登录密码")


class UpstreamResponse(BaseModel):
    code: int
    msg: str
    success: bool
    raw: dict[str, Any]


def _wrap(decoded: dict[str, Any]) -> UpstreamResponse:
    code = int(decoded.get("wjmgawm", -1))
    msg = str(decoded.get("yftkram", ""))
    return UpstreamResponse(code=code, msg=msg, success=code == 0, raw=decoded)


async def _resolve_request_ctx(phone: str) -> tuple[dict[str, Any], str]:
    """Per-request setup: pull base_url from app_config, resolve device envelope."""
    sf = app.state.db_session
    async with sf() as session:
        base_url = await get_config(session, "upstream_base_url", BASE_URL_FALLBACK)
    try:
        device = await devices.get_or_create_device(sf, app.state.http, phone, base_url)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream error during device bootstrap: {e}") from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return device, base_url


def _call_or_502(coro):
    async def _inner():
        try:
            return await coro
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"upstream error: {e}") from e
    return _inner()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    device, base_url = await _resolve_request_ctx(req.phone)
    decoded = await _call_or_502(
        upstream.verify_user_account(app.state.http, base_url, req.phone, req.region, device)
    )
    code = int(decoded.get("wjmgawm", -1))
    msg = str(decoded.get("yftkram", ""))

    exists: bool | None = None
    atkjtu = decoded.get("atkjtu")
    if isinstance(atkjtu, dict) and "dclogpot" in atkjtu:
        exists = atkjtu["dclogpot"] == 1

    return VerifyResponse(code=code, msg=msg, exists=exists, raw=decoded)


@app.post("/send-code", response_model=UpstreamResponse)
async def send_code(req: SendCodeRequest) -> UpstreamResponse:
    """触发上游 text-user/transfer 给手机号发一条短信验证码。

    Mimics the apk: call existence/verify-user-account first, then
    text-user/transfer. The pre-call appears to be required for the upstream
    to actually deliver the SMS — without it the server returns success but
    silently drops the message. We discard verify's response and only return
    the transfer result to the caller.
    """
    device, base_url = await _resolve_request_ctx(req.phone)
    await _call_or_502(
        upstream.verify_user_account(app.state.http, base_url, req.phone, 1, device)
    )
    decoded = await _call_or_502(
        upstream.send_sms_code(app.state.http, base_url, req.phone, req.channel, device)
    )
    return _wrap(decoded)


@app.post("/verify-code", response_model=UpstreamResponse)
async def verify_code(req: VerifyCodeRequest) -> UpstreamResponse:
    """通过 register/clientSignUp 校验短信验证码（顺带完成注册并设置密码）。"""
    device, base_url = await _resolve_request_ctx(req.phone)
    decoded = await _call_or_502(
        upstream.verify_sms_code(
            app.state.http, base_url, req.phone, req.code, req.password, device
        )
    )
    return _wrap(decoded)
