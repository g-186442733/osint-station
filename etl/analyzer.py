"""
OSINT 工作站 — LLM 分析模块
职责：对采集到的帖子进行摘要、话题分类、趋势评分
使用 147AI API（OpenAI 兼容格式）
"""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from config.settings import DB_PATH, LLM_API_BASE, LLM_API_KEY, MODELS

logger = logging.getLogger(__name__)

# Fallback 链
MODEL_CHAIN = [
    MODELS["daily_summary"],  # gemini-2.0-flash
    MODELS["intelligence_brief"],  # gpt-4o-mini
    MODELS["fallback"],  # gpt-4.1-nano
]


def _call_llm(prompt: str, system: str = "", model: str | None = None) -> str:
    """调用 LLM API，带 fallback 链"""
    models = [model] if model else MODEL_CHAIN

    for m in models:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            resp = requests.post(
                f"{LLM_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": m,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.debug(f"LLM 调用成功: model={m}, tokens={data.get('usage', {})}")
            return content

        except Exception as e:
            logger.warning(f"LLM 调用失败 (model={m}): {e}")
            continue

    logger.error("所有模型调用均失败")
    return ""


def classify_posts(hours: int = 24) -> int:
    """
    对最近 N 小时内未分类的帖子进行话题分类
    返回分类的帖子数量
    """
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    # 获取未分类的帖子
    rows = db.execute(
        """
        SELECT id, platform, title, content, profile_slug
        FROM posts
        WHERE topic_id IS NULL AND collected_at > ?
        ORDER BY collected_at DESC
        LIMIT 50
    """,
        (cutoff,),
    ).fetchall()

    if not rows:
        logger.info("无需分类的新帖子")
        db.close()
        return 0

    # 获取已有话题
    existing_topics = db.execute(
        "SELECT id, name, category FROM topics WHERE post_count > 0 ORDER BY post_count DESC LIMIT 30"
    ).fetchall()
    topic_list = (
        [f"ID={t['id']}: {t['name']} ({t['category']})" for t in existing_topics]
        if existing_topics
        else []
    )

    classified = 0
    batch_size = 10

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        posts_text = "\n".join(
            [
                f"[帖子{p['id']}] [{p['platform']}] {p['profile_slug']}: "
                f"{p['title'] or ''} {(p['content'] or '')[:200]}"
                for p in batch
            ]
        )

        system = """你是一个情报分析助手。对每条帖子归入话题。

分类维度：business_opportunity（商业机会）、tech_trend（技术趋势）、industry_news（行业动态）、personal_update（个人动态）、gossip（八卦）、other（其他）。

关键规则：
1. 优先复用已有话题！只有当帖子内容确实不属于任何已有话题时，才创建新话题。
2. 话题名称要概括性强，如"AI模型发布动态"而非"GPT-5.4发布"。一个好话题应能覆盖多条相关帖子。
3. 同时评估情感倾向：positive/negative/neutral。
4. 【强制】话题名称必须使用中文！禁止使用英文话题名。例如用"AI代理技术"而非"AI Agent"，用"大语言模型训练"而非"LLM Training"。即使原文是英文，话题名也必须翻译为中文。"""

        existing_str = "\n".join(topic_list) if topic_list else "暂无已有话题"
        prompt = f"""已有话题列表（按热度排序，优先归入这些话题）：
{existing_str}

请对以下帖子分类，返回 JSON 数组：
[{{"post_id": 123, "topic_name": "话题名", "category": "tech_trend", "sentiment": "positive", "is_new_topic": false}}]

重要：尽量使用已有话题（is_new_topic=false），话题名必须和已有列表完全一致。只有确实无法归入时才创建新话题。

帖子列表：
{posts_text}"""

        result = _call_llm(prompt, system)
        if not result:
            continue

        try:
            # 提取 JSON（处理 markdown code block）
            cleaned = result.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]

            classifications = json.loads(cleaned)

            for cls in classifications:
                post_id = cls.get("post_id")
                topic_name = cls.get("topic_name", "未分类")
                category = cls.get("category", "other")
                sentiment = cls.get("sentiment", "neutral")
                is_new = cls.get("is_new_topic", True)

                # 创建或查找话题
                if is_new:
                    db.execute(
                        """
                        INSERT OR IGNORE INTO topics (name, category, first_seen, last_updated)
                        VALUES (?, ?, ?, ?)
                    """,
                        (
                            topic_name,
                            category,
                            datetime.now().isoformat(),
                            datetime.now().isoformat(),
                        ),
                    )
                    db.commit()

                topic_row = db.execute(
                    "SELECT id FROM topics WHERE name = ?", (topic_name,)
                ).fetchone()
                topic_id = topic_row["id"] if topic_row else None

                # 更新帖子
                db.execute(
                    """
                    UPDATE posts SET topic_id = ?, sentiment = ? WHERE id = ?
                """,
                    (topic_id, sentiment, post_id),
                )
                classified += 1

                # 更新话题帖子计数
                if topic_id:
                    db.execute(
                        """
                        UPDATE topics SET post_count = (
                            SELECT COUNT(*) FROM posts WHERE topic_id = ?
                        ), last_updated = ? WHERE id = ?
                    """,
                        (topic_id, datetime.now().isoformat(), topic_id),
                    )

            db.commit()

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"分类结果解析失败: {e}\n原始输出: {result[:500]}")
            continue

    db.close()
    logger.info(f"话题分类完成，共分类 {classified} 条帖子")
    return classified


