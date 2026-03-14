"""
OSINT 工作站 — 推送模块
职责：将情报简报推送到企业微信群机器人
复用 TrendRadar 的 Webhook
"""
import json
import logging
import sqlite3
from datetime import datetime

import requests

from config.settings import DB_PATH, WECOM_WEBHOOK_URL

logger = logging.getLogger(__name__)


def push_to_wecom(content: str, msg_type: str = "markdown") -> bool:
    """
    推送到企业微信群机器人
    
    Args:
        content: Markdown 格式的消息内容
        msg_type: 消息类型 (markdown/text)
    
    Returns:
        是否推送成功
    """
    if not WECOM_WEBHOOK_URL:
        logger.error("企微 Webhook URL 未配置")
        return False

    payload = {
        "msgtype": msg_type,
        "markdown": {"content": content} if msg_type == "markdown" else None,
        "text": {"content": content} if msg_type == "text" else None,
    }
    # 移除 None 值
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        resp = requests.post(
            WECOM_WEBHOOK_URL,
            json=payload,
            timeout=10,
        )
        data = resp.json()

        if data.get("errcode") == 0:
            logger.info("企微推送成功")
            return True
        else:
            logger.error(f"企微推送失败: {data}")
            return False

    except Exception as e:
        logger.error(f"企微推送异常: {e}")
        return False


def push_daily_brief(brief: str) -> bool:
    """
    推送每日情报简报
    会在简报前加上标题，并记录推送状态
    """
    if not brief or brief.startswith("⚠️"):
        logger.warning("简报内容为空或生成失败，跳过推送")
        return False

    # 企微 Markdown 限制 4096 字符
    if len(brief) > 3800:
        brief = brief[:3800] + "\n\n... (内容过长已截断)"

    title = f"🔍 **OSINT 情报简报** | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    full_content = title + brief

    success = push_to_wecom(full_content)

    # 更新推送状态
    if success:
        db = sqlite3.connect(str(DB_PATH))
        db.execute("""
            UPDATE alerts
            SET pushed_to = 'wecom', pushed_at = ?
            WHERE id = (
                SELECT id FROM alerts
                WHERE alert_type = 'daily_brief' AND (pushed_to = '' OR pushed_to IS NULL)
                ORDER BY created_at DESC LIMIT 1
            )
        """, (datetime.now().isoformat(),))
        db.commit()
        db.close()

    return success


def push_trend_alert(topic_name: str, summary: str) -> bool:
    """推送趋势告警"""
    content = (
        f"⚡ **趋势告警** | {datetime.now().strftime('%H:%M')}\n\n"
        f"**话题**: {topic_name}\n\n"
        f"{summary}"
    )
    return push_to_wecom(content)
