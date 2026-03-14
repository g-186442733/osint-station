#!/usr/bin/env python3
"""OSINT 工作站 — Dashboard 服务"""

import json
import sqlite3
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from intel.profiler import run_profile_intel
from intel.discovery import discover_new_people
from intel.weekly_report import list_reports, get_report

DB_PATH = Path(__file__).parent.parent / "storage" / "osint.db"
PORT = 8501

PLATFORM_COLORS = {
    "bilibili": "#FB7299",
    "weibo": "#E6162D",
    "twitter": "#1DA1F2",
    "youtube": "#FF0000",
    "reddit": "#FF5700",
    "hackernews": "#FF6600",
    "github": "#238636",
}

# Slugs that are aggregation sources, NOT real people
SOURCE_PREFIXES = ("r/", "hn_", "topic_")

# English → Chinese topic label mapping (comprehensive)
TOPIC_LABEL_ZH = {
    "other": "其他",
    "personal_update": "个人动态",
    "tech_trend": "技术趋势",
    "business_opportunity": "商业机会",
    "industry_news": "行业动态",
    "gossip": "八卦",
}


def is_person_slug(slug: str) -> bool:
    """Check if a profile slug represents a real person (not a subreddit/topic/source)."""
    return not any(slug.startswith(p) for p in SOURCE_PREFIXES)


def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row
    return db


def api_data():
    db = get_db()
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0).isoformat()

    total_posts = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    today_new = db.execute(
        "SELECT COUNT(*) FROM posts WHERE COALESCE(NULLIF(published_at, ''), collected_at) > ?",
        (today_start,),
    ).fetchone()[0]
    people_count = db.execute(
        "SELECT COUNT(DISTINCT profile_slug) FROM posts"
    ).fetchone()[0]
    platform_count = db.execute(
        "SELECT COUNT(DISTINCT platform) FROM posts"
    ).fetchone()[0]

    by_platform = [
        {"platform": r[0], "count": r[1], "color": PLATFORM_COLORS.get(r[0], "#888")}
        for r in db.execute(
            "SELECT platform, COUNT(*) FROM posts GROUP BY platform ORDER BY COUNT(*) DESC"
        )
    ]

    by_person = [
        {"name": r[0], "count": r[1]}
        for r in db.execute(
            "SELECT COALESCE(p.name, posts.profile_slug), COUNT(*), posts.profile_slug FROM posts LEFT JOIN profiles p ON posts.profile_slug = p.slug GROUP BY posts.profile_slug ORDER BY COUNT(*) DESC"
        )
        if is_person_slug(r[2])
    ]

    slug_to_name = {r[0]: r[1] for r in db.execute("SELECT slug, name FROM profiles")}

    cutoff_30d = (now - timedelta(days=30)).isoformat()

    def _row_to_post(r):
        return {
            "person": slug_to_name.get(r[0], r[0]) or "",
            "platform": r[1],
            "title": r[2] or "",
            "content": (r[3] or "")[:120],
            "url": r[4] or "",
            "published_at": r[5] or "",
            "likes": r[6] or 0,
            "comments": r[7] or 0,
            "views": r[8] or 0,
            "collected_at": r[9] or "",
        }

    person_slugs = [s for s in slug_to_name if is_person_slug(s)]
    placeholders = ",".join("?" for _ in person_slugs)

    kol_posts = []
    if person_slugs:
        for r in db.execute(
            f"""
            SELECT profile_slug, platform, COALESCE(title_zh, title) as display_title,
                   content, url, published_at, engagement_likes, engagement_comments,
                   engagement_views, collected_at
            FROM posts
            WHERE profile_slug IN ({placeholders})
              AND COALESCE(NULLIF(published_at, ''), collected_at) > ?
            ORDER BY (COALESCE(engagement_likes, 0) + COALESCE(engagement_comments, 0) * 3 + COALESCE(engagement_views, 0) / 100) DESC,
                     COALESCE(NULLIF(published_at, ''), collected_at) DESC
            LIMIT 20
        """,
            (*person_slugs, cutoff_30d),
        ):
            kol_posts.append(_row_to_post(r))

    industry_news = []
    for r in db.execute(
        """
        SELECT profile_slug, platform, COALESCE(title_zh, title) as display_title,
               content, url, published_at, engagement_likes, engagement_comments,
               engagement_views, collected_at
        FROM posts
        WHERE platform IN ('hackernews', 'reddit')
          AND COALESCE(NULLIF(published_at, ''), collected_at) > ?
        ORDER BY (COALESCE(engagement_likes, 0) + COALESCE(engagement_comments, 0) * 3) DESC,
                 COALESCE(NULLIF(published_at, ''), collected_at) DESC
        LIMIT 10
    """,
        (cutoff_30d,),
    ):
        industry_news.append(_row_to_post(r))

    recent = kol_posts

    topics = [
        {
            "name": TOPIC_LABEL_ZH.get(r[0], r[0]),
            "category": r[1] or "other",
            "count": r[2],
        }
        for r in db.execute(
            "SELECT name, category, post_count FROM topics WHERE post_count > 0 ORDER BY post_count DESC LIMIT 15"
        )
    ]

    brief = ""
    row = db.execute(
        "SELECT content FROM alerts WHERE alert_type='daily_brief' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row:
        brief = row[0]

    db.close()
    return {
        "total_posts": total_posts,
        "today_new": today_new,
        "people_count": people_count,
        "platform_count": platform_count,
        "by_platform": by_platform,
        "by_person": by_person,
        "recent": recent,
        "industry_news": industry_news,
        "topics": topics,
        "brief": brief,
        "updated_at": now.isoformat(),
    }


def api_people():
    db = get_db()
    rows = db.execute(
        "SELECT slug, name, tags, priority FROM profiles ORDER BY name"
    ).fetchall()
    db.close()
    return [
        {
            "slug": r["slug"],
            "name": r["name"],
            "tags": r["tags"] or "[]",
            "priority": r["priority"] or "normal",
        }
        for r in rows
        if is_person_slug(r["slug"])
    ]


def api_search(
    query: str = "",
    days: int = 0,
    platform: str = "",
    person: str = "",
    limit: int = 50,
):
    db = get_db()
    conditions = []
    params = []
    use_fts = False

    if query:
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in query)
        use_fts = False
        if not has_cjk:
            try:
                db.execute("SELECT 1 FROM posts_fts LIMIT 1")
                use_fts = True
            except Exception:
                pass

    if use_fts and query:
        fts_query = query.replace('"', '""')
        conditions.append(
            "posts.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)"
        )
        params.append(f'"{fts_query}"')
    elif query:
        conditions.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if person:
        conditions.append("profile_slug = ?")
        params.append(person)
    if days > 0:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conditions.append("collected_at > ?")
        params.append(since)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    rows = db.execute(
        f"""
        SELECT profile_slug, platform, COALESCE(title_zh, title) as display_title,
               content, url, published_at, engagement_likes, engagement_comments,
               engagement_views, collected_at
        FROM posts WHERE {where}
        ORDER BY collected_at DESC LIMIT ?
    """,
        params,
    ).fetchall()

    total = db.execute(
        f"SELECT COUNT(*) FROM posts WHERE {where}", params[:-1]
    ).fetchone()[0]
    db.close()

    results = []
    for r in rows:
        results.append(
            {
                "person": r[0] or "",
                "platform": r[1],
                "title": r[2] or "",
                "content": (r[3] or "")[:200],
                "url": r[4] or "",
                "published_at": r[5] or "",
                "likes": r[6] or 0,
                "comments": r[7] or 0,
                "views": r[8] or 0,
                "collected_at": r[9] or "",
            }
        )

    return {"total": total, "results": results}


def api_compare(slugs: list[str]):
    db = get_db()
    people_data = []

    for slug in slugs:
        profile = db.execute(
            "SELECT name, slug, tags, priority FROM profiles WHERE slug = ?", (slug,)
        ).fetchone()
        if not profile:
            continue

        posts = db.execute(
            "SELECT platform, engagement_likes, engagement_comments, engagement_views FROM posts WHERE profile_slug = ?",
            (slug,),
        ).fetchall()

        by_platform = {}
        total_likes = 0
        total_views = 0
        for p in posts:
            plat = p["platform"]
            if plat not in by_platform:
                by_platform[plat] = {"count": 0, "likes": 0, "views": 0}
            by_platform[plat]["count"] += 1
            by_platform[plat]["likes"] += p["engagement_likes"] or 0
            by_platform[plat]["views"] += p["engagement_views"] or 0
            total_likes += p["engagement_likes"] or 0
            total_views += p["engagement_views"] or 0

        people_data.append(
            {
                "name": profile["name"],
                "slug": profile["slug"],
                "tags": profile["tags"] or "[]",
                "priority": profile["priority"] or "normal",
                "total_posts": len(posts),
                "total_likes": total_likes,
                "total_views": total_views,
                "platforms": list(by_platform.keys()),
                "by_platform": by_platform,
            }
        )

    db.close()
    return people_data


def api_trends():
    db = get_db()
    topics = db.execute(
        "SELECT id, name, trend_score, post_count FROM topics WHERE post_count > 0 ORDER BY trend_score DESC LIMIT 10"
    ).fetchall()
    result = []
    for t in topics:
        daily = db.execute(
            "SELECT DATE(collected_at) as day, COUNT(*) as cnt FROM posts WHERE topic_id = ? AND collected_at > datetime('now', '-14 days') GROUP BY DATE(collected_at) ORDER BY day",
            (t["id"],),
        ).fetchall()
        result.append(
            {
                "name": t["name"],
                "trend_score": t["trend_score"],
                "post_count": t["post_count"],
                "daily": [{"day": d["day"], "count": d["cnt"]} for d in daily],
            }
        )
    db.close()
    return result


