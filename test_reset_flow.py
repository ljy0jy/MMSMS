"""Standalone test: register-vs-reset branching in /send-code + /verify-code.

Runs the real FastAPI app against a throwaway SQLite DB with all upstream
network calls stubbed, so we exercise the actual routing/DB logic (flow column,
VERIFY_UPSTREAM fallback, endpoint selection) without hitting the third party.

    python3 test_reset_flow.py
"""
import os
import tempfile

# Must be set before importing app.* (config reads env at import time).
_dbfd, _dbpath = tempfile.mkstemp(suffix=".sqlite")
os.close(_dbfd)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_dbpath}"
os.environ["UPSTREAM_PROXY_API"] = ""  # disable proxy → direct client, no novproxy call

from fastapi.testclient import TestClient  # noqa: E402

from app import upstream  # noqa: E402
from app.main import app  # noqa: E402

CALLS: list[tuple[str, dict]] = []


def _stub_upstream(*, registered: bool, leak_code: str | None, correct_code: str):
    """Patch upstream.* with fakes that record calls and mimic the real shapes."""

    async def apparatus_make(client, base_url, env, *, android_id, gaid):
        CALLS.append(("apparatus-make", {}))
        return "stub-osghu"

    async def verify_user_account(client, base_url, phone, region, env):
        CALLS.append(("verify-user-account", {"phone": phone}))
        return {"wjmgawm": 0, "yftkram": "success",
                "atkjtu": {"dclogpot": 1 if registered else 0}}

    async def finished_check(client, base_url, phone, env):
        CALLS.append(("finished-check", {"phone": phone}))
        return {"wjmgawm": 0, "yftkram": "success", "atkjtu": {}}

    async def send_sms_code(client, base_url, phone, channel, env):
        CALLS.append(("text-user/transfer", {"phone": phone}))
        atk = {"pococb": channel, "phhrwmjp": 0}
        if leak_code is not None:
            atk["twwxfuya"] = int(leak_code)
        return {"wjmgawm": 0, "yftkram": "success", "atkjtu": atk}

    async def send_reset_code(client, base_url, phone, channel, env):
        CALLS.append(("reset-text-service", {"phone": phone}))
        # reset endpoint never leaks the code
        return {"wjmgawm": 0, "yftkram": "success",
                "atkjtu": {"zpleg": channel, "owb": 0}}

    async def sign_up(client, base_url, phone, code, password, env):
        CALLS.append(("clientSignUp", {"phone": phone, "code": code, "password": password}))
        ok = str(code) == correct_code
        return {"wjmgawm": 0, "yftkram": "success"} if ok \
            else {"wjmgawm": 7104, "yftkram": "wrong code"}

    async def reset_password(client, base_url, phone, code, password, env):
        CALLS.append(("userPass/refresh", {"phone": phone, "code": code, "password": password}))
        ok = str(code) == correct_code
        return {"wjmgawm": 0, "yftkram": "success"} if ok \
            else {"wjmgawm": 7104, "yftkram": "wrong code"}

    upstream.apparatus_make = apparatus_make
    upstream.verify_user_account = verify_user_account
    upstream.finished_check = finished_check
    upstream.send_sms_code = send_sms_code
    upstream.send_reset_code = send_reset_code
    upstream.sign_up = sign_up
    upstream.reset_password = reset_password


def _paths():
    return [c[0] for c in CALLS]


def run():
    with TestClient(app) as client:
        # ---- Scenario A: UNREGISTERED → register flow, local code compare ----
        CALLS.clear()
        _stub_upstream(registered=False, leak_code="1234", correct_code="1234")
        phone = "09111111111"
        r = client.post("/send-code", json={"phone": phone}).json()
        assert r["success"] and r["trace_id"], r
        assert "text-user/transfer" in _paths(), _paths()
        assert "reset-text-service" not in _paths(), _paths()
        trace = r["trace_id"]

        # wrong code → local 7104, must NOT hit any upstream verify endpoint
        CALLS.clear()
        r = client.post("/verify-code", json={"trace_id": trace, "code": "0000"}).json()
        assert r["code"] == 7104 and not r["success"], r
        assert CALLS == [], ("local compare must not call upstream", _paths())

        # right code → local 0
        r = client.post("/verify-code", json={"trace_id": trace, "code": "1234"}).json()
        assert r["code"] == 0 and r["success"] and r["phone"] == phone, r
        assert CALLS == [], ("local compare must not call upstream", _paths())
        print("[A] unregistered → register flow, local compare      OK")

        # ---- Scenario B: REGISTERED → reset flow, upstream userPass/refresh ----
        CALLS.clear()
        _stub_upstream(registered=True, leak_code=None, correct_code="4321")
        phone = "09222222222"
        r = client.post("/send-code", json={"phone": phone}).json()
        assert r["success"] and r["trace_id"], r
        p = _paths()
        assert "finished-check" in p and "reset-text-service" in p, p
        assert "text-user/transfer" not in p, p
        # warm-up order: verify-user-account → finished-check → reset-text-service
        assert p.index("verify-user-account") < p.index("finished-check") < p.index("reset-text-service"), p
        trace = r["trace_id"]

        # wrong code → upstream userPass/refresh judges it, collapses to 7104
        CALLS.clear()
        r = client.post("/verify-code", json={"trace_id": trace, "code": "0000"}).json()
        assert r["code"] == 7104 and not r["success"], r
        assert "userPass/refresh" in _paths(), _paths()
        assert "clientSignUp" not in _paths(), _paths()

        # right code → upstream resets password, returns 0; password is random
        CALLS.clear()
        r = client.post("/verify-code", json={"trace_id": trace, "code": "4321"}).json()
        assert r["code"] == 0 and r["success"] and r["phone"] == phone, r
        refresh = [c for c in CALLS if c[0] == "userPass/refresh"]
        assert refresh and refresh[0][1]["password"], ("expected a random password", CALLS)
        assert "clientSignUp" not in _paths(), _paths()
        print("[B] registered → reset flow, upstream userPass/refresh OK")

    print("\nALL PASS")


if __name__ == "__main__":
    try:
        run()
    finally:
        os.unlink(_dbpath)
