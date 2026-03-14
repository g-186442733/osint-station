"""OSINT data layer — shared DB queries and LLM wrapper for all skills."""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

import requests

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "storage" / "osint.db"
STYLE_CSS_PATH = Path.home() / "Desktop" / "subtitle_work" / "style.css"
REPORTS_OUTPUT_DIR = Path.home() / "Desktop" / "osint-reports"

logger = logging.getLogger(__name__)

SOURCE_PREFIXES = ("r/", "hn_", "topic_")

# ─── LLM ────────────────────────────────────────────────────────────────

def _load_llm_config():
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from config.settings import LLM_API_BASE, LLM_API_KEY, MODELS
    return LLM_API_BASE, LLM_API_KEY, MODELS


def call_llm(prompt: str, system: str = "", model: str | None = None, max_tokens: int = 4000) -> str:
    api_base, api_key, models = _load_llm_config()
    if not api_key:
        logger.warning("No LLM API key configured")
        return ""

    target_models = [model or models["daily_summary"], models["fallback"]]
    for m in target_models:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": m, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens},
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"LLM call failed (model={m}): {e}")
    return ""


# ─── DB ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row
    return db


def is_person_slug(slug: str) -> bool:
    return not any(slug.startswith(p) for p in SOURCE_PREFIXES)


def resolve_person(name_or_slug: str, db: sqlite3.Connection) -> tuple[str, str] | None:
    """Resolve name/slug to (display_name, slug). Returns None if not found."""
    slug = name_or_slug.lower().replace(" ", "_")
    row = db.execute(
        "SELECT name, slug FROM profiles WHERE slug = ? OR LOWER(name) = LOWER(?)",
        (slug, name_or_slug),
    ).fetchone()
    if row:
        return row["name"], row["slug"]

    all_slugs = [r[0] for r in db.execute("SELECT DISTINCT profile_slug FROM posts").fetchall()]
    matched = [s for s in all_slugs if name_or_slug.lower() in s.lower()]
    if matched:
        s = matched[0]
        row = db.execute("SELECT name FROM profiles WHERE slug = ?", (s,)).fetchone()
        return (row["name"] if row else s), s
    return None


def get_person_posts(slug: str, db: sqlite3.Connection, days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT platform, COALESCE(title_zh, title) as title, content, url,
                  published_at, engagement_likes, engagement_comments, engagement_views, collected_at
           FROM posts WHERE profile_slug = ? AND COALESCE(NULLIF(published_at, ''), collected_at) > ?
           ORDER BY COALESCE(NULLIF(published_at, ''), collected_at) DESC""",
        (slug, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_recent_posts(db: sqlite3.Connection, days: int = 7, persons_only: bool = True) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT p.profile_slug, COALESCE(pr.name, p.profile_slug) as person_name,
                  p.platform, COALESCE(p.title_zh, p.title) as title, p.content, p.url,
                  p.published_at, p.engagement_likes, p.engagement_comments, p.engagement_views
           FROM posts p LEFT JOIN profiles pr ON p.profile_slug = pr.slug
           WHERE COALESCE(NULLIF(p.published_at, ''), p.collected_at) > ?
           ORDER BY (COALESCE(p.engagement_likes, 0) + COALESCE(p.engagement_comments, 0) * 3) DESC""",
        (cutoff,),
    ).fetchall()
    result = [dict(r) for r in rows]
    if persons_only:
        result = [r for r in result if is_person_slug(r["profile_slug"])]
    return result


def compute_person_stats(posts: list[dict]) -> dict:
    if not posts:
        return {"total": 0, "platforms": [], "avg_likes": 0, "avg_views": 0, "top_post": None}

    platforms = list(set(p["platform"] for p in posts))
    total_likes = sum(p.get("engagement_likes") or 0 for p in posts)
    total_views = sum(p.get("engagement_views") or 0 for p in posts)
    n = len(posts)

    by_platform = Counter(p["platform"] for p in posts)

    top_post = max(posts, key=lambda p: (p.get("engagement_likes") or 0) + (p.get("engagement_views") or 0) / 100)

    active_days = len(set(
        (p.get("published_at") or p.get("collected_at", ""))[:10]
        for p in posts if (p.get("published_at") or p.get("collected_at"))
    ))

    return {
        "total": n,
        "platforms": platforms,
        "platform_count": len(platforms),
        "avg_likes": round(total_likes / n, 1) if n else 0,
        "avg_views": round(total_views / n, 1) if n else 0,
        "total_likes": total_likes,
        "total_views": total_views,
        "by_platform": dict(by_platform),
        "active_days": active_days,
        "top_post": top_post,
    }


def fmt_number(n: int | float) -> str:
    if not n:
        return "0"
    n = int(n)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# ─── Output ─────────────────────────────────────────────────────────────

def ensure_output_dir():
    REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_style_css_link() -> str:
    if STYLE_CSS_PATH.exists():
        return f'<link rel="stylesheet" href="{STYLE_CSS_PATH}">'
    return '<link rel="stylesheet" href="style.css">'


# ─── Markdown → HTML ────────────────────────────────────────────────────

def md_to_html(text: str) -> str:
    """Convert simple Markdown to HTML (headings, bold, lists, paragraphs)."""
    import re
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"^### (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"^- (.+)$", r"<li>\1</li>", text, flags=re.MULTILINE)
    text = re.sub(r"^(\d+)\. (.+)$", r"<li>\2</li>", text, flags=re.MULTILINE)
    text = re.sub(r"(<li>.*?</li>\n?)+", lambda m: f"<ul>{m.group(0)}</ul>", text, flags=re.DOTALL)
    text = text.replace("</ul>\n<ul>", "\n")
    text = re.sub(r"\n\n+", "</p><p>", text)
    text = text.replace("\n", "<br>")
    if not text.startswith("<"):
        text = f"<p>{text}</p>"
    return text


# ─── Shared constants ───────────────────────────────────────────────────

PLATFORM_LABELS = {
    "bilibili": "B站", "weibo": "微博", "twitter": "X/推特",
    "youtube": "YouTube", "reddit": "Reddit", "hackernews": "HN", "github": "GitHub",
}
PLATFORM_COLORS = {
    "bilibili": "#FB7299", "weibo": "#E6162D", "twitter": "#1DA1F2",
    "youtube": "#FF0000", "reddit": "#FF5700", "hackernews": "#FF6600", "github": "#238636",
}