def api_alerts_recent():
    db = get_db()
    rows = db.execute(
        "SELECT id, alert_type, title, content, created_at FROM alerts WHERE alert_type != 'daily_brief' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    db.close()
    return [
        {
            "id": r["id"],
            "type": r["alert_type"],
            "title": r["title"],
            "content": (r["content"] or "")[:300],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def api_staleness():
    db = get_db()
    threshold = (datetime.now() - timedelta(days=7)).isoformat()
    stale = db.execute(
        "SELECT name, slug, last_updated FROM profiles WHERE last_updated < ? AND priority IN ('high', 'medium') ORDER BY last_updated ASC",
        (threshold,),
    ).fetchall()
    db.close()
    return [
        {"name": r["name"], "slug": r["slug"], "last_updated": r["last_updated"]}
        for r in stale
    ]


def add_person_to_watchlist(data):
    from config.settings import WATCHLIST_PATH

    name = data.get("name", "").strip()
    if not name:
        return {"error": "名称不能为空"}

    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    for p in watchlist.get("people", []):
        if p["name"].lower() == name.lower():
            return {"error": f"'{name}' 已存在"}

    usernames = {}
    for plat, key in [
        ("bilibili", "bilibili"),
        ("weibo", "weibo"),
        ("twitter", "twitter"),
        ("youtube", "youtube"),
        ("github", "github"),
        ("reddit", "reddit"),
    ]:
        val = data.get(key, "").strip()
        if val:
            usernames[plat] = val

    if not usernames:
        return {"error": "至少需要填写一个平台用户名"}

    person = {
        "name": name,
        "usernames": usernames,
        "tags": [t.strip() for t in data.get("tags", "").split(",") if t.strip()],
        "priority": data.get("priority", "medium"),
        "notes": data.get("notes", ""),
    }
    watchlist["people"].append(person)

    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)

    slug = name.lower().replace(" ", "_")
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO profiles (name, slug, tags, priority, first_seen, last_updated) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            name,
            slug,
            json.dumps(person["tags"], ensure_ascii=False),
            person["priority"],
        ),
    )
    db.commit()
    db.close()

    return {"status": "ok", "name": name, "platforms": list(usernames.keys())}


