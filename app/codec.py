"""ky_data codec — AES/CBC/PKCS5 with the static key & IV baked into the apk."""
from __future__ import annotations

import base64
import json
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

KEY = b"XckH3I7TpYRAcRSN"
IV = b"ajagMKjxqKfI6UV2"


def encrypt(plain: str | dict[str, Any]) -> str:
    if not isinstance(plain, str):
        plain = json.dumps(plain, separators=(",", ":"), ensure_ascii=False)
    ct = AES.new(KEY, AES.MODE_CBC, IV).encrypt(
        pad(plain.encode("utf-8"), AES.block_size)
    )
    return base64.b64encode(ct).decode()


def decrypt(blob: str | dict[str, Any]) -> str:
    if isinstance(blob, dict):
        blob = blob["ky_data"]
    raw = AES.new(KEY, AES.MODE_CBC, IV).decrypt(base64.b64decode(blob))
    return unpad(raw, AES.block_size).decode("utf-8")


def wrap(plain: dict[str, Any]) -> dict[str, str]:
    """Build the {"ky_data": "..."} envelope sent over the wire."""
    return {"ky_data": encrypt(plain)}


def unwrap(body: dict[str, Any]) -> dict[str, Any]:
    """Decrypt the {"ky_data": "..."} envelope back to a JSON dict."""
    return json.loads(decrypt(body))
