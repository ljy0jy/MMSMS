# MMSMS Proxy API

> 上游 `com.easy.bayarsantai` 中转层。封装"发短信验证码 → 本地校验"两步，调用方不直接接触上游加解密、设备指纹、代理 IP 这些细节。

- **生产 Base URL**：`http://54.179.197.66:8002`
- **Content-Type**：所有请求一律 `application/json`
- **当前版本**：v0.8.0
- **OpenAPI 自动文档**：`GET /docs`（Swagger UI）/ `GET /openapi.json`（原始 schema）

---

## 业务流程

```
┌──────────┐  ① POST /send-code         ┌──────────┐
│  调用方  │ ─────────────────────────▶ │   MMSMS  │
│          │      {phone}               │   Proxy  │ ─▶ 上游下发短信验证码
│          │ ◀───────────────────────── │          │ ◀─ 返回 twwxfuya
│          │   {trace_id, success, ...} └──────────┘
│          │
│          │  ② 用户在手机上看到短信验证码
│          │     调用方提示用户输入 4 位 code
│          │
│          │  ③ POST /verify-code       ┌──────────┐
│          │ ─────────────────────────▶ │   MMSMS  │
│          │   {trace_id, code}         │   Proxy  │  (纯本地比对, 不打上游)
│          │ ◀───────────────────────── │          │
└──────────┘   {success, code, phone}   └──────────┘
```

`trace_id` 由 `/send-code` 服务端生成（uuid4），调用方负责保存并随 `/verify-code` 一起传回。

---

## `POST /send-code`

给手机号下发短信验证码。

### Request