def update_trend_scores() -> int:
    """
    计算所有话题的 trend_score。
    公式: trend_score = recent_count * (1 + growth_rate) * (1 + log(avg_engagement + 1))
      - recent_count: 最近7天帖子数
      - growth_rate: 最近7天 vs 前7天的增长率
      - avg_engagement: 平均互动量(likes+comments)
    返回更新的话题数量。
    """
    import math

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).isoformat()
    two_weeks_ago = (now - timedelta(days=14)).isoformat()

    topics = db.execute("SELECT id, name FROM topics").fetchall()
    updated = 0

    for topic in topics:
        tid = topic["id"]

        recent = db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(AVG(engagement_likes + engagement_comments), 0) as avg_eng "
            "FROM posts WHERE topic_id = ? AND collected_at > ?",
            (tid, week_ago),
        ).fetchone()

        previous = db.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE topic_id = ? AND collected_at > ? AND collected_at <= ?",
            (tid, two_weeks_ago, week_ago),
        ).fetchone()

        recent_count = recent["cnt"] or 0
        avg_eng = recent["avg_eng"] or 0
        prev_count = previous["cnt"] or 0

        if prev_count > 0:
            growth_rate = (recent_count - prev_count) / prev_count
        elif recent_count > 0:
            growth_rate = 1.0
        else:
            growth_rate = 0.0

        growth_rate = max(growth_rate, -0.9)

        score = recent_count * (1 + growth_rate) * (1 + math.log(avg_eng + 1))
        score = round(score, 2)

        db.execute(
            "UPDATE topics SET trend_score = ?, last_updated = ? WHERE id = ?",
            (score, now.isoformat(), tid),
        )
        updated += 1

    db.commit()
    db.close()
    logger.info(f"趋势评分更新完成，共更新 {updated} 个话题")
    return updated


