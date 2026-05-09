"""Upstream URL + static fingerprint constants + DB / env config.

The fields under ``STATIC_DEVICE_DEFAULTS`` are the parts of ``akmcchi`` that the
real apk hardcodes (apk constants like ``hkc``, package name, app version) or
that we keep frozen on purpose (``brand``/``model``/``os_release``/``sdk_int``
— a single Xiaomi MIX 2S Android 10 profile per the captured trace). The
*per-phone* parts (``android_id``/``gaid``/``osghu``) live in MySQL — see
``app.devices``.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("UPSTREAM_BASE_URL", "https://eworr.onetooutlimitss.com/streamservice")

UPSTREAM_VERIFY = os.getenv("UPSTREAM_VERIFY", "false").lower() in {"1", "true", "yes", "on"}

DATABASE_URL = os.getenv("DATABASE_URL", "")

USER_AGENT = "okhttp/4.10.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "content-type": "application/json; charset=utf-8",
}

# Cleartext session-y fields the apk puts on every request (ApiRequest.d).
DCVNQ = "xeumd"
VSWYD = "vrpen"

# apk constant — every install ships with this exact value (ApiRequest.c line 59).
HKC_CONST = "f0jCuicdsDFrBvI9"

# Frozen device profile. Per-phone fields live in DB; the rest stay on this single profile.
STATIC_DEVICE_DEFAULTS = {
    "brand": "Xiaomi",
    "model": "MIX 2S",
    "os_release": "10",
    "sdk_int": 29,
}
