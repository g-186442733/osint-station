"""
企微/飞书推送 — 复用 TrendRadar 的 webhook 模式
支持 Markdown 格式推送到企业微信群机器人
"""
import json
import logging
import requests
from typing import Optional

from config.settings import WECOM_WEBHOOK_URL

logger = logging.getLogger(__name__)

# 企微单条消息 Markdown 最大 4096 字节
WECOM_MAX_BYTES = 4000


def send_wecom_markdown(content: str, webhook_url: str | None = None) -> bool:
    """
    推送 Markdown 格式消息到企业微信群机器人

    Args:
        content: Markdown 格式的消息正文
        webhook_url: Webhook URL（默认用 settings 里的配置）

    Returns:
        是否发送成功
    """
    url = webhook_url or WECOM_WEBHOOK_URL
    if not url:
        logger.error("未配置企微 Webhook URL (WECOM_WEBHOOK_URL)")
        return False

    # 分批发送（企微限制 4096 字节）
    batches = _split_markdown(content, WECOM_MAX_BYTES)

    success = True
    for i, batch in enumerate(batches):
        if len(batches) > 1:
            header = f"**[{i + 1}/{len(batches)}]**\n\n"
            batch = header + batch

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": batch},
        }

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()
            if data.get("errcode") != 0:
                logger.error(f"企微推送失败 [{i + 1}/{len(batches)}]: {data}")
                success = False
            else:
                logger.info(f"企微推送成功 [{i + 1}/{len(batches)}]")
        except Exception as e:
            logger.error(f"企微推送异常: {e}")
            success = False

    return success


def send_wecom_text(content: str, webhook_url: str | None = None) -> bool:
    """推送纯文本消息到企微"""
    url = webhook_url or WECOM_WEBHOOK_URL
    if not url:
        logger.error("未配置企微 Webhook URL")
        return False

    payload = {
        "msgtype": "text",
        "text": {"content": content[:2048]},
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.error(f"企微文本推送失败: {data}")
            return False
        logger.info("企微文本推送成功")
        return True
    except Exception as e:
        logger.error(f"企微文本推送异常: {e}")
        return False


def _split_markdown(content: str, max_bytes: int = WECOM_MAX_BYTES) -> list[str]:
    """
    按字节数切分 Markdown 内容，尽量在段落边界切割

    Args:
        content: 完整 Markdown 内容
        max_bytes: 每批最大字节数

    Returns:
        切分后的内容列表
    """
    if len(content.encode("utf-8")) <= max_bytes:
        return [content]

    batches = []
    lines = content.split("\n")
    current = []
    current_size = 0

    for line in lines:
        line_bytes = len((line + "\n").encode("utf-8"))

        if current_size + line_bytes > max_bytes and current:
            batches.append("\n".join(current))
            current = []
            current_size = 0

        current.append(line)
        current_size += line_bytes

    if current:
        batches.append("\n".join(current))

    return batches


def push_intelligence_brief(
    title: str,
    content: str,
    webhook_url: str | None = None,
) -> bool:
    """
    推送情报简报到企微

    Args:
        title: 简报标题
        content: 简报 Markdown 正文
        webhook_url: 可选的 webhook URL

    Returns:
        是否成功
    """
    full_content = f"# {title}\n\n{content}"
    return send_wecom_markdown(full_content, webhook_url)
