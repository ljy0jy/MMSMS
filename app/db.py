"""Async SQLAlchemy setup + ORM models.

Two tables:

- ``phone_devices`` — per-phone-number virtual device fingerprint
- ``app_config``    — generic key/value runtime config (e.g. upstream base URL)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class PhoneDevice(Base):
    __tablename__ = "phone_devices"

    phone:      Mapped[str] = mapped_column(String(32), primary_key=True)
    android_id: Mapped[str] = mapped_column(String(16))
    gaid:       Mapped[str] = mapped_column(String(36))
    osghu:      Mapped[str] = mapped_column(String(64), default="")
    brand:      Mapped[str] = mapped_column(String(32), default="Xiaomi")
    model:      Mapped[str] = mapped_column(String(32), default="MIX 2S")
    os_release: Mapped[str] = mapped_column(String(16), default="10")
    sdk_int:    Mapped[int] = mapped_column(Integer, default=29)
    hkc:        Mapped[str] = mapped_column(String(32), default="f0jCuicdsDFrBvI9")
    proxy_url:        Mapped[str]            = mapped_column(String(64), default="")
    proxy_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AppConfig(Base):
    __tablename__ = "app_config"

    config_key:   Mapped[str]      = mapped_column(String(64), primary_key=True)
    config_value: Mapped[str]      = mapped_column(Text)
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class VerificationAttempt(Base):
    """One row per /send-code call that successfully issued a code.

    The trace_id (uuid4) is what /verify-code identifies the attempt by,
    decoupling the verify call from the phone number.
    """
    __tablename__ = "verification_attempts"

    trace_id:   Mapped[str]      = mapped_column(String(40), primary_key=True)
    phone:      Mapped[str]      = mapped_column(String(32), index=True)
    code:       Mapped[str]      = mapped_column(String(16))
    # Which upstream flow this attempt belongs to: "register" (new account,
    # verified via register/clientSignUp) or "reset" (phone already registered,
    # verified via account/userPass/refresh). Decides which endpoint /verify-code
    # hits on the VERIFY_UPSTREAM path. Defaults to "register" for back-compat.
    flow:       Mapped[str]      = mapped_column(String(16), default="register", server_default="register")
    fail_count: Mapped[int]      = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UpstreamLog(Base):
    """One row per upstream (third-party API) HTTP call.

    Captures the *decrypted* request payload and response so issues like
    "user got the right code but verify failed" can be traced after the fact —
    the wire traffic is AES-encrypted, so without this there's nothing to read.
    Logging is best-effort: a failure to write a row never breaks the request.
    """
    __tablename__ = "upstream_logs"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    phone:       Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    path:        Mapped[str]      = mapped_column(String(64))
    status:      Mapped[int | None] = mapped_column(Integer, nullable=True)  # HTTP status
    biz_code:    Mapped[int | None] = mapped_column(Integer, nullable=True)  # upstream wjmgawm
    req:         Mapped[str]      = mapped_column(Text)   # decrypted request payload (vhhwl)
    resp:        Mapped[str]      = mapped_column(Text)   # decrypted response, or raw body on error
    error:       Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


def make_engine_and_session() -> tuple:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set; populate proxy/.env or export it.")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
    session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session


# Lightweight additive migrations applied on every startup. The deploy pipeline
# has no migration tool and relies on create_all, which never ALTERs an existing
# table — so a column added to an existing model (e.g. verification_attempts.flow
# in 0.10.0) must be backfilled here. Each entry is run in its own transaction and
# any "column already exists" error is swallowed, making this idempotent on both a
# fresh DB (create_all already added the column) and an old one (we add it).
_ADD_COLUMN_MIGRATIONS: list[str] = [
    "ALTER TABLE verification_attempts ADD COLUMN flow VARCHAR(16) NOT NULL DEFAULT 'register'",
]


async def init_schema(engine) -> None:
    """Create the tables if they don't exist, then apply additive migrations.

    The database itself must already exist — we don't try CREATE DATABASE since
    that needs elevated perms."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for ddl in _ADD_COLUMN_MIGRATIONS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(ddl))
        except Exception:
            # Column already present (fresh DB or prior run) — additive migration
            # is idempotent, so a duplicate-column error is expected and ignored.
            pass


async def seed_config(engine, key: str, value: str) -> None:
    """Idempotent: insert an app_config row only if the key is missing.
    Never overwrites — the operator's UPDATE in MySQL is the source of truth."""
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with SessionLocal() as session:
        existing = await session.get(AppConfig, key)
        if existing is None:
            session.add(AppConfig(config_key=key, config_value=value))
            await session.commit()


async def get_config(session: AsyncSession, key: str, default: str = "") -> str:
    row = await session.get(AppConfig, key)
    return row.config_value if row else default