def remove_person_from_watchlist(data):
    from config.settings import WATCHLIST_PATH

    name = data.get("name", "").strip()
    if not name:
        return {"error": "名称不能为空"}

    removed_from_watchlist = False
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    original_count = len(watchlist.get("people", []))
    watchlist["people"] = [
        p for p in watchlist.get("people", []) if p["name"].lower() != name.lower()
    ]

    if len(watchlist["people"]) < original_count:
        removed_from_watchlist = True
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(watchlist, f, ensure_ascii=False, indent=2)

    removed_from_db = False
    try:
        conn = get_db()
        cur = conn.cursor()
        slugs = [
            r[0]
            for r in cur.execute(
                "SELECT slug FROM profiles WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchall()
        ]
        cur.execute("DELETE FROM profiles WHERE LOWER(name) = LOWER(?)", (name,))
        db_deleted = cur.rowcount
        for s in slugs:
            cur.execute("DELETE FROM posts WHERE profile_slug = ?", (s,))
            db_deleted += cur.rowcount
        conn.commit()
        removed_from_db = db_deleted > 0
        conn.close()
    except Exception:
        pass

    if not removed_from_watchlist and not removed_from_db:
        return {"error": f"'{name}' 不在监控列表中"}

    return {"status": "ok", "name": name}


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSINT 情报站</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fdfbf7;color:#2d2a26;font-family:"PingFang SC", "Hiragino Sans GB", -apple-system;padding:20px}
a{color:#d44536;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.header h1{font-size:22px;color:#d44536;font-family:\"Songti SC\", \"Hiragino Mincho ProN\", serif}
.header .sub{color:#8c8172;font-size:13px}
.nav-links{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.nav-btn{color:#d44536;font-size:13px;border:1px solid #e8dfd0;padding:6px 14px;border-radius:6px;background:#ffffff;text-decoration:none;white-space:nowrap}
.nav-btn:hover{background:#f5f0eb;text-decoration:none}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.card{background:#ffffff;border:2px solid #e8dfd0;border-radius:8px;padding:20px;box-shadow:4px 4px 0 #e8dfd0;transition:transform 0.2s, box-shadow 0.2s}
.card:hover{transform:translateY(-3px);box-shadow:6px 6px 0 #e8dfd0}
.card .label{font-size:12px;color:#8c8172;text-transform:uppercase;letter-spacing:1px;font-weight:bold}
.card .value{font-size:32px;font-weight:700;color:#d44536;margin-top:4px;font-family:"Songti SC", "Hiragino Mincho ProN", serif}
.filter-bar{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;align-items:center}
.filter-bar input,.filter-bar select{padding:8px 12px;background:#ffffff;border:1px solid #e8dfd0;border-radius:6px;color:#1d1b19;font-size:13px;outline:none}
.filter-bar input:focus,.filter-bar select:focus{border-color:#d44536}
.filter-bar input{flex:1;min-width:180px}
.filter-bar button{padding:8px 16px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:13px;cursor:pointer;font-weight:600}
.filter-bar button:hover{background:#2ea043}
.time-pills{display:flex;gap:6px}
.time-pill{padding:5px 12px;border-radius:14px;font-size:12px;cursor:pointer;background:#ffffff;border:1px solid #e8dfd0;color:#8c8172;transition:all .2s}
.time-pill.active{border-color:#d44536;color:#d44536;background:#1f6feb22}
.time-pill:hover{border-color:#d44536;color:#d44536}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.panel{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:20px;box-shadow:0 4px 16px rgba(45, 42, 38, 0.04)}
.panel h2{font-size:14px;color:#d44536;margin-bottom:16px;font-weight:600}
.feed{max-height:520px;overflow-y:auto}
.feed-item{padding:12px 0;border-bottom:1px solid #f5f0eb}
.feed-item:last-child{border-bottom:none}
.feed-meta{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.badge{font-size:11px;padding:2px 8px;border-radius:12px;color:#fff;font-weight:600}
.person-name{font-size:13px;color:#8c8172}
.feed-title{font-size:14px;color:#2d2a26;margin-bottom:4px}
.feed-title a{color:#d44536;text-decoration:none}
.feed-title a:hover{text-decoration:underline}
.feed-stats{font-size:12px;color:#8c8172;display:flex;gap:12px}
.topics-grid{display:flex;flex-wrap:wrap;gap:8px}
.topic-tag{padding:6px 12px;border-radius:16px;font-size:12px;font-weight:500;cursor:default}
.brief-content{font-size:15px;line-height:1.85;color:#2d2a26;white-space:pre-wrap;max-height:500px;overflow-y:auto}
.full-width{grid-column:1/-1}
.result-count{font-size:12px;color:#8c8172;margin-bottom:8px}
canvas{max-height:220px}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:#fdfbf7}
::-webkit-scrollbar-thumb{background:#e8dfd0;border-radius:3px}
.cat-business_opportunity{background:#1f6feb33;color:#d44536;border:1px solid #1f6feb}
.cat-tech_trend{background:#7faa6e33;color:#7faa6e;border:1px solid #7faa6e}
.cat-industry_news{background:#d4a04a33;color:#d4a04a;border:1px solid #d4a04a}
.cat-personal_update{background:#8c817233;color:#8c8172;border:1px solid #8c8172}
.cat-other{background:#e8dfd0;color:#8c8172;border:1px solid #484f58}
.cat-gossip{background:#f778ba33;color:#f778ba;border:1px solid #f778ba}
@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <h1>🛰️ OSINT 情报站</h1>
  <span class="sub" id="updateTime"></span>
  <div class="nav-links">
    <a href="/intel" class="nav-btn">🔍 人物情报</a>
    <a href="/discover" class="nav-btn">🌐 发现推荐</a>
    <a href="/reports" class="nav-btn">📋 情报周报</a>
    <a href="/manage" class="nav-btn">⚙️ 管理</a>
  </div>
</div>

<div id="stalenessBar" style="display:none;background:#da361933;border:1px solid #d4453666;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#d44536">
  <strong>⚠️ 数据过期警告:</strong> <span id="stalenessText"></span>
</div>

<div class="filter-bar">
  <input type="text" id="searchQ" placeholder="搜索内容关键词...">
  <select id="filterPlatform"><option value="">全部平台</option></select>
  <select id="filterPerson"><option value="">全部人物</option></select>
  <div class="time-pills">
    <span class="time-pill active" data-days="0" onclick="setDays(0,this)">全部</span>
    <span class="time-pill" data-days="1" onclick="setDays(1,this)">24h</span>
    <span class="time-pill" data-days="7" onclick="setDays(7,this)">7天</span>
    <span class="time-pill" data-days="30" onclick="setDays(30,this)">30天</span>
  </div>
  <button onclick="doSearch()">搜索</button>
</div>

<div class="cards">
  <div class="card"><div class="label">总帖子数</div><div class="value" id="totalPosts">-</div></div>
  <div class="card"><div class="label">今日新增</div><div class="value" id="todayNew">-</div></div>
  <div class="card"><div class="label">关注人物</div><div class="value" id="peopleCount">-</div></div>
  <div class="card"><div class="label">覆盖平台</div><div class="value" id="platformCount">-</div></div>
</div>

<div id="alertsBar" class="panel" style="margin-bottom:16px;display:none">
  <h2>⚠️ 最新告警</h2>
  <div id="alertsList" style="max-height:400px;overflow-y:auto"></div>
</div>

<div class="grid">
  <div class="panel">
    <h2>📊 平台分布</h2>
    <canvas id="platformChart"></canvas>
  </div>
  <div class="panel">
    <h2>👤 人物动态排行</h2>
    <canvas id="personChart"></canvas>
  </div>
</div>
<div class="grid">
  <div class="panel">
    <h2>📰 人物动态</h2>
    <div class="result-count" id="resultCount"></div>
    <div class="feed" id="feedList"></div>
  </div>
  <div class="panel">
    <h2>🔥 话题热度 TOP 15</h2>
    <div class="topics-grid" id="topicsList"></div>
    <div style="margin-top:20px">
      <h2>📋 最新情报简报</h2>
      <div class="brief-content" id="briefContent" style="margin-top:12px"></div>
    </div>
  </div>
</div>
<div class="grid">
  <div class="panel full-width">
    <h2>📡 行业资讯 <span style="font-size:12px;color:#8c8172;font-weight:normal">(HN / Reddit 高互动内容)</span></h2>
    <div class="feed" id="industryFeed" style="max-height:320px"></div>
  </div>
</div>
<div class="grid">
  <div class="panel full-width">
    <h2>📈 话题趋势 (14天)</h2>
    <canvas id="trendChart" style="max-height:300px"></canvas>
  </div>
</div>
<script>
const COLORS = {bilibili:'#FB7299',weibo:'#E6162D',twitter:'#1DA1F2',youtube:'#FF0000',reddit:'#FF5700',hackernews:'#FF6600',github:'#238636'};
const TOPIC_ZH = {'other':'其他','personal_update':'个人动态','tech_trend':'技术趋势','business_opportunity':'商业机会','industry_news':'行业动态','gossip':'八卦','AI工具与应用':'AI工具与应用','AI发展趋势与影响':'AI发展趋势与影响','行业动态':'行业动态','个人动态':'个人动态','AI模型发布动态':'AI模型发布动态','行业合作与应用':'行业合作与应用','GPT-5.4发布动态':'GPT-5.4发布动态','OpenAI相关讨论':'OpenAI相关讨论','AI模型开源与发展趋势':'AI模型开源与发展趋势','OpenAI产品发布':'OpenAI产品发布','OpenAI产品改进':'OpenAI产品改进'};
let platChart, personChart;
let currentDays = 0;

function setDays(d, el) {
  currentDays = d;
  document.querySelectorAll('.time-pill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  doSearch();
}

document.getElementById('searchQ').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

function relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), diff = (now - d) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff/60) + '分钟前';
  if (diff < 86400) return Math.floor(diff/3600) + '小时前';
  if (diff < 172800) return '昨天';
  if (diff < 604800) return Math.floor(diff/86400) + '天前';
  return d.toLocaleDateString('zh-CN');
}

function fmt(n) {
  if (n >= 10000) return (n/10000).toFixed(1) + '万';
  if (n >= 1000) return (n/1000).toFixed(1) + 'k';
  return n;
}

function renderFeed(items, total) {
  const count = document.getElementById('resultCount');
  count.textContent = total !== undefined ? `共 ${total} 条结果` : '';

  const feed = document.getElementById('feedList');
  feed.innerHTML = items.map(r => {
    const color = COLORS[r.platform] || '#888';
    const title = r.title || r.content || '(无内容)';
    const link = r.url ? `<a href="${r.url}" target="_blank">${title}</a>` : title;
    const stats = [];
    if (r.views) stats.push('👁 ' + fmt(r.views));
    if (r.likes) stats.push('❤️ ' + fmt(r.likes));
    if (r.comments) stats.push('💬 ' + fmt(r.comments));
    return `<div class="feed-item">
      <div class="feed-meta"><span class="badge" style="background:${color}">${r.platform}</span><span class="person-name">${r.person}</span><span class="person-name">${relTime(r.published_at || r.collected_at)}</span></div>
      <div class="feed-title">${link}</div>
      <div class="feed-stats">${stats.join(' ')}</div>
    </div>`;
  }).join('');
}

function render(d) {
  document.getElementById('totalPosts').textContent = d.total_posts;
  document.getElementById('todayNew').textContent = d.today_new;
  document.getElementById('peopleCount').textContent = d.people_count;
  document.getElementById('platformCount').textContent = d.platform_count;
  document.getElementById('updateTime').textContent = '更新于 ' + new Date(d.updated_at).toLocaleString('zh-CN');

  const pCtx = document.getElementById('platformChart').getContext('2d');
  if (platChart) platChart.destroy();
  platChart = new Chart(pCtx, {
    type: 'doughnut',
    data: {
      labels: d.by_platform.map(p => p.platform),
      datasets: [{data: d.by_platform.map(p => p.count), backgroundColor: d.by_platform.map(p => p.color), borderWidth: 0}]
    },
    options: {plugins:{legend:{labels:{color:'#2d2a26',font:{size:12}}}}, cutout:'60%'}
  });

  const bCtx = document.getElementById('personChart').getContext('2d');
  if (personChart) personChart.destroy();
  personChart = new Chart(bCtx, {
    type: 'bar',
    data: {
      labels: d.by_person.slice(0,10).map(p => p.name),
      datasets: [{data: d.by_person.slice(0,10).map(p => p.count), backgroundColor: '#d44536', borderRadius: 4}]
    },
    options: {indexAxis:'y', plugins:{legend:{display:false}}, scales:{x:{ticks:{color:'#8c8172'},grid:{color:'#f5f0eb'}},y:{ticks:{color:'#2d2a26'},grid:{display:false}}}}
  });

  renderFeed(d.recent);

  // Render industry news
  const industryFeed = document.getElementById('industryFeed');
  if (d.industry_news && d.industry_news.length > 0) {
    industryFeed.innerHTML = d.industry_news.map(r => {
      const color = COLORS[r.platform] || '#888';
      const title = r.title || r.content || '(无内容)';
      const link = r.url ? `<a href="${r.url}" target="_blank">${title}</a>` : title;
      const stats = [];
      if (r.views) stats.push('👁 ' + fmt(r.views));
      if (r.likes) stats.push('❤️ ' + fmt(r.likes));
      if (r.comments) stats.push('💬 ' + fmt(r.comments));
      return `<div class="feed-item">
        <div class="feed-meta"><span class="badge" style="background:${color}">${r.platform}</span><span class="person-name">${r.person}</span><span class="person-name">${relTime(r.published_at || r.collected_at)}</span></div>
        <div class="feed-title">${link}</div>
        <div class="feed-stats">${stats.join(' ')}</div>
      </div>`;
    }).join('');
  } else {
    industryFeed.innerHTML = '<div style="color:#8c8172;padding:20px;text-align:center">暂无行业资讯</div>';
  }

  const topics = document.getElementById('topicsList');
  topics.innerHTML = d.topics.map(t => {
    const cat = t.category || 'other';
    const displayName = TOPIC_ZH[t.name] || t.name;
    return `<span class="topic-tag cat-${cat}">${displayName} (${t.count})</span>`;
  }).join('');

  document.getElementById('briefContent').textContent = d.brief || '暂无简报';

  // Populate filter dropdowns
  const platSelect = document.getElementById('filterPlatform');
  if (platSelect.options.length <= 1) {
    d.by_platform.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.platform;
      opt.textContent = p.platform;
      platSelect.appendChild(opt);
    });
  }
  const personSelect = document.getElementById('filterPerson');
  if (personSelect.options.length <= 1) {
    d.by_person.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name;
      personSelect.appendChild(opt);
    });
  }
}

async function doSearch() {
  const q = document.getElementById('searchQ').value.trim();
  const platform = document.getElementById('filterPlatform').value;
  const person = document.getElementById('filterPerson').value;

  if (!q && !platform && !person && currentDays === 0) {
    load();
    return;
  }

  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (platform) params.set('platform', platform);
  if (person) params.set('person', person);
  if (currentDays > 0) params.set('days', currentDays);

  try {
    const r = await fetch('/api/search?' + params.toString());
    const d = await r.json();
    renderFeed(d.results, d.total);
  } catch(e) { console.error('搜索失败', e); }
}

async function load() {
  try {
    const r = await fetch('/api/data');
    const d = await r.json();
    render(d);
  } catch(e) { console.error('加载失败', e); }
}

load();
setInterval(load, 300000);

let trendChart;
async function loadTrends() {
  try {
    const r = await fetch('/api/trends');
    const topics = await r.json();
    if (!topics.length) return;
    const days = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i);
      days.push(d.toISOString().slice(0,10));
    }
    const colors = ['#d44536','#7faa6e','#d4a04a','#d44536','#f778ba','#8c8172','#FF5700','#FB7299','#FF0000','#238636'];
    const datasets = topics.slice(0,6).map((t, idx) => {
      const dayMap = {};
      t.daily.forEach(d => dayMap[d.day] = d.count);
      return {
        label: t.name + ' (' + (t.trend_score||0).toFixed(1) + ')',
        data: days.map(d => dayMap[d] || 0),
        borderColor: colors[idx % colors.length],
        backgroundColor: 'transparent',
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 2
      };
    });
    const ctx = document.getElementById('trendChart').getContext('2d');
    if (trendChart) trendChart.destroy();
    trendChart = new Chart(ctx, {
      type: 'line',
      data: { labels: days.map(d => d.slice(5)), datasets },
      options: {
        plugins: { legend: { labels: { color: '#2d2a26', font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: '#8c8172' }, grid: { color: '#f5f0eb' } },
          y: { ticks: { color: '#8c8172' }, grid: { color: '#f5f0eb' }, beginAtZero: true }
        }
      }
    });
  } catch(e) { console.error('趋势加载失败', e); }
}
loadTrends();

async function loadAlerts() {
  try {
    const r = await fetch('/api/alerts/recent');
    const alerts = await r.json();
    if (!alerts.length) return;
    document.getElementById('alertsBar').style.display = 'block';
    const typeLabels = {anomaly_alert:'异常告警',weekly_intel:'情报周报',trend_alert:'趋势告警'};
    const typeColors = {anomaly_alert:'#d44536',weekly_intel:'#d44536',trend_alert:'#d4a04a'};
    document.getElementById('alertsList').innerHTML = alerts.map(a => {
      const color = typeColors[a.type] || '#8c8172';
      const label = typeLabels[a.type] || a.type;
      const time = new Date(a.created_at).toLocaleString('zh-CN');
      let preview = (a.content || '').replace(/^#[^\n]*/gm,'').replace(/\|[^\n]*/gm,'').replace(/[>\-*#|]/g,' ').replace(/\s+/g,' ').trim().slice(0,150);
      if (preview.length >= 150) preview += '...';
      const link = a.type === 'weekly_intel' ? `<a href="/reports" style="color:#d44536;font-size:12px;margin-left:8px">查看完整报告 →</a>` : '';
      return `<div style="padding:10px 12px;border-bottom:1px solid #f5f0eb;font-size:13px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
          <span style="color:${color};font-weight:600;font-size:12px;padding:2px 6px;border-radius:4px;border:1px solid ${color}33;background:${color}11">${label}</span>
          <span style="color:#1d1b19;font-weight:500">${a.title}</span>
          <span style="color:#8c8172;font-size:11px;margin-left:auto">${time}</span>
        </div>
        <div style="color:#8c8172;font-size:12px;line-height:1.5;margin-top:4px">${preview}${link}</div>
      </div>`;
    }).join('');
  } catch(e) {}
}
loadAlerts();

async function loadStaleness() {
  try {
    const r = await fetch('/api/staleness');
    const stale = await r.json();
    if (!stale.length) return;
    document.getElementById('stalenessBar').style.display = 'block';
    const names = stale.map(s => s.name + ' (' + new Date(s.last_updated).toLocaleDateString('zh-CN') + ')');
    document.getElementById('stalenessText').textContent = names.join(', ') + ' 超过7天未更新数据';
  } catch(e) {}
}
loadStaleness();
</script>
</body>
</html>"""


INTEL_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSINT 人物情报</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9/dist/vis-network.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fdfbf7;color:#2d2a26;font-family:"PingFang SC", "Hiragino Sans GB", -apple-system;padding:20px}
a{color:#d44536;text-decoration:none}
a:hover{text-decoration:underline}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.header h1{font-size:22px;color:#d44536;font-family:\"Songti SC\", \"Hiragino Mincho ProN\", serif}
.nav-btn{color:#d44536;font-size:14px;border:1px solid #e8dfd0;padding:6px 16px;border-radius:6px;background:#ffffff;text-decoration:none;white-space:nowrap}
.nav-btn:hover{background:#f5f0eb;text-decoration:none}
.search-box{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.search-input{flex:1;min-width:200px;padding:12px 16px;background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;color:#1d1b19;font-size:15px;outline:none}
.search-input:focus{border-color:#d44536}
.search-btn{padding:12px 24px;background:#238636;border:none;border-radius:8px;color:#fff;font-size:15px;cursor:pointer;font-weight:600}
.search-btn:hover{background:#2ea043}
.search-btn:disabled{opacity:.5;cursor:not-allowed}
.people-list{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px}
.people-chip{padding:4px 12px;border-radius:14px;font-size:12px;cursor:pointer;background:#ffffff;border:1px solid #e8dfd0;color:#2d2a26;transition:all .2s}
.people-chip:hover{border-color:#d44536;color:#d44536}
.people-chip.high{border-color:#d4453666}
.loading{text-align:center;padding:60px;color:#8c8172;font-size:16px}
.spinner{display:inline-block;width:24px;height:24px;border:3px solid #e8dfd0;border-top-color:#d44536;border-radius:50%;animation:spin .8s linear infinite;margin-right:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.hidden{display:none}
.result{animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}

/* Profile header */
.profile-header{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:24px;margin-bottom:20px;display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap}
.profile-name{font-size:24px;font-weight:700;color:#1d1b19}
.profile-slug{font-size:13px;color:#8c8172;margin-top:2px}
.profile-bio{font-size:14px;color:#2d2a26;margin-top:8px;line-height:1.5}
.tag{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;margin-right:6px;margin-top:6px}
.tag-high{background:#da361933;color:#d44536;border:1px solid #d4453666}
.tag-normal{background:#1f6feb33;color:#d44536;border:1px solid #1f6feb66}
.tag-topic{background:#7faa6e33;color:#7faa6e;border:1px solid #7faa6e66}
.priority-badge{padding:4px 12px;border-radius:12px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.priority-high{background:#da361933;color:#d44536;border:1px solid #d4453666}
.priority-medium{background:#d4a04a33;color:#d4a04a;border:1px solid #d4a04a66}
.priority-normal{background:#e8dfd0;color:#8c8172}

/* Cards grid */
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}
.card{background:#ffffff;border:2px solid #e8dfd0;border-radius:8px;padding:20px;box-shadow:4px 4px 0 #e8dfd0;transition:transform 0.2s, box-shadow 0.2s}
.card:hover{transform:translateY(-3px);box-shadow:6px 6px 0 #e8dfd0}
.card .label{font-size:12px;color:#8c8172;text-transform:uppercase;letter-spacing:1px;font-weight:bold}
.card .value{font-size:28px;font-weight:700;color:#1d1b19;margin-top:4px}

/* Identity cards */
.identity-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-bottom:20px}
.id-card{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:16px;display:flex;align-items:center;gap:12px}
.id-platform{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;color:#fff;flex-shrink:0}
.id-info{overflow:hidden}
.id-username{font-size:14px;color:#1d1b19;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.id-confidence{font-size:11px;color:#8c8172;margin-top:2px}

/* Section */
.section{margin-bottom:20px}
.section-title{font-size:15px;color:#d44536;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px}

/* Two column layout */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
.panel{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:20px;box-shadow:0 4px 16px rgba(45, 42, 38, 0.04)}

/* Network graph */
#network{width:100%;height:500px;background:#fdfbf7;border:1px solid #e8dfd0;border-radius:8px}
#networkDetail{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:16px;margin-top:12px;display:none;animation:fadeIn .3s ease}
#networkDetail .nd-name{font-size:16px;font-weight:700;color:#1d1b19;margin-bottom:4px}
#networkDetail .nd-stats{font-size:13px;color:#8c8172;margin-bottom:8px}
#networkDetail .nd-bio{font-size:13px;color:#4a4642;line-height:1.6;margin-bottom:8px}
#networkDetail .nd-actions a{font-size:13px;color:#d44536;margin-right:12px}

/* Viewpoints */
.viewpoints{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:24px;font-size:14px;line-height:1.8;color:#2d2a26;margin-bottom:20px}
.viewpoints h1,.viewpoints h2,.viewpoints h3{color:#d44536;margin:16px 0 8px;font-size:15px}
.viewpoints h1{font-size:17px}
.viewpoints ul,.viewpoints ol{padding-left:20px;margin:8px 0}
.viewpoints li{margin:4px 0}
.viewpoints strong{color:#1d1b19}
.viewpoints p{margin:8px 0}

/* Timeline */
.timeline{max-height:500px;overflow-y:auto}
.tl-item{padding:14px 0;border-bottom:1px solid #f5f0eb;display:flex;gap:12px}
.tl-item:last-child{border-bottom:none}
.tl-dot{width:10px;height:10px;border-radius:50%;margin-top:6px;flex-shrink:0}
.tl-content{flex:1;min-width:0}
.tl-meta{display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap}
.tl-badge{font-size:11px;padding:2px 8px;border-radius:12px;color:#fff;font-weight:600}
.tl-date{font-size:12px;color:#8c8172}
.tl-title{font-size:14px;color:#2d2a26;margin-bottom:4px}
.tl-title a{color:#d44536}
.tl-stats{font-size:12px;color:#8c8172;display:flex;gap:12px}

canvas{max-height:220px}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:#fdfbf7}
::-webkit-scrollbar-thumb{background:#e8dfd0;border-radius:3px}
@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}.identity-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <h1>🔍 人物情报</h1>
  <a href="/" class="nav-btn" style="margin-left:auto">🛰️ 返回总览</a>
</div>

<div class="search-box">
  <input type="text" id="searchInput" class="search-input" placeholder="输入人物名称，如：Sam Altman、林亦LYi、李开复..." autofocus>
  <button id="searchBtn" class="search-btn" onclick="doSearch()">🔍 分析</button>
</div>
<div class="people-list" id="peopleList"></div>

<div id="loadingArea" class="loading hidden">
  <span class="spinner"></span>正在分析，请稍候（LLM分析可能需要10-20秒）...
</div>

<div id="resultArea" class="hidden result">
  <!-- Profile Header -->
  <div class="profile-header" id="profileHeader"></div>

  <!-- Influence Stats Cards -->
  <div class="cards" id="statsCards"></div>

  <div class="grid2">
    <div class="panel">
      <div class="section-title">📊 平台数据分布</div>
      <canvas id="platChart"></canvas>
    </div>
    <div class="panel">
      <div class="section-title">🧠 观点分析</div>
      <div class="viewpoints" id="viewpoints"></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">🕸️ 关系网络 <span style="font-size:12px;color:#8c8172;font-weight:normal;margin-left:8px">点击节点查看详情</span></div>
    <div class="panel" style="padding:0;overflow:hidden">
      <div id="network"></div>
      <div id="networkDetail"></div>
    </div>
  </div>

  <!-- Timeline -->
  <div class="section">
    <div class="section-title">📅 活跃时间线</div>
    <div class="panel">
      <div class="timeline" id="timeline"></div>
    </div>
  </div>

  <!-- Identity at bottom -->
  <div class="section">
    <div class="section-title">🌐 跨平台身份</div>
    <div class="identity-grid" id="identityGrid"></div>
  </div>
</div>

<script>
const COLORS = {bilibili:'#FB7299',weibo:'#E6162D',twitter:'#1DA1F2',youtube:'#FF0000'};
const PLAT_LABELS = {bilibili:'B站',weibo:'微博',twitter:'X/推特',youtube:'YouTube'};
let platChartInst = null;

// Load people list
async function loadPeople() {
  try {
    const r = await fetch('/api/people');
    const people = await r.json();
    const el = document.getElementById('peopleList');
    el.innerHTML = people.map(p => {
      const cls = p.priority === 'high' ? 'people-chip high' : 'people-chip';
      return `<span class="${cls}" onclick="searchFor('${p.name.replace(/'/g,"\\\'")}')">${p.name}</span>`;
    }).join('');
  } catch(e) { console.error(e); }
}

function searchFor(name) {
  document.getElementById('searchInput').value = name;
  doSearch();
}

document.getElementById('searchInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

async function doSearch() {
  const name = document.getElementById('searchInput').value.trim();
  if (!name) return;
  const btn = document.getElementById('searchBtn');
  btn.disabled = true;
  document.getElementById('loadingArea').classList.remove('hidden');
  document.getElementById('resultArea').classList.add('hidden');

  try {
    const r = await fetch('/api/intel?name=' + encodeURIComponent(name));
    const d = await r.json();
    if (d.error) {
      alert(d.error + (d.suggestion ? '\n' + d.suggestion : ''));
      return;
    }
    renderResult(d);
    document.getElementById('resultArea').classList.remove('hidden');
  } catch(e) {
    alert('请求失败: ' + e.message);
  } finally {
    btn.disabled = false;
    document.getElementById('loadingArea').classList.add('hidden');
  }
}

function fmt(n) {
  if (!n) return '0';
  if (n >= 10000) return (n/10000).toFixed(1) + '万';
  if (n >= 1000) return (n/1000).toFixed(1) + 'k';
  return n;
}

function renderLightweightResult(d) {
  document.getElementById('profileHeader').innerHTML = `
    <div style="flex:1;min-width:200px">
      <div class="profile-name">${d.name} <span class="priority-badge" style="background:#d4a04a33;color:#d4a04a;border:1px solid #d4a04a66">未追踪</span></div>
      <div class="profile-slug">@${d.slug}</div>
      ${d.bio ? `<div class="profile-bio">${d.bio}</div>` : ''}
    </div>
    <div style="text-align:right;font-size:12px;color:#8c8172">
      分析时间<br>${new Date(d.generated_at).toLocaleString('zh-CN')}
    </div>`;

  const mentioners = d.mentioned_by || [];
  const mentionHtml = mentioners.map(m =>
    `<span class="people-chip" onclick="searchFor('${m.name.replace(/'/g,"\\\\'")}')" style="cursor:pointer">${m.name} <span style="color:#d44536;font-weight:600">${m.count}次</span></span>`
  ).join('');

  document.getElementById('statsCards').innerHTML = `
    <div class="card"><div class="label">被提及</div><div class="value">${d.mention_count || 0}</div></div>
    <div class="card"><div class="label">提及者</div><div class="value">${mentioners.length}</div></div>
    <div class="card"><div class="label">涉及平台</div><div class="value">${(d.platforms_seen||[]).length}</div></div>
    <div class="card" style="cursor:pointer;border-color:#238636" onclick="addToWatchlist('${d.name.replace(/'/g,"\\\\'")}','${d.slug}')">
      <div class="label" style="color:#238636">操作</div>
      <div class="value" style="font-size:16px;color:#238636">➕ 加入追踪</div>
    </div>`;

  document.getElementById('identityGrid').innerHTML = '';

  const grid2Left = document.querySelector('#resultArea .grid2 .panel:first-child');
  const grid2Right = document.querySelector('#resultArea .grid2 .panel:last-child');

  if (grid2Left) {
    grid2Left.innerHTML = `
      <div class="section-title">👥 被谁提及</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">${mentionHtml || '<span style="color:#8c8172;font-size:13px">暂无提及数据</span>'}</div>`;
  }
  if (grid2Right) {
    grid2Right.innerHTML = `
      <div class="section-title">🧠 简要分析</div>
      <div class="viewpoints">
        <p>${d.bio || '暂无足够信息'}</p>
        ${mentioners.length > 0 ? '<p><strong>关联人物：</strong>' + mentioners.map(m => m.name).join('、') + '</p>' : ''}
        ${(d.platforms_seen||[]).length > 0 ? '<p><strong>出现平台：</strong>' + d.platforms_seen.join('、') + '</p>' : ''}
        <p style="color:#8c8172;font-size:12px;margin-top:12px">💡 此人尚未加入追踪列表，仅显示其他监控人物的提及数据。点击上方"加入追踪"可启用完整分析。</p>
      </div>`;
  }

  document.getElementById('network').innerHTML = '<div style="color:#8c8172;padding:40px;text-align:center">加入追踪后可查看关系网络</div>';
  document.getElementById('networkDetail').style.display = 'none';

  const tl = d.timeline || [];
  const tlEl = document.getElementById('timeline');
  if (tl.length === 0) {
    tlEl.innerHTML = '<div style="color:#8c8172;font-size:13px;padding:20px;text-align:center">暂无相关内容</div>';
  } else {
    tlEl.innerHTML = tl.map(t => {
      const color = COLORS[t.platform] || '#8c8172';
      const label = PLAT_LABELS[t.platform] || t.platform;
      const date = t.published_at ? new Date(t.published_at).toLocaleDateString('zh-CN') : '';
      const src = t.source ? `<span style="font-size:11px;color:#8c8172;margin-left:6px">via @${t.source}</span>` : '';
      const link = t.url ? `<a href="${t.url}" target="_blank">${t.title || '(无标题)'}</a>` : (t.title || '(无标题)');
      return `<div class="tl-item">
        <div class="tl-dot" style="background:${color}"></div>
        <div class="tl-content">
          <div class="tl-meta"><span class="tl-badge" style="background:${color}">${label}</span><span class="tl-date">${date}</span>${src}</div>
          <div class="tl-title">${link}</div>
          <div class="tl-stats">${t.engagement_views ? '👁 '+fmt(t.engagement_views)+' ' : ''}${t.engagement_likes ? '❤️ '+fmt(t.engagement_likes) : ''}</div>
        </div>
      </div>`;
    }).join('');
  }
}

async function addToWatchlist(name, slug) {
  if (!confirm('将 ' + name + ' 加入追踪列表？')) return;
  try {
    const r = await fetch('/api/manage/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, slug: slug, priority: 'low', tags: ['AI']})
    });
    const d = await r.json();
    if (d.error) { alert('添加失败: ' + d.error); return; }
    alert('已加入追踪！下次运行采集后即可获取完整分析。');
    loadPeople();
  } catch(e) { alert('请求失败: ' + e.message); }
}

function renderResult(d) {
  if (d.lightweight) {
    renderLightweightResult(d);
    return;
  }
  // Header
  let tags = [];
  try { tags = JSON.parse(d.tags || '[]'); } catch(e) {}
  const priCls = d.priority === 'high' ? 'priority-high' : d.priority === 'medium' ? 'priority-medium' : 'priority-normal';
  document.getElementById('profileHeader').innerHTML = `
    <div style="flex:1;min-width:200px">
      <div class="profile-name">${d.name} <span class="priority-badge ${priCls}">${d.priority}</span></div>
      <div class="profile-slug">@${d.slug}</div>
      ${d.bio ? `<div class="profile-bio">${d.bio}</div>` : ''}
      <div style="margin-top:8px">${tags.map(t => `<span class="tag tag-topic">${t}</span>`).join('')}</div>
    </div>
    <div style="text-align:right;color:#8c8172;font-size:12px">
      分析时间<br>${new Date(d.generated_at).toLocaleString('zh-CN')}
    </div>`;

  // Stats cards
  const inf = d.influence || {};
  document.getElementById('statsCards').innerHTML = `
    <div class="card"><div class="label">采集帖子</div><div class="value">${inf.total_posts || 0}</div></div>
    <div class="card"><div class="label">覆盖平台</div><div class="value">${inf.platform_count || 0}</div></div>
    <div class="card"><div class="label">平均点赞</div><div class="value">${fmt(inf.avg_likes)}</div></div>
    <div class="card"><div class="label">平均播放</div><div class="value">${fmt(inf.avg_views)}</div></div>`;

  // Identity grid — show top relevant platforms, collapse the rest
  const idGrid = document.getElementById('identityGrid');
  const IMPORTANT_PLATFORMS = ['twitter','github','youtube','bilibili','weibo','reddit','hackernews','linkedin','medium','substack','producthunt','kaggle','hackernoon.com','dev community','docker hub','stackoverflow'];
  const NOISE_PLATFORMS = ['adultfriendfinder','livemaster','kaskus','mercadolivre','osu!','pling','skoob','splice','traktrain','xvideos','youporn','pornhub','redtube','xhamster','spankbang','motherless','tube8','cam4','chaturbate','bongacams','stripchat','flirt4free','livejasmin','myfreecams','freelancer','fiverr','ebay','etsy','imgur','9gag','ifunny','memrise','duolingo','letterboxd','last.fm','myanimelist','anilist','goodreads','bookmate','livelib','wattpad','quotev','tapas','webtoon','tiktok','kwai','likee','triller'];
  if (d.identity && d.identity.length > 0) {
    // Filter out noise platforms and low confidence
    const filtered = d.identity.filter(id => {
      const plat = id.platform.toLowerCase();
      if (NOISE_PLATFORMS.some(n => plat.includes(n))) return false;
      return true;
    });
    const sorted = [...filtered].sort((a,b) => {
      const aImp = IMPORTANT_PLATFORMS.indexOf(a.platform.toLowerCase()) >= 0 ? 0 : 1;
      const bImp = IMPORTANT_PLATFORMS.indexOf(b.platform.toLowerCase()) >= 0 ? 0 : 1;
      return aImp - bImp || (b.confidence||0) - (a.confidence||0);
    });
    const top = sorted.slice(0, 8);
    const rest = sorted.slice(8);
    const renderCard = (id) => {
      const color = COLORS[id.platform] || '#8c8172';
      const label = PLAT_LABELS[id.platform] || id.platform;
      const link = id.platform_url ? `<a href="${id.platform_url}" target="_blank">${id.platform_username || '—'}</a>` : (id.platform_username || '—');
      return `<div class="id-card">
        <div class="id-platform" style="background:${color}">${label.charAt(0).toUpperCase()}</div>
        <div class="id-info">
          <div class="id-username">${link}</div>
          <div class="id-confidence">${label} · 置信度 ${Math.round((id.confidence || 0) * 100)}%</div>
        </div>
      </div>`;
    };
    let html = top.map(renderCard).join('');
    if (rest.length > 0) {
      html += `<div id="moreIdentities" style="display:none">${rest.map(renderCard).join('')}</div>`;
      html += `<div style="grid-column:1/-1;text-align:center;padding:8px"><a href="#" onclick="document.getElementById('moreIdentities').style.display=document.getElementById('moreIdentities').style.display==='none'?'grid':'none';this.textContent=document.getElementById('moreIdentities').style.display==='none'?'展开其他 ${rest.length} 个平台 ▼':'收起 ▲';return false" style="font-size:13px;color:#d44536">展开其他 ${rest.length} 个平台 ▼</a></div>`;
    }
    idGrid.innerHTML = html;
  } else {
    idGrid.innerHTML = '<div style="color:#8c8172;font-size:13px">暂无跨平台身份数据（可运行 Maigret 扫描获取）</div>';
  }

  // Platform chart
  const bp = inf.by_platform || {};
  const platNames = Object.keys(bp);
  if (platNames.length > 0) {
    const ctx = document.getElementById('platChart').getContext('2d');
    if (platChartInst) platChartInst.destroy();
    platChartInst = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: platNames.map(p => PLAT_LABELS[p] || p),
        datasets: [
          {label:'帖子', data: platNames.map(p => bp[p].count), backgroundColor: platNames.map(p => COLORS[p] || '#8c8172'), borderRadius:4},
          {label:'点赞', data: platNames.map(p => bp[p].likes), backgroundColor: '#7faa6e', borderRadius:4},
        ]
      },
      options: {
        plugins:{legend:{labels:{color:'#2d2a26',font:{size:11}}}},
        scales:{x:{ticks:{color:'#8c8172'},grid:{color:'#f5f0eb'}},y:{ticks:{color:'#8c8172'},grid:{color:'#f5f0eb'}}}
      }
    });
  }

  // Relationship network
  renderNetwork(d.name, d.relationships || {});

  // Viewpoints
  const vp = d.viewpoints || '暂无分析';
  document.getElementById('viewpoints').innerHTML = renderMarkdown(vp);

  // Timeline
  const tl = d.timeline || [];
  const tlEl = document.getElementById('timeline');
  if (tl.length === 0) {
    tlEl.innerHTML = '<div style="color:#8c8172;padding:20px">暂无时间线数据</div>';
  } else {
    const TIMELINE_LIMIT = 20;
    const visible = tl.slice(0, TIMELINE_LIMIT);
    const hidden = tl.slice(TIMELINE_LIMIT);
    const renderItem = (t) => {
      const color = COLORS[t.platform] || '#8c8172';
      const title = t.title || (t.content || '').slice(0, 80) || '(无内容)';
      const link = t.url ? `<a href="${t.url}" target="_blank">${title}</a>` : title;
      const stats = [];
      if (t.engagement_views) stats.push('👁 ' + fmt(t.engagement_views));
      if (t.engagement_likes) stats.push('❤️ ' + fmt(t.engagement_likes));
      const date = t.published_at ? new Date(t.published_at).toLocaleDateString('zh-CN') : '';
      return `<div class="tl-item">
        <div class="tl-dot" style="background:${color}"></div>
        <div class="tl-content">
          <div class="tl-meta">
            <span class="tl-badge" style="background:${color}">${t.platform}</span>
            <span class="tl-date">${date}</span>
          </div>
          <div class="tl-title">${link}</div>
          ${stats.length ? `<div class="tl-stats">${stats.join(' ')}</div>` : ''}
        </div>
      </div>`;
    };
    let html = visible.map(renderItem).join('');
    if (hidden.length > 0) {
      html += `<div id="hiddenTimeline" style="display:none">${hidden.map(renderItem).join('')}</div>`;
      html += `<div style="text-align:center;padding:12px"><a href="#" onclick="const el=document.getElementById('hiddenTimeline');el.style.display=el.style.display==='none'?'block':'none';this.textContent=el.style.display==='none'?'展开更多 ${hidden.length} 条 ▼':'收起 ▲';return false" style="font-size:13px;color:#d44536;text-decoration:none">展开更多 ${hidden.length} 条 ▼</a></div>`;
    }
    tlEl.innerHTML = html;
  }
}

function renderNetwork(centerName, rel) {
  const interactions = rel.top_interactions || {};
  const mentions = rel.mentions || {};
  const retweets = rel.retweets || {};
  const names = Object.keys(interactions);

  if (names.length === 0) {
    document.getElementById('network').innerHTML = '<div style="color:#8c8172;padding:60px;text-align:center">暂无关系数据<br><span style="font-size:12px">Twitter/微博内容中的@提及和转发会自动提取</span></div>';
    return;
  }

  const maxCount = Math.max(...Object.values(interactions), 1);
  const nodes = [{id: 0, label: centerName, color: {background:'#d44536',border:'#b32d24'}, font:{color:'#1d1b19',size:16,bold:true}, size: 35, shape:'dot'}];
  const edges = [];
  const nodeMap = {};

  names.forEach((name, i) => {
    const count = interactions[name];
    const isMention = (mentions[name] || 0) > 0;
    const isRT = (retweets[name] || 0) > 0;
    let nodeColor = '#8c8172';
    if (isRT && isMention) nodeColor = '#d4a04a';
    else if (isRT) nodeColor = '#d44536';
    else if (isMention) nodeColor = '#7faa6e';
    const sz = 10 + (count / maxCount) * 25;

    nodeMap[i + 1] = {
      name: name,
      mentions: mentions[name] || 0,
      retweets: retweets[name] || 0,
      total: count,
      type: (isRT && isMention) ? '提及+转发' : isRT ? '转发' : isMention ? '提及' : '互动'
    };

    nodes.push({
      id: i + 1,
      label: name,
      color: {background: nodeColor, border: nodeColor},
      font: {color:'#2d2a26', size: 12},
      size: sz,
      shape: 'dot',
      title: `${name}\n提及: ${mentions[name]||0} 次\n转发: ${retweets[name]||0} 次\n\n点击查看详情`
    });
    edges.push({
      from: 0, to: i + 1,
      width: 1 + (count / maxCount) * 5,
      color: {color: nodeColor + '66', highlight: nodeColor},
      smooth: {type:'continuous'}
    });
  });

  const container = document.getElementById('network');
  container.innerHTML = '';
  const data = {nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges)};
  const options = {
    physics: {
      forceAtlas2Based: {gravitationalConstant: -50, centralGravity: 0.005, springLength: 140},
      solver: 'forceAtlas2Based',
      stabilization: {iterations: 100}
    },
    interaction: {hover: true, tooltipDelay: 100, navigationButtons: false, zoomView: true},
    nodes: {borderWidth: 2},
    edges: {smooth: {type:'continuous'}}
  };
  const network = new vis.Network(container, data, options);

  network.on('click', function(params) {
    const detail = document.getElementById('networkDetail');
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0];
      if (nodeId === 0) {
        detail.style.display = 'none';
        return;
      }
      const info = nodeMap[nodeId];
      if (!info) return;
      const typeColor = info.type === '提及+转发' ? '#d4a04a' : info.type === '转发' ? '#d44536' : '#7faa6e';
      const contextLine = info.type === '转发' 
        ? `${centerName} 转发了此人 ${info.retweets} 条内容`
        : info.type === '提及+转发'
        ? `${centerName} 提及此人 ${info.mentions} 次，转发 ${info.retweets} 次`
        : `${centerName} 提及此人 ${info.mentions} 次`;
      detail.style.display = 'block';
      detail.innerHTML = `
        <div class="nd-name">@${info.name} <span style="font-size:12px;color:${typeColor};border:1px solid ${typeColor}44;padding:2px 8px;border-radius:10px;background:${typeColor}11">${info.type}</span></div>
        <div class="nd-stats">提及 ${info.mentions} 次 · 转发 ${info.retweets} 次 · 总互动 ${info.total} 次</div>
        <div class="nd-bio" style="color:#4a4642;font-size:13px;margin:6px 0;line-height:1.5">${contextLine}</div>
        <div class="nd-actions">
          <a href="/intel" onclick="localStorage.setItem('intelSearch','${info.name}')">🔍 查看情报分析</a>
          <a href="https://twitter.com/${info.name}" target="_blank">🐦 Twitter 主页</a>
          <a href="https://github.com/${info.name}" target="_blank">💻 GitHub</a>
        </div>`;
      detail.scrollIntoView({behavior:'smooth', block:'nearest'});
    } else {
      detail.style.display = 'none';
    }
  });

  network.on('hoverNode', function() { container.style.cursor = 'pointer'; });
  network.on('blurNode', function() { container.style.cursor = 'default'; });
}

function renderMarkdown(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^\- (.+)$/gm, '<li>$1</li>')
    .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/<\/ul>\s*<ul>/g, '')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
}

loadPeople();

// Auto-search if redirected from discover page
const savedSearch = localStorage.getItem('intelSearch');
if (savedSearch) {
  localStorage.removeItem('intelSearch');
  document.getElementById('searchInput').value = savedSearch;
  doSearch();
}
</script>
</body>
</html>"""


DISCOVER_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSINT 发现推荐</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fdfbf7;color:#2d2a26;font-family:"PingFang SC", "Hiragino Sans GB", -apple-system;padding:20px}
a{color:#d44536;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.header h1{font-size:22px;color:#d44536;font-family:\"Songti SC\", \"Hiragino Mincho ProN\", serif}
.nav-btn{color:#d44536;font-size:13px;border:1px solid #e8dfd0;padding:6px 14px;border-radius:6px;background:#ffffff;text-decoration:none}
.nav-btn:hover{background:#f5f0eb;text-decoration:none}
.controls{display:flex;gap:12px;margin-bottom:24px;align-items:center;flex-wrap:wrap}
.controls select,.controls input{padding:8px 12px;background:#ffffff;border:1px solid #e8dfd0;border-radius:6px;color:#1d1b19;font-size:13px}
.controls button{padding:8px 18px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:13px;cursor:pointer;font-weight:600}
.controls button:hover{background:#2ea043}
.controls button:disabled{opacity:.5;cursor:not-allowed}
.loading{text-align:center;padding:40px;color:#8c8172}
.spinner{display:inline-block;width:20px;height:20px;border:3px solid #e8dfd0;border-top-color:#d44536;border-radius:50%;animation:spin .8s linear infinite;margin-right:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.hidden{display:none}
.summary{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:16px;margin-bottom:20px;display:flex;gap:24px;flex-wrap:wrap}
.summary .stat{text-align:center}
.summary .stat .num{font-size:28px;font-weight:700;color:#1d1b19}
.summary .stat .lbl{font-size:12px;color:#8c8172;margin-top:2px}
.candidate-list{display:flex;flex-direction:column;gap:12px}
.candidate{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:16px;display:flex;align-items:flex-start;gap:16px;transition:border-color .2s}
.candidate:hover{border-color:#d44536}
.candidate .rank{font-size:20px;font-weight:700;color:#8c8172;width:30px;text-align:center;flex-shrink:0;margin-top:4px}
.candidate .info{flex:1}
.candidate .username{font-size:16px;font-weight:600;color:#1d1b19}
.candidate .meta{font-size:12px;color:#8c8172;margin-top:4px;display:flex;gap:12px;flex-wrap:wrap}
.candidate .note{font-size:13px;color:#7faa6e;margin-top:6px;font-style:italic}
.candidate .contexts{margin-top:8px;font-size:12px;color:#8c8172;line-height:1.5}
.candidate .contexts span{display:block;padding:2px 0;border-bottom:1px solid #f5f0eb}
.plat-badge{font-size:11px;padding:2px 8px;border-radius:10px;color:#fff;font-weight:600}
.by-badge{font-size:11px;padding:2px 8px;border-radius:10px;background:#e8dfd0;color:#2d2a26}
</style>
</head>
<body>
<div class="header">
  <h1>🌐 发现推荐</h1>
  <span style="color:#8c8172;font-size:13px">基于已采集内容中@提及和转发关系，自动发现值得关注的新人物</span>
  <a href="/" class="nav-btn" style="margin-left:auto">🛰️ 返回总览</a>
</div>

<div class="controls">
  <label style="font-size:13px;color:#8c8172">扫描天数:</label>
  <input type="number" id="scanDays" value="30" min="1" max="90" style="width:70px">
  <label style="font-size:13px;color:#8c8172">最低提及次数:</label>
  <input type="number" id="minCount" value="2" min="1" max="20" style="width:60px">
  <label style="font-size:13px;color:#8c8172">LLM过滤:</label>
  <select id="useLLM"><option value="0">关闭（快速）</option><option value="1">开启（精准）</option></select>
  <button id="scanBtn" onclick="doScan()">🔍 扫描</button>
</div>

<div id="loadingArea" class="loading hidden">
  <span class="spinner"></span>正在扫描...
</div>

<div id="resultArea" class="hidden">
  <div class="summary" id="summary"></div>
  <div class="candidate-list" id="candidateList"></div>
</div>

<script>
const PLAT_COLORS = {twitter:'#1DA1F2',weibo:'#E6162D',bilibili:'#FB7299',youtube:'#FF0000',reddit:'#FF5700',hackernews:'#FF6600',github:'#238636'};

async function doScan() {
  const days = document.getElementById('scanDays').value;
  const minCount = document.getElementById('minCount').value;
  const useLLM = document.getElementById('useLLM').value;
  const btn = document.getElementById('scanBtn');

  btn.disabled = true;
  document.getElementById('loadingArea').classList.remove('hidden');
  document.getElementById('resultArea').classList.add('hidden');

  try {
    const r = await fetch(`/api/discover?days=${days}&min_count=${minCount}&use_llm=${useLLM}`);
    const d = await r.json();
    renderResults(d);
    document.getElementById('resultArea').classList.remove('hidden');
  } catch(e) {
    alert('扫描失败: ' + e.message);
  } finally {
    btn.disabled = false;
    document.getElementById('loadingArea').classList.add('hidden');
  }
}

function renderResults(d) {
  document.getElementById('summary').innerHTML = `
    <div class="stat"><div class="num">${d.days_scanned || 30}</div><div class="lbl">扫描天数</div></div>
    <div class="stat"><div class="num">${d.total_candidates || 0}</div><div class="lbl">总候选人</div></div>
    <div class="stat"><div class="num">${(d.recommendations || []).length}</div><div class="lbl">推荐关注</div></div>
  `;

  const list = document.getElementById('candidateList');
  const recs = d.recommendations || [];
  if (recs.length === 0) {
    list.innerHTML = '<div style="text-align:center;padding:40px;color:#8c8172">暂无推荐 — 尝试降低最低提及次数或增加扫描天数</div>';
    return;
  }

  list.innerHTML = recs.map((r, i) => {
    const platforms = (r.platforms || []).map(p => `<span class="plat-badge" style="background:${PLAT_COLORS[p]||'#8c8172'}">${p}</span>`).join(' ');
    const mentionedBy = (r.mentioned_by || []).map(b => `<span class="by-badge">${b}</span>`).join(' ');
    const contexts = (r.sample_contexts || []).map(c => `<span>${c.length > 100 ? c.slice(0,100)+'...' : c}</span>`).join('');
    const note = r.relevance_note ? `<div class="note">💡 ${r.relevance_note}</div>` : '';

    // Build profile links for each platform
    const profileLinks = (r.platforms || []).map(p => {
      let url = '#';
      if (p === 'twitter') url = 'https://twitter.com/' + r.username;
      else if (p === 'github') url = 'https://github.com/' + r.username;
      else if (p === 'reddit') url = 'https://reddit.com/user/' + r.username;
      else return '';
      return `<a href="${url}" target="_blank" style="font-size:12px;color:#d44536;margin-left:6px">${p}主页 ↗</a>`;
    }).filter(Boolean).join(' ');
    const intelLink = `<a href="/intel" onclick="localStorage.setItem('intelSearch','${r.username}');return true" style="font-size:12px;color:#4a5c80;margin-left:6px">情报分析 →</a>`;

    return `<div class="candidate">
      <div class="rank">#${i+1}</div>
      <div class="info">
        <div class="username">@${r.username} ${profileLinks} ${intelLink}</div>
        <div class="meta">
          <span>被提及 <strong>${r.count}</strong> 次</span>
          ${platforms}
          ${mentionedBy}
        </div>
        ${note}
        ${contexts ? `<div class="contexts">${contexts}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}
</script>
</body>
</html>"""


REPORTS_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSINT 情报周报</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fdfbf7;color:#2d2a26;font-family:"PingFang SC", "Hiragino Sans GB", -apple-system;padding:20px}
a{color:#d44536;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.header h1{font-size:22px;color:#d44536;font-family:\"Songti SC\", \"Hiragino Mincho ProN\", serif}
.nav-btn{color:#d44536;font-size:13px;border:1px solid #e8dfd0;padding:6px 14px;border-radius:6px;background:#ffffff;text-decoration:none}
.nav-btn:hover{background:#f5f0eb;text-decoration:none}
.report-list{display:flex;flex-direction:column;gap:12px;margin-bottom:24px}
.report-card{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:16px;cursor:pointer;transition:border-color .2s}
.report-card:hover{border-color:#d44536}
.report-card.active{border-color:#d44536;background:#1f6feb11}
.report-title{font-size:15px;font-weight:600;color:#1d1b19}
.report-date{font-size:12px;color:#8c8172;margin-top:4px}
.report-content{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:24px;font-size:14px;line-height:1.8;color:#2d2a26}
.report-content h1,.report-content h2,.report-content h3{color:#d44536;margin:16px 0 8px}
.report-content h1{font-size:18px}.report-content h2{font-size:15px}.report-content h3{font-size:14px}
.report-content table{border-collapse:collapse;width:100%;margin:12px 0}
.report-content th,.report-content td{padding:6px 12px;border:1px solid #e8dfd0;text-align:left;font-size:13px}
.report-content th{background:#f5f0eb;color:#8c8172;font-weight:600}
.report-content ul,.report-content ol{padding-left:20px;margin:8px 0}
.report-content li{margin:4px 0}
.report-content strong{color:#1d1b19}
.report-content p{margin:8px 0}
.report-content a{color:#d44536}
.report-content blockquote{border-left:3px solid #e8dfd0;padding-left:12px;color:#8c8172;margin:8px 0}
.report-content hr{border:none;border-top:1px solid #e8dfd0;margin:16px 0}
.report-content em{color:#8c8172}
.empty{text-align:center;padding:60px;color:#8c8172}
.layout{display:grid;grid-template-columns:300px 1fr;gap:20px}
@media(max-width:900px){.layout{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 情报周报</h1>
  <span style="color:#8c8172;font-size:13px">自动生成的高优先级人物情报报告</span>
  <a href="/" class="nav-btn" style="margin-left:auto">🛰️ 返回总览</a>
</div>

<div class="layout">
  <div class="report-list" id="reportList">
    <div class="empty">加载中...</div>
  </div>
  <div class="report-content" id="reportContent">
    <div class="empty">← 选择一个报告查看</div>
  </div>
</div>

<script>
let reports = [];

function renderMd(text) {
  let result = text;
  // Handle tables: find consecutive lines with |
  result = result.replace(/(^\|.+\|$\n?)+/gm, function(tableBlock) {
    const lines = tableBlock.trim().split('\n');
    let html = '<table>';
    let isFirst = true;
    for (const line of lines) {
      const cells = line.split('|').filter(c => c.trim() !== '');
      if (cells.every(c => /^[\s\-:]+$/.test(c))) continue;
      const tag = isFirst ? 'th' : 'td';
      html += '<tr>' + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
      isFirst = false;
    }
    html += '</table>';
    return html;
  });

  return result
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/^---$/gm, '<hr>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/<\/ul>\s*<ul>/g, '')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
}

async function loadReports() {
  try {
    const r = await fetch('/api/reports');
    reports = await r.json();
    const list = document.getElementById('reportList');
    if (reports.length === 0) {
      list.innerHTML = '<div class="empty">暂无周报<br><span style="font-size:12px">运行 python scripts/run_daily.py --weekly 生成</span></div>';
      return;
    }
    list.innerHTML = reports.map((rpt, i) => {
      const date = new Date(rpt.created_at).toLocaleString('zh-CN');
      return `<div class="report-card" onclick="showReport(${i})" id="rcard-${i}">
        <div class="report-title">${rpt.title}</div>
        <div class="report-date">${date}</div>
      </div>`;
    }).join('');

    showReport(0);
  } catch(e) { console.error(e); }
}

function showReport(idx) {
  document.querySelectorAll('.report-card').forEach(c => c.classList.remove('active'));
  const card = document.getElementById('rcard-' + idx);
  if (card) card.classList.add('active');

  const rpt = reports[idx];
  if (rpt) {
    document.getElementById('reportContent').innerHTML = renderMd(rpt.content || '空报告');
  }
}

loadReports();
</script>
</body>
</html>"""


MANAGE_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OSINT 管理 - 添加人物</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fdfbf7;color:#2d2a26;font-family:"PingFang SC", "Hiragino Sans GB", -apple-system;padding:20px}
a{color:#d44536;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.header h1{font-size:22px;color:#d44536;font-family:\"Songti SC\", \"Hiragino Mincho ProN\", serif}
.nav-btn{color:#d44536;font-size:13px;border:1px solid #e8dfd0;padding:6px 14px;border-radius:6px;background:#ffffff;text-decoration:none}
.nav-btn:hover{background:#f5f0eb;text-decoration:none}
.panel{background:#ffffff;border:1px solid #e8dfd0;border-radius:8px;padding:20px;box-shadow:0 4px 16px rgba(45, 42, 38, 0.08);margin-bottom:20px}
.panel h2{font-size:15px;color:#d44536;margin-bottom:16px;font-weight:600}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group.full{grid-column:1/-1}
.form-group label{font-size:12px;color:#8c8172;text-transform:uppercase;letter-spacing:.5px}
.form-group input,.form-group select{padding:8px 12px;background:#fdfbf7;border:1px solid #e8dfd0;border-radius:6px;color:#1d1b19;font-size:13px;outline:none}
.form-group input:focus,.form-group select:focus{border-color:#d44536}
.submit-btn{padding:10px 24px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:14px;cursor:pointer;font-weight:600;margin-top:12px}
.submit-btn:hover{background:#2ea043}
.submit-btn:disabled{opacity:.5;cursor:not-allowed}
.msg{padding:10px 14px;border-radius:6px;margin-top:12px;font-size:13px;display:none}
.msg.ok{display:block;background:#23863633;border:1px solid #238636;color:#7faa6e}
.msg.err{display:block;background:#da361933;border:1px solid #d44536;color:#d44536}
.people-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.person-card{background:#fdfbf7;border:1px solid #e8dfd0;border-radius:8px;padding:14px;position:relative}
.person-card .name{font-size:15px;font-weight:600;color:#1d1b19}
.person-card .del-btn{position:absolute;top:8px;right:8px;background:transparent;border:1px solid #d4453666;color:#d44536;border-radius:4px;cursor:pointer;font-size:13px;padding:2px 6px;opacity:.6;transition:opacity .2s}
.person-card .del-btn:hover{opacity:1;background:#d4453622}
.person-card .meta{font-size:12px;color:#8c8172;margin-top:6px}
.person-card .tags{margin-top:6px;display:flex;gap:4px;flex-wrap:wrap}
.person-card .tag{font-size:11px;padding:2px 8px;border-radius:10px;background:#1f6feb33;color:#d44536;border:1px solid #1f6feb66}
.priority-high{color:#d44536}.priority-medium{color:#d4a04a}.priority-low{color:#8c8172}
@media(max-width:600px){.form-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <h1>⚙️ 人物管理</h1>
  <a href="/" class="nav-btn" style="margin-left:auto">🛰️ 返回总览</a>
</div>

<div class="panel">
  <h2>➕ 添加新人物</h2>
  <form id="addForm" onsubmit="return submitForm(event)">
    <div class="form-grid">
      <div class="form-group"><label>名称 *</label><input type="text" id="f_name" placeholder="如：Sam Altman" required></div>
      <div class="form-group"><label>优先级</label><select id="f_priority"><option value="high">high</option><option value="medium" selected>medium</option><option value="low">low</option></select></div>
      <div class="form-group"><label>B站UID</label><input type="text" id="f_bilibili" placeholder="数字UID"></div>
      <div class="form-group"><label>微博UID</label><input type="text" id="f_weibo" placeholder="数字UID"></div>
      <div class="form-group"><label>Twitter用户名</label><input type="text" id="f_twitter" placeholder="不含@"></div>
      <div class="form-group"><label>YouTube频道ID</label><input type="text" id="f_youtube" placeholder="频道ID或@handle"></div>
      <div class="form-group"><label>GitHub用户名</label><input type="text" id="f_github" placeholder="用户名"></div>
      <div class="form-group"><label>Reddit用户名</label><input type="text" id="f_reddit" placeholder="用户名"></div>
      <div class="form-group full"><label>标签（逗号分隔）</label><input type="text" id="f_tags" placeholder="AI,创业,投资"></div>
    </div>
    <button type="submit" class="submit-btn" id="submitBtn">添加到监控列表</button>
    <div class="msg" id="formMsg"></div>
  </form>
</div>

<div class="panel">
  <h2>👥 当前监控列表</h2>
  <div class="people-grid" id="peopleGrid">加载中...</div>
</div>

<script>
async function submitForm(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const msg = document.getElementById('formMsg');
  btn.disabled = true;
  msg.className = 'msg';
  msg.style.display = 'none';

  const data = {
    name: document.getElementById('f_name').value.trim(),
    bilibili: document.getElementById('f_bilibili').value.trim(),
    weibo: document.getElementById('f_weibo').value.trim(),
    twitter: document.getElementById('f_twitter').value.trim(),
    youtube: document.getElementById('f_youtube').value.trim(),
    github: document.getElementById('f_github').value.trim(),
    reddit: document.getElementById('f_reddit').value.trim(),
    priority: document.getElementById('f_priority').value,
    tags: document.getElementById('f_tags').value.trim()
  };

  try {
    const r = await fetch('/api/manage/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    const result = await r.json();
    if (result.error) {
      msg.className = 'msg err'; msg.textContent = result.error; msg.style.display = 'block';
    } else {
      msg.className = 'msg ok'; msg.textContent = '✅ 已添加 ' + result.name + '，平台: ' + result.platforms.join(', '); msg.style.display = 'block';
      document.getElementById('addForm').reset();
      loadPeople();
    }
  } catch(err) {
    msg.className = 'msg err'; msg.textContent = '请求失败: ' + err.message; msg.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
  return false;
}

async function deletePerson(name) {
  if (!confirm('确定要删除 ' + name + ' 吗？')) return;
  try {
    const r = await fetch('/api/manage/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    });
    const result = await r.json();
    if (result.error) { alert(result.error); }
    else { loadPeople(); }
  } catch(e) { alert('删除失败: ' + e.message); }
}

async function loadPeople() {  try {
    const r = await fetch('/api/people');
    const people = await r.json();
    const grid = document.getElementById('peopleGrid');
    if (!people.length) { grid.innerHTML = '<div style="color:#8c8172">暂无监控人物</div>'; return; }
    grid.innerHTML = people.map(p => {
      let tags = [];
      try { tags = JSON.parse(p.tags || '[]'); } catch(e) {}
      const priClass = 'priority-' + (p.priority || 'low');
      return `<div class="person-card">
        <button class="del-btn" onclick="deletePerson('${p.name.replace(/'/g,"\\\\'")}')" title="删除">🗑️</button>
        <div class="name">${p.name} <span class="${priClass}" style="font-size:11px;font-weight:600">[${p.priority}]</span></div>
        <div class="meta">@${p.slug}</div>
        ${tags.length ? '<div class="tags">' + tags.map(t => '<span class="tag">' + t + '</span>').join('') + '</div>' : ''}
      </div>`;
    }).join('');
  } catch(e) { console.error(e); }
}
loadPeople();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/data":
            self._json_response(api_data())
        elif path == "/api/people":
            self._json_response(api_people())
        elif path == "/api/search":
            query = qs.get("q", [""])[0].strip()
            days = int(qs.get("days", ["0"])[0])
            platform = qs.get("platform", [""])[0].strip()
            person = qs.get("person", [""])[0].strip()
            limit = int(qs.get("limit", ["50"])[0])
            self._json_response(api_search(query, days, platform, person, limit))
        elif path == "/api/discover":
            days = int(qs.get("days", ["30"])[0])
            min_count = int(qs.get("min_count", ["2"])[0])
            use_llm = qs.get("use_llm", ["0"])[0] == "1"
            try:
                result = discover_new_people(
                    days=days, min_count=min_count, use_llm=use_llm
                )
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        elif path == "/api/reports":
            reports = list_reports(limit=50)
            self._json_response(reports)
        elif path.startswith("/api/report/"):
            try:
                report_id = int(path.split("/")[-1])
                report = get_report(report_id)
                if report:
                    self._json_response(report)
                else:
                    self._json_response({"error": "报告不存在"}, 404)
            except ValueError:
                self._json_response({"error": "无效的报告ID"}, 400)
        elif path == "/api/compare":
            slugs = qs.get("slugs", [""])[0].split(",")
            slugs = [s.strip() for s in slugs if s.strip()]
            if len(slugs) < 2:
                self._json_response({"error": "请提供至少2个人物slug (逗号分隔)"}, 400)
            else:
                self._json_response(api_compare(slugs))
        elif path == "/api/intel":
            name = qs.get("name", [""])[0].strip()
            if not name:
                self._json_response({"error": "请提供人物名称 (name 参数)"}, 400)
            else:
                try:
                    result = run_profile_intel(name)
                    self._json_response(result)
                except Exception as e:
                    self._json_response({"error": str(e)}, 500)
        elif path == "/api/trends":
            self._json_response(api_trends())
        elif path == "/api/alerts/recent":
            self._json_response(api_alerts_recent())
        elif path == "/api/staleness":
            self._json_response(api_staleness())
        elif path == "/intel":
            self._html_response(INTEL_PAGE)
        elif path == "/discover":
            self._html_response(DISCOVER_PAGE)
        elif path == "/reports":
            self._html_response(REPORTS_PAGE)
        elif path == "/manage":
            self._html_response(MANAGE_PAGE)
        else:
            self._html_response(HTML_PAGE)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if path == "/api/manage/add":
            try:
                data = json.loads(body.decode("utf-8"))
                result = add_person_to_watchlist(data)
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        elif path == "/api/manage/remove":
            try:
                data = json.loads(body.decode("utf-8"))
                result = remove_person_from_watchlist(data)
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)
        else:
            self._json_response({"error": "未知接口"}, 404)

    def log_message(self, format, *args):
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"🛰️  OSINT Dashboard: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止服务")
        server.server_close()


if __name__ == "__main__":
    main()
