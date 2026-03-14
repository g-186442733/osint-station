"""
人物情报分析引擎

输入人名/用户名 → 输出:
  1. 跨平台身份 (Maigret + 已知数据)
  2. 影响力指标 (粉丝、互动率、跨平台覆盖)
  3. 关系网络 (@提及、转发、互动对象)
  4. 核心观点/立场 (LLM提炼)
  5. 活跃度时间线
"""

import re
import json
import sqlite3
import logging
import asyncio
from datetime import datetime
from collections import Counter
from pathlib import Path

import requests

from config.settings import DB_PATH, LLM_API_BASE, LLM_API_KEY, MODELS

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"@(\w{1,30})")
RT_PATTERN = re.compile(r"^RT @(\w+):")


def _call_llm(prompt: str, system: str = "") -> str:
    models = [MODELS["daily_summary"], MODELS["fallback"]]
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
                    "max_tokens": 3000,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"LLM call failed (model={m}): {e}")
    return ""


def get_identity_map(slug: str, db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT platform, platform_username, platform_url, confidence, source FROM identity_mappings WHERE profile_slug = ?",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_influence_stats(slug: str, db: sqlite3.Connection) -> dict:
    posts = db.execute(
        "SELECT platform, engagement_likes, engagement_comments, engagement_shares, engagement_views FROM posts WHERE profile_slug = ?",
        (slug,),
    ).fetchall()

    if not posts:
        return {
            "total_posts": 0,
            "platforms": [],
            "avg_likes": 0,
            "avg_views": 0,
            "avg_comments": 0,
        }

    platforms = list(set(p["platform"] for p in posts))
    total_likes = sum(p["engagement_likes"] or 0 for p in posts)
    total_views = sum(p["engagement_views"] or 0 for p in posts)
    total_comments = sum(p["engagement_comments"] or 0 for p in posts)
    n = len(posts)

    by_platform = {}
    for p in posts:
        plat = p["platform"]
        if plat not in by_platform:
            by_platform[plat] = {"count": 0, "likes": 0, "views": 0, "comments": 0}
        by_platform[plat]["count"] += 1
        by_platform[plat]["likes"] += p["engagement_likes"] or 0
        by_platform[plat]["views"] += p["engagement_views"] or 0
        by_platform[plat]["comments"] += p["engagement_comments"] or 0

    return {
        "total_posts": n,
        "platforms": platforms,
        "platform_count": len(platforms),
        "avg_likes": round(total_likes / n, 1),
        "avg_views": round(total_views / n, 1),
        "avg_comments": round(total_comments / n, 1),
        "total_likes": total_likes,
        "total_views": total_views,
        "by_platform": by_platform,
    }


def extract_relationships(slug: str, db: sqlite3.Connection) -> dict:
    posts = db.execute(
        "SELECT platform, content, raw_json FROM posts WHERE profile_slug = ?",
        (slug,),
    ).fetchall()

    mentions = Counter()
    retweets = Counter()
    interactions = Counter()

    for p in posts:
        text = p["content"] or ""

        rt = RT_PATTERN.match(text)
        if rt:
            target = rt.group(1).lower()
            retweets[target] += 1
            interactions[target] += 1

        for m in MENTION_PATTERN.findall(text):
            m_lower = m.lower()
            mentions[m_lower] += 1
            interactions[m_lower] += 1

        raw = p["raw_json"] or ""
        if raw:
            try:
                raw_data = json.loads(raw)
                retweeted_by = raw_data.get("retweeted_status", {})
                if isinstance(retweeted_by, dict):
                    rt_user = retweeted_by.get("user", {})
                    if isinstance(rt_user, dict) and rt_user.get("screen_name"):
                        name = rt_user["screen_name"].lower()
                        retweets[name] += 1
                        interactions[name] += 1
            except (json.JSONDecodeError, AttributeError):
                pass

    return {
        "mentions": dict(mentions.most_common(20)),
        "retweets": dict(retweets.most_common(20)),
        "top_interactions": dict(interactions.most_common(20)),
        "unique_contacts": len(interactions),
    }


def extract_viewpoints(slug: str, db: sqlite3.Connection) -> str:
    posts = db.execute(
        "SELECT platform, title, content, published_at, engagement_likes FROM posts WHERE profile_slug = ? ORDER BY published_at DESC LIMIT 30",
        (slug,),
    ).fetchall()

    if not posts:
        return "暂无内容数据"

    name_row = db.execute(
        "SELECT name FROM profiles WHERE slug = ?", (slug,)
    ).fetchone()
    display_name = name_row["name"] if name_row else slug

    posts_text = "\n".join(
        [
            f"[{p['platform']}] {p['published_at'] or '?'} | {p['title'] or ''} {(p['content'] or '')[:200]}"
            for p in posts
        ]
    )

    system = f"""你是一个情报分析师。分析 {display_name} 的社交媒体内容，提取以下信息：

1. 核心关注领域（他最关心什么话题，列出3-5个）
2. 主要观点/立场（他对AI/科技/行业的看法是什么）
3. 近期动态摘要（最近在做什么、发布什么）
4. 值得注意的信号（任何可能有商业价值或重要趋势的内容）

输出格式用 Markdown，简洁有力，中文。"""

    prompt = f"以下是 {display_name} 最近的社交媒体内容（共{len(posts)}条）：\n\n{posts_text}"
    return _call_llm(prompt, system) or "分析失败"


def get_activity_timeline(slug: str, db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT platform, published_at, COALESCE(title_zh, title) as title, content, engagement_likes, engagement_views, url FROM posts WHERE profile_slug = ? AND published_at != '' ORDER BY published_at DESC LIMIT 50",
        (slug,),
    ).fetchall()
    return [dict(r) for r in rows]


def _lightweight_analysis(name: str, slug: str, db: sqlite3.Connection) -> dict:
    patterns = [f"%@{name}%", f"%@{slug}%", f"%{name}%"]
    mention_posts = []
    seen_ids = set()
    for pat in patterns:
        rows = db.execute(
            "SELECT id, profile_slug, platform, title, COALESCE(title_zh, title) as title_display, "
            "content, published_at, engagement_likes, engagement_views, url "
            "FROM posts WHERE content LIKE ? ORDER BY published_at DESC LIMIT 30",
            (pat,),
        ).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                mention_posts.append(dict(r))

    mentioned_by = Counter()
    platforms_seen = set()
    for p in mention_posts:
        mentioned_by[p["profile_slug"]] += 1
        platforms_seen.add(p["platform"])

    mentioners = []
    for mentioner_slug, count in mentioned_by.most_common(10):
        row = db.execute(
            "SELECT name, bio FROM profiles WHERE slug = ?", (mentioner_slug,)
        ).fetchone()
        mentioners.append(
            {
                "slug": mentioner_slug,
                "name": row["name"] if row else mentioner_slug,
                "count": count,
            }
        )

    context_posts = mention_posts[:10]
    timeline = [
        {
            "platform": p["platform"],
            "published_at": p["published_at"],
            "title": p["title_display"] or (p["content"] or "")[:80],
            "content": p["content"],
            "engagement_likes": p["engagement_likes"],
            "engagement_views": p["engagement_views"],
            "url": p["url"],
            "source": p["profile_slug"],
        }
        for p in context_posts
    ]

    bio = ""
    if mention_posts:
        sample_text = "\n".join(
            f"[{p['platform']}] by @{p['profile_slug']}: {(p['content'] or '')[:200]}"
            for p in mention_posts[:15]
        )
        system = (
            f"根据以下社交媒体中对 {name} 的提及内容，用中文写一段50-80字的简介，"
            "描述此人在AI/科技圈的角色和影响力。只输出简介文字。如果信息不足就写'暂无足够信息生成简介'。"
        )
        bio = _call_llm(sample_text, system)
        if bio:
            bio = bio.strip().strip('"').strip("'").strip("「").strip("」")

    return {
        "name": name,
        "slug": slug,
        "bio": bio or f"在监控数据中被提及 {len(mention_posts)} 次",
        "tags": "[]",
        "priority": "none",
        "lightweight": True,
        "mention_count": len(mention_posts),
        "mentioned_by": mentioners,
        "platforms_seen": list(platforms_seen),
        "identity": [],
        "influence": {
            "total_posts": 0,
            "platforms": [],
            "platform_count": 0,
            "avg_likes": 0,
            "avg_views": 0,
            "avg_comments": 0,
            "by_platform": {},
        },
        "relationships": {
            "mentions": {},
            "retweets": {},
            "top_interactions": {},
            "unique_contacts": 0,
        },
        "viewpoints": "",
        "timeline": timeline,
        "generated_at": datetime.now().isoformat(),
    }


def generate_bio(name: str, slug: str, db: sqlite3.Connection) -> str:
    """根据采集的帖子内容，用LLM生成人物简介并保存到DB"""
    posts = db.execute(
        "SELECT platform, title, content FROM posts WHERE profile_slug = ? ORDER BY published_at DESC LIMIT 20",
        (slug,),
    ).fetchall()

    if not posts:
        return ""

    posts_text = "\n".join(
        [
            f"[{p['platform']}] {p['title'] or ''} {(p['content'] or '')[:150]}"
            for p in posts
        ]
    )

    system = "你是一个简洁的人物资料撰写专家。根据提供的社交媒体内容，生成一段50-100字的中文人物简介。只输出简介文字，不要标题、引号或其他格式。"
    prompt = f"以下是 {name} 的社交媒体内容，请据此生成简介：\n\n{posts_text}"

    bio = _call_llm(prompt, system)
    if bio:
        bio = bio.strip().strip('"').strip("'").strip("「").strip("」")
        try:
            db.execute("UPDATE profiles SET bio = ? WHERE slug = ?", (bio, slug))
            db.commit()
            logger.info(f"[intel] 已生成并保存 {name} 的简介 ({len(bio)} 字)")
        except Exception as e:
            logger.warning(f"[intel] 保存简介失败: {e}")
    return bio or ""


def run_profile_intel(name_or_slug: str) -> dict:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    slug = name_or_slug.lower().replace(" ", "_")

    profile = db.execute(
        "SELECT * FROM profiles WHERE slug = ? OR name = ?", (slug, name_or_slug)
    ).fetchone()
    if not profile:
        all_slugs = [
            r[0]
            for r in db.execute("SELECT DISTINCT profile_slug FROM posts").fetchall()
        ]
        matched = [s for s in all_slugs if name_or_slug.lower() in s.lower()]
        if matched:
            slug = matched[0]
            profile = db.execute(
                "SELECT * FROM profiles WHERE slug = ?", (slug,)
            ).fetchone()

    if not profile:
        # Lightweight analysis: search mentions across all posts
        result = _lightweight_analysis(name_or_slug, slug, db)
        db.close()
        return result

    slug = profile["slug"]
    display_name = profile["name"]
    bio = profile["bio"] or ""

    logger.info(f"[intel] 开始分析: {display_name} (slug={slug})")

    if not bio:
        bio = generate_bio(display_name, slug, db)

    identity = get_identity_map(slug, db)
    influence = get_influence_stats(slug, db)
    relationships = extract_relationships(slug, db)
    viewpoints = extract_viewpoints(slug, db)
    timeline = get_activity_timeline(slug, db)

    db.close()

    return {
        "name": display_name,
        "slug": slug,
        "bio": bio,
        "tags": profile["tags"] or "[]",
        "priority": profile["priority"],
        "identity": identity,
        "influence": influence,
        "relationships": relationships,
        "viewpoints": viewpoints,
        "timeline": timeline,
        "generated_at": datetime.now().isoformat(),
    }
