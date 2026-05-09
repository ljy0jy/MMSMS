# mmsms-proxy

FastAPI 中转层，封装 `com.easy.bayarsantai` 的账号核验、发短信验证码、注册三个接口。
传手机号过来，这边自动**为每个号码生成独立设备指纹**、加密 / 调上游 / 解密响应。

## 特性

- 每个手机号在 MySQL 里维护一份独立设备指纹（`android_id` / GAID / `osghu`）
- 首次出现的手机号会调上游 `user/construct/apparatus-make` 拿到与该指纹绑定的 `osghu`
- `send-code` 与 `verify-code` 复用同一台虚拟设备，重启服务后也不丢

## 安装

```sh
cd proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填上 DATABASE_URL
```

数据库本身（默认库名 `mmsms`）需要预先建好；表 `phone_devices` 在服务首次启动时自动 `CREATE TABLE IF NOT EXISTS`。

```sql
-- 一次性手动跑：
CREATE DATABASE mmsms DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
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

### `POST /verify-code` — 本地校验验证码
**纯本地比对**，不调用上游、不会真注册账号。/send-code 时上游返回的 `twwxfuya` 已经写到 `phone_devices.last_code`，这里直接对比。

```sh
curl -X POST http://127.0.0.1:8000/verify-code \
  -H 'content-type: application/json' \
  -d '{"phone":"0902095885","code":"1234"}'
```

返回字段：
- `code`：`0` 匹配；`7104` 不匹配；`-1` 该号从未调过 send-code；`-2` 验证码超过 10 分钟 TTL
- `msg` / `success` 同字面量
- 不返回 `raw`（不打上游）

发码端的返回字段：
- `code` 上游 `wjmgawm`，`0` 即成功
- `msg`  上游 `yftkram` 原文
- `success` 即 `code == 0`
- `raw` 上游解密后的完整响应（验证码在 `raw.atkjtu.twwxfuya`）

## 文件

| 文件 | 作用 |
|---|---|
| `app/codec.py`    | `ky_data` 字段的 AES/CBC/PKCS5 加解密（key/iv 都是 apk 里硬编的） |
| `app/config.py`   | 上游 URL / 静态字段 / DB URL；从 `.env` 读取 |
| `app/db.py`       | SQLAlchemy async + `phone_devices` 模型 |
| `app/devices.py`  | 每个手机号生成 / bootstrap / 持久化设备指纹 |
| `app/upstream.py` | 把 plaintext 包成完整 envelope，调上游 |
| `app/main.py`     | FastAPI 入口 |

## 注意

- `.env` 已在 `.gitignore` 里，**生产凭据不要往仓库里丢**。
- 上游 URL 存在 `app_config.upstream_base_url`，**换 TLD 不需要重启**：
  ```sql
  UPDATE app_config SET config_value='https://eworr.<新TLD>/streamservice' WHERE config_key='upstream_base_url';
  ```
  `.env` 里的 `UPSTREAM_BASE_URL` 仅作为 seed/fallback——首次启动时如果 `app_config` 里没行就用这个值播种。
- 静态机型暂时锁定 Xiaomi MIX 2S / Android 10。需要按机型池随机时改 `app/devices.py`。
