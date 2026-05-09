#!/bin/bash
# MMSMS 宝塔 Webhook 一键部署脚本
#
# 用法: ./deploy.sh
#
# 服务器上一次性准备：
#   1. 在 /www/wwwroot/MMSMS 克隆好仓库（git@github.com:ljy0jy/MMSMS.git）
#   2. cp .env.example .env.prod && vi .env.prod  # 填生产 DATABASE_URL
#   3. 在 MySQL 上 CREATE DATABASE mmsms DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
#   4.（可选）在宝塔 Supervisor 里建 mmsms-prod 任务，运行命令同下方 nohup 的 uvicorn 行
#
# 宝塔 Webhook URL 占位（在宝塔 → 软件商店 → Webhook 里建好这个 hook 后回填）：
#   生产: https://<server>:<port>/hook?access_key=<token>

set -e

# ── 固定参数（只跑 prod） ────────────────────────────────────────────
ENV_FILE=".env.prod"
PORT=8002
WORKERS=4
SUPERVISOR_NAME="mmsms-prod"

# ── 配置区（根据实际情况修改） ────────────────────────────────────────
PROJECT_DIR="/www/wwwroot/MMSMS"
LOG_FILE="/var/log/mmsms_deploy.log"
BRANCH="main"
PYTHON="/www/server/pyporject_evn/versions/3.13.12/bin/python3"
PIP="/www/server/pyporject_evn/versions/3.13.12/bin/pip3"
UVICORN="/www/server/pyporject_evn/versions/3.13.12/bin/uvicorn"
# ─────────────────────────────────────────────────────────────────────

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [prod] $1" | tee -a "$LOG_FILE"
}

log "===== 开始部署 MMSMS (prod) ====="
log "ENV_FILE=$ENV_FILE  PORT=$PORT  WORKERS=$WORKERS"

# 1. 拉取最新代码
log "[1/5] 拉取代码..."
cd "$PROJECT_DIR"
git fetch origin
git reset --hard origin/$BRANCH
log "当前版本: $(git log -1 --oneline)"

# 2. 安装/更新依赖
log "[2/5] 安装依赖..."
$PIP install -r requirements.txt -q

# 3. 检查 env 文件是否存在
if [ ! -f "$ENV_FILE" ]; then
    log "错误: $ENV_FILE 不存在，请先 cp .env.example .env.prod && vi .env.prod"
    exit 1
fi

# 4. 重启服务（phone_devices 表会在启动时通过 SQLAlchemy 自动 create_all）
log "[3/5] 重启服务..."

if command -v supervisorctl &>/dev/null && supervisorctl status "$SUPERVISOR_NAME" &>/dev/null; then
    log "使用 Supervisor 重启 $SUPERVISOR_NAME"
    supervisorctl restart "$SUPERVISOR_NAME"
else
    log "使用 nohup 方式重启（端口 $PORT）"
    pkill -f "uvicorn app.main:app.*--port $PORT" || true
    sleep 1
    nohup env ENV_FILE="$ENV_FILE" $UVICORN app.main:app \
        --host 0.0.0.0 \
        --port $PORT \
        --workers $WORKERS \
        >> "$LOG_FILE" 2>&1 &
    log "uvicorn 后台启动完成 (PID: $!, PORT: $PORT)"
fi

# 5. 健康检查
log "[4/5] 健康检查..."
sleep 5
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:$PORT/health || echo "000")
if [ "$HTTP_CODE" == "200" ]; then
    log "[5/5] 健康检查通过 (HTTP $HTTP_CODE)"
else
    log "[5/5] 健康检查失败 (HTTP $HTTP_CODE)，请查看日志 $LOG_FILE"
    exit 1
fi

log "===== 部署完成 (prod @ port $PORT) ====="
