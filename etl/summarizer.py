"""LLM 摘要层 — 147AI API 调用，话题分类与情报摘要"""
import json
import logging
from datetime import datetime

import requests

from config.settings import LLM_API_BASE, LLM_API_KEY, MODELS

logger = logging.getLogger(__name__)


class Summarizer:

    def __init__(self):
        self.api_base = LLM_API_BASE
        self.api_key = LLM_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _call_llm(self, prompt: str, model: str | None = None, max_tokens: int = 2000) -> str:
        model = model or MODELS["daily_summary"]

        if not self.api_key:
            logger.warning("未配置 LLM API Key，跳过 LLM 调用")
            return ""

        try:
            resp = self.session.post(
                f"{self.api_base}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.RequestException as e:
            logger.error(f"LLM 调用失败 ({model}): {e}")
            # fallback 到备选模型
            if model != MODELS["fallback"]:
                logger.info(f"尝试 fallback 模型: {MODELS['fallback']}")
                return self._call_llm(prompt, model=MODELS["fallback"], max_tokens=max_tokens)
            return ""

    def classify_posts(self, posts: list[dict]) -> list[dict]:
        """
        对帖子进行话题分类

        Returns:
            每条帖子附加 topic_name 和 category 字段
        """
        if not posts:
            return posts

        # 构造分类 prompt
        post_texts = []
        for i, p in enumerate(posts[:30]):  # 限制30条防止 token 溢出
            text = p.get("title") or p.get("content", "")
            post_texts.append(f"{i+1}. [{p.get('platform')}] {text[:100]}")

        prompt = f"""你是一个信息分类专家。请将以下内容分类到这些类别之一：
- business_opportunity（商业机会）
- tech_trend（技术趋势）
- industry_news（行业新闻）
- personal_update（个人动态）
- opinion（观点评论）
- other（其他）

内容列表：
{chr(10).join(post_texts)}

请用 JSON 数组返回，格式：[{{"index": 1, "category": "tech_trend", "topic": "AI Agent"}}]
只返回 JSON，不要其他文字。"""

        result = self._call_llm(prompt, model=MODELS["daily_summary"])
        if not result:
            return posts

        try:
            # 清理 LLM 输出中的 markdown 标记
            clean = result.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
            classifications = json.loads(clean)

            for item in classifications:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(posts):
                    posts[idx]["_topic_name"] = item.get("topic", "")
                    posts[idx]["_category"] = item.get("category", "other")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"分类结果解析失败: {e}")

        return posts

    def generate_daily_brief(self, posts: list[dict], date: str | None = None) -> str:
        """生成每日情报简报（Markdown 格式）"""
        date = date or datetime.now().strftime("%Y-%m-%d")

        if not posts:
            return f"# 每日情报简报 — {date}\n\n暂无新数据。"

        # 按平台分组统计
        by_platform: dict[str, list] = {}
        for p in posts:
            by_platform.setdefault(p.get("platform", "unknown"), []).append(p)

        stats = ", ".join(f"{k}: {len(v)}条" for k, v in by_platform.items())

        # 取前20条内容构造 prompt
        content_lines = []
        for p in posts[:20]:
            text = p.get("title") or p.get("content", "")
            content_lines.append(
                f"- [{p.get('platform')}] {text[:80]} "
                f"(👍{p.get('engagement_likes', 0)} 💬{p.get('engagement_comments', 0)})"
            )

        prompt = f"""你是一个 OSINT 情报分析师。基于以下今日采集数据，生成一份简短的每日情报简报。

日期：{date}
数据概况：{stats}

内容摘要：
{chr(10).join(content_lines)}

要求：
1. 用 Markdown 格式
2. 包含：核心发现（3-5条）、趋势观察、值得关注的异动
3. 简洁有力，每条发现不超过2句话
4. 如果数据中有明显的商业机会信号，单独标注"""

        brief = self._call_llm(prompt, model=MODELS["intelligence_brief"], max_tokens=3000)

        if brief:
            return f"# 每日情报简报 — {date}\n\n{brief}"
        else:
            # LLM 不可用时的纯统计 fallback
            return self._fallback_brief(posts, date, stats)

    def _fallback_brief(self, posts: list[dict], date: str, stats: str) -> str:
        lines = [f"# 每日情报简报 — {date}", "", f"**数据概况**: {stats}", ""]

        # 按互动量排序取 top 5
        sorted_posts = sorted(
            posts,
            key=lambda p: p.get("engagement_likes", 0) + p.get("engagement_comments", 0),
            reverse=True,
        )[:5]

        lines.append("## 热门内容 Top 5")
        for i, p in enumerate(sorted_posts, 1):
            text = p.get("title") or p.get("content", "")[:60]
            lines.append(
                f"{i}. [{p.get('platform')}] {text} "
                f"(👍{p.get('engagement_likes', 0)})"
            )

        return "\n".join(lines)
