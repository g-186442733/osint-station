#!/bin/bash
# OSINT 工作站 — crontab 安装脚本
#
# 用法:
#   bash scripts/setup_cron.sh          # 安装定时任务
#   bash scripts/setup_cron.sh remove   # 移除定时任务
#
# 默认调度: 每天 8:00 和 20:00 运行

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
RUN_SCRIPT="${SCRIPT_DIR}/run_daily.py"
LOG_FILE="${PROJECT_DIR}/data/cron.log"

CRON_TAG="# OSINT-STATION"
CRON_SCHEDULE="0 9,21 * * *"

# 确保日志目录存在
mkdir -p "$(dirname "$LOG_FILE")"

install_cron() {
    # 移除旧的（如果存在）
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true

    # 添加新的
    CRON_CMD="$CRON_SCHEDULE cd $PROJECT_DIR && $VENV_PYTHON $RUN_SCRIPT >> $LOG_FILE 2>&1 $CRON_TAG"

    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

    echo "✅ crontab 已安装:"
    echo "   调度: $CRON_SCHEDULE (每天 8:00, 20:00)"
    echo "   脚本: $RUN_SCRIPT"
    echo "   日志: $LOG_FILE"
    echo ""
    echo "当前 crontab:"
    crontab -l | grep "$CRON_TAG"
}

remove_cron() {
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true
    echo "✅ OSINT crontab 已移除"
}

case "${1:-install}" in
    install)
        install_cron
        ;;
    remove)
        remove_cron
        ;;
    *)
        echo "用法: $0 [install|remove]"
        exit 1
        ;;
esac
