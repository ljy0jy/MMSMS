"""Async SQLAlchemy setup + ORM model for per-phone device fingerprints."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


def make_engine_and_session() -> tuple:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set; populate proxy/.env or export it.")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
    session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session


async def init_schema(engine) -> None:
    """Create the phone_devices table if it doesn't exist. The database itself must
    already exist — we don't try CREATE DATABASE since that needs elevated perms."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
