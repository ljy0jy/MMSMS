"""Async SQLAlchemy setup + ORM models.

Two tables:

- ``phone_devices`` — per-phone-number virtual device fingerprint
- ``app_config``    — generic key/value runtime config (e.g. upstream base URL)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
    last_code:    Mapped[str]            = mapped_column(String(16), default="")
    last_code_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
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


def make_engine_and_session() -> tuple:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set; populate proxy/.env or export it.")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
    session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session


async def init_schema(engine) -> None:
    """Create the tables if they don't exist. The database itself must
    already exist — we don't try CREATE DATABASE since that needs elevated perms."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