```json
{
  "phone":   "09778001234",
  "channel": "TEXT"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `phone` | string | ✅ | 缅甸本地手机号，例 `09778001234` |
| `channel` | string |  | 下发渠道，默认 `TEXT`（短信） |

### Response 200

```json
{
  "code":     0,
  "msg":      "success",
  "success":  true,
  "trace_id": "d01dcad3-be4c-40da-8512-470ebf10ad42",
  "raw": {
    "wjmgawm": 0,
    "yftkram": "success",
    "atkjtu": {
      "pococb":   "TEXT",
      "phhrwmjp": 0,
      "twwxfuya": 3373
    }
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 上游 `wjmgawm`，**`0` 即成功** |
| `msg` | string | 上游 `yftkram`，错误时是缅甸语原文 |
| `success` | bool | `code == 0` |
| `trace_id` | string \| null | **本次发码的 uuid，调用 `/verify-code` 时必传**；上游 dedup 没下发新码时为 `null`（调用方应该用上一次的 trace_id） |
| `raw` | object | 上游解密后的完整响应（验证码本身在 `raw.atkjtu.twwxfuya`） |

### 错误响应

| HTTP | `code` | `msg` 范例 | 说明 |
|---|---|---|---|
| 200 | `1` | `"သင်ထည့်လိုက်သော..."` | 手机号格式不合法（缅甸语） |
| 502 | — | `"upstream error: ..."` | 上游网络异常 / 解密失败 |
| 502 | — | `"upstream unreachable after proxy rotation: ..."` | 代理 IP 死了，自动换 IP 重试 1 次仍失败 |

### Example

```sh
curl -X POST http://54.179.197.66:8002/send-code \
  -H 'content-type: application/json' \
  -d '{"phone":"09778001234"}'
```

---

## `POST /verify-code`

校验调用方收到的验证码。**纯本地比对**，不调用上游、不会注册账号。

### Request

```json
{
  "trace_id": "d01dcad3-be4c-40da-8512-470ebf10ad42",
  "code":     "3373"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `trace_id` | string | ✅ | `/send-code` 返回的 trace_id |
| `code` | string | ✅ | 用户输入的 4 位验证码 |

### Response 200

```json
{
  "code":    0,
  "msg":     "success",
  "success": true,
  "phone":   "09778001234"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | 见下表 |
| `msg` | string | 文字描述 |
| `success` | bool | `code == 0` |
| `phone` | string \| null | 该 trace_id 对应的手机号；`code == -1` 时为 `null` |

### `code` 字段含义

| `code` | `success` | 含义 | 调用方该怎么处理 |
|---|---|---|---|
| `0` | true | 验证码正确 | 走业务流程 |
| `7104` | false | 验证码错误 | 提示用户重新输入；`msg` 里会带 "(N attempts left)" |
| `-1` | false | trace_id 找不到 | trace_id 无效，让用户重新发码 |
| `-2` | false | trace_id 过期 | 距离发码已超过 **30 分钟**，让用户重新发码 |
| `-3` | false | 错误次数过多 | 同一 trace_id 连续输错 ≥ 5 次后锁死，**即使后面输对也返 -3**，让用户重新发码 |

### Example

```sh
curl -X POST http://54.179.197.66:8002/verify-code \
  -H 'content-type: application/json' \
  -d '{"trace_id":"d01dcad3-be4c-40da-8512-470ebf10ad42","code":"3373"}'
```

---

## `POST /verify`

查手机号是否在上游已注册。**会真的打上游**（带代理）。

### Request

```json
{
  "phone":  "09778001234",
  "region": 1
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `phone` | string | ✅ | — | 手机号 |
| `region` | int | | `1` | 区号（缅甸=1） |

### Response 200

```json
{
  "code":   0,
  "msg":    "success",
  "exists": false,
  "raw": {
    "wjmgawm": 0,
    "yftkram": "success",
    "atkjtu": {
      "dclogpot": 0,
      "...": "..."
    }
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` / `msg` | | 同前 |
| `exists` | bool \| null | `true` 该号已注册 / `false` 未注册 / `null` 上游没返这个字段 |
| `raw` | object | 上游解密响应 |

### Example

```sh
curl -X POST http://54.179.197.66:8002/verify \
  -H 'content-type: application/json' \
  -d '{"phone":"09778001234","region":1}'
```

---

## `GET /health`

健康检查。

```sh
$ curl http://54.179.197.66:8002/health
{"status":"ok"}
```

200 + `{"status":"ok"}` 表示进程在运行。**不**反映上游可达性、不消耗代理 IP 配额。

---

## 端到端 Python 示例

```python
import requests

BASE = "http://54.179.197.66:8002"
phone = "09778001234"

# 1. 触发发码
r = requests.post(f"{BASE}/send-code", json={"phone": phone}).json()
assert r["success"], r
trace_id = r["trace_id"]

# 2. 用户在手机上看到验证码并输入
code = input(f"请输入手机 {phone} 收到的验证码: ").strip()

# 3. 校验
r = requests.post(f"{BASE}/verify-code",
                  json={"trace_id": trace_id, "code": code}).json()

if r["success"]:
    print(f"OK, 已确认手机 {r['phone']}")
elif r["code"] == 7104:
    print(r["msg"])  # 例: "verification code mismatch (3 attempts left)"
elif r["code"] == -2:
    print("已过期，请重新发码")
elif r["code"] == -1:
    print("trace_id 无效，请重新发码")
elif r["code"] == -3:
    print("错误次数过多，trace_id 已锁死，请重新发码")
```

---

## 实现细节（仅供参考，调用方无需关心）

### 设备指纹
每个手机号在 DB 里维护一份独立的 `akmcchi`（设备身份），首次发码时调上游 `apparatus-make` 拿到 `osghu`，之后复用。

### 出口代理
每个手机号绑一个独立的 US 出口 IP（来自 novproxy.com），TTL 8 分钟。同一号 8 分钟内的请求走同一 IP；不同号各拿各的。代理死了自动 invalidate + 拿新 IP 重试 1 次。

### 调用顺序
`/send-code` 内部对上游打 3 次：
1. `apparatus-make`（仅首次发码该号时调）
2. `existence/verify-user-account`（每次必调，是上游的"号码热身"前置）
3. `text-user/transfer`（真正发码）

### 验证码本身
上游 `text-user/transfer` 响应的 `atkjtu.twwxfuya` 字段直接就是 4 位验证码（apk 客户端从不读这个字段，但服务端会发——本质是上游的实现"漏洞"）。我们持久化到 `verification_attempts` 表，`/verify-code` 据此本地比对。

---

## Changelog

| Version | Notes |
|---|---|
| 0.8.0 | TTL 10min→30min；新增 fail_count + `-3` "trace_id 锁死"状态 |
| 0.7.2 | 拆分 httpx timeout（connect=5s/read=15s），代理失败重试更快 |
| 0.7.1 | 代理连接失败 invalidate + rotate 重试 1 次 |
| 0.7.0 | 出口代理改为按手机号缓存；新增 `phone_devices.proxy_url` |
| 0.6.0 | 上游全局走 novproxy 代理 |
| 0.5.0 | `/verify-code` 改为 `(trace_id, code)`；新增 `verification_attempts` 表 |
| 0.4.0 | `/verify-code` 改为本地比对，去掉 register/clientSignUp |
| 0.3.0 | `/send-code` 前置 `existence/verify-user-account` 才能真发短信 |
| 0.2.0 | 上游 base_url 移到 `app_config` 表 |
| 0.1.0 | 初版：每号独立设备指纹 + apparatus-make bootstrap |