def generate_daily_brief(hours: int = 24) -> str:
    """
    生成每日情报简报
    返回 Markdown 格式的简报文本
    """
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    # 获取最近的帖子
    posts = db.execute(
        """
        SELECT p.*, t.name as topic_name, t.category as topic_category
        FROM posts p
        LEFT JOIN topics t ON p.topic_id = t.id
        WHERE p.collected_at > ?
        ORDER BY p.engagement_views DESC
        LIMIT 30
    """,
        (cutoff,),
    ).fetchall()

    if not posts:
        db.close()
        return "📊 过去24小时无新内容"

    # 按人物分组
    by_person: dict[str, list] = {}
    for p in posts:
        slug = p["profile_slug"] or "unknown"
        by_person.setdefault(slug, []).append(p)

    # 获取人物信息
    profiles = {
        row["slug"]: row["name"]
        for row in db.execute("SELECT slug, name FROM profiles").fetchall()
    }

    # 构造 prompt
    posts_summary = []
    for slug, person_posts in by_person.items():
        name = profiles.get(slug, slug)
        for p in person_posts[:5]:
            content_preview = (p["content"] or "")[:300]
            posts_summary.append(
                f"- [{name}] [{p['platform']}] {p['title'] or ''}\n"
                f"  内容: {content_preview}\n"
                f"  互动: 👁{p['engagement_views']} ❤️{p['engagement_likes']} "
                f"💬{p['engagement_comments']}\n"
                f"  话题: {p['topic_name'] or '未分类'} ({p['topic_category'] or '-'})\n"
                f"  情感: {p['sentiment'] or '-'}"
            )

    system = """你是一个专业的情报分析师。根据社交媒体采集数据生成简洁有洞察的每日简报。
简报格式：
1. 📌 今日要点（3-5个关键发现）
2. 🔥 趋势观察（正在升温的话题）
3. 💡 商业机会信号（如有）
4. 📊 人物动态摘要（每人1-2句话）
使用 Markdown 格式，简洁有力，不要废话。"""

    prompt = f"以下是过去{hours}小时内采集到的社交媒体数据：\n\n" + "\n".join(
        posts_summary
    )

    brief = _call_llm(prompt, system)

    if brief:
        # 存储告警记录
        db.execute(
            """
            INSERT INTO alerts (alert_type, title, content, pushed_to, created_at)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                "daily_brief",
                f"每日情报简报 {datetime.now().strftime('%Y-%m-%d')}",
                brief,
                "",
                datetime.now().isoformat(),
            ),
        )
        db.commit()

    db.close()
    return brief or "⚠️ 简报生成失败"


def translate_titles_to_zh(batch_size: int = 20, limit: int = 500) -> int:
    """批量翻译英文标题为中文，每次发送 batch_size 条给 LLM"""
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")

    rows = db.execute(
        """SELECT id, title, substr(content, 1, 120) as preview, platform
           FROM posts
           WHERE title_zh IS NULL
             AND title IS NOT NULL AND title != ''
           ORDER BY COALESCE(NULLIF(published_at, ''), collected_at) DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    if not rows:
        db.close()
        logger.info("没有需要翻译的标题")
        return 0

    def _is_chinese(text: str) -> bool:
        return sum(1 for c in text if "\u4e00" <= c <= "\u9fff") > len(text) * 0.3

    skip_count = 0
    for row_id, title, _, _ in rows:
        if _is_chinese(title):
            db.execute("UPDATE posts SET title_zh = ? WHERE id = ?", (title, row_id))
            skip_count += 1

    if skip_count:
        db.commit()
        logger.info(f"跳过 {skip_count} 条已是中文的标题")

    en_rows = [(r[0], r[1], r[2], r[3]) for r in rows if not _is_chinese(r[1])]

    translated = 0
    for i in range(0, len(en_rows), batch_size):
        batch = en_rows[i : i + batch_size]
        numbered = "\n".join(
            f"{idx + 1}. [{r[3]}] {r[1]}" for idx, r in enumerate(batch)
        )

        prompt = f"""将以下英文标题翻译为简洁的中文。保持原意，不要添加额外信息。
每行输出格式：序号. 中文翻译
不要输出其他内容。

{numbered}"""

        try:
            result = _call_llm(
                prompt, system="你是一个专业翻译，将英文标题翻译为简洁准确的中文。"
            )
            lines = [l.strip() for l in result.strip().split("\n") if l.strip()]

            translations = {}
            for line in lines:
                parts = line.split(".", 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    idx = int(parts[0].strip()) - 1
                    zh = parts[1].strip().lstrip("．。 ")
                    if 0 <= idx < len(batch):
                        translations[idx] = zh

            for idx, row in enumerate(batch):
                zh = translations.get(idx)
                if zh:
                    db.execute(
                        "UPDATE posts SET title_zh = ? WHERE id = ?", (zh, row[0])
                    )
                    translated += 1

            db.commit()
            logger.info(
                f"翻译批次 {i // batch_size + 1}: {len(translations)}/{len(batch)} 条成功"
            )

        except Exception as e:
            logger.error(f"翻译批次 {i // batch_size + 1} 失败: {e}")

    db.close()
    logger.info(f"翻译完成: {translated} 条英文标题已翻译, {skip_count} 条中文跳过")
    return translated + skip_count
