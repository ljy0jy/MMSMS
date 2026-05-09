# trustkyat-proxy

FastAPI 中转层，封装 com.easy.bayarsantai (TrustKyat) 的 `existence/verify-user-account` 接口。
传手机号过来，这边自动加密 / 加设备指纹 / 调上游 / 解密响应。

## 安装

```sh
cd proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 启动

```sh
uvicorn app.main:app --reload --port 8000
```

## 接口

### `POST /verify` — 查手机号是否已注册
```sh
curl -X POST http://127.0.0.1:8000/verify \
  -H 'content-type: application/json' \
  -d '{"phone":"098512515","region":1}'
```

### `POST /send-code` — 发短信验证码
```sh
curl -X POST http://127.0.0.1:8000/send-code \
  -H 'content-type: application/json' \
  -d '{"phone":"0902095885"}'
```

### `POST /verify-code` — 校验验证码（同时完成注册）
```sh
curl -X POST http://127.0.0.1:8000/verify-code \
  -H 'content-type: application/json' \
  -d '{"phone":"0902095885","code":"1234","password":"abcd1234"}'
```

返回字段：
- `code` 上游 `wjmgawm`，`0` 即成功，`7104` 是「验证码错误」
- `msg`  上游 `yftkram` 原文
- `success` 即 `code == 0`
- `raw` 上游解密后的完整响应

## 文件

| 文件 | 作用 |
|---|---|
| `app/codec.py`    | `ky_data` 字段的 AES/CBC/PKCS5 加解密（key/iv 都是 apk 里硬编的） |
| `app/config.py`   | 上游 URL、UA、设备指纹常量；可通过环境变量 `UPSTREAM_BASE_URL` 覆盖域名 |
| `app/upstream.py` | 把 plaintext 包成完整 envelope（`vhhwl` + `dcvnq` + `vswyd` + `akmcchi`），调上游 |
| `app/main.py`     | FastAPI 入口，单 `POST /verify` 接口 |

## 注意

- `app/config.py` 里的 `DEVICE` 块是从一台真实 Xiaomi MIX 2S 抓包得到的。
  如果服务端开始拒，把这个块换成另一台机器抓的就行（GAID、`osghu`、`lpyblk`、`mthwtv` 都是设备相关的）。
- 上游域名 `eworr.<TLD>` 的 TLD 由 app 启动时调 `platform/service/usage` 动态拿，
  目前看到的是 `onetooutlimitss.com`，如果换了就改 `BASE_URL` 或设环境变量。
