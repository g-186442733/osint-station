"""
OSINT 工作站 — 采集管线
职责：遍历 watchlist → 调用收集器 → 存入 SQLite（自动去重）
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from config.settings import DB_PATH, WATCHLIST_PATH

logger = logging.getLogger(__name__)


def _get_weibo_cookies():
    try:
        from config.settings import WEIBO_COOKIES

        return WEIBO_COOKIES
    except ImportError:
        return ""


def _get_collector(platform: str):
    if platform == "bilibili":
        from collectors.bilibili.collector import BilibiliCollector

        return BilibiliCollector(delay=5.0)
    elif platform == "weibo":
        from collectors.weibo.collector import WeiboCollector

        return WeiboCollector(delay=3.0, cookies=_get_weibo_cookies())
    elif platform == "twitter":
        from collectors.twitter.collector import TwitterCollector

        return TwitterCollector(delay=3.0)
    elif platform == "youtube":
        from collectors.youtube.collector import YouTubeCollector

        return YouTubeCollector(delay=2.0)
    elif platform == "reddit":
        from collectors.reddit.collector import RedditCollector

        return RedditCollector(delay=2.0)
    elif platform == "hackernews":
        from collectors.hackernews.collector import HackerNewsCollector

        return HackerNewsCollector(delay=0.5)
    elif platform == "github":
        from collectors.github.collector import GitHubCollector

        return GitHubCollector(delay=1.0)
    return None


def load_watchlist() -> dict:
    """加载关注列表"""
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def store_posts(posts: list[dict], profile_slug: str, db: sqlite3.Connection) -> int:
    """存储帖子到 posts 表，返回新增数量"""
    new_count = 0
    for post in posts:
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO posts
                (profile_slug, platform, platform_id, title, content, url,
                 published_at, engagement_likes, engagement_comments,
                 engagement_shares, engagement_views, raw_json, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    profile_slug,
                    post["platform"],
                    post["platform_id"],
                    post.get("title", ""),
                    post.get("content", ""),
                    post.get("url", ""),
                    post.get("published_at", ""),
                    post.get("engagement_likes", 0),
                    post.get("engagement_comments", 0),
                    post.get("engagement_shares", 0),
                    post.get("engagement_views", 0),
                    post.get("raw_json", ""),
                    datetime.now().isoformat(),
                ),
            )
            if db.execute("SELECT changes()").fetchone()[0] > 0:
                new_count += 1
        except sqlite3.IntegrityError:
            pass  # 去重：同平台同 ID 已存在
        except Exception as e:
            logger.error(f"存储帖子失败: {e}")
    return new_count


def collect_person(person: dict, db: sqlite3.Connection) -> dict:
    """采集单个人物的所有平台数据"""
    name = person["name"]
    slug = name.lower().replace(" ", "_")
    results = {"name": name, "slug": slug, "platforms": {}}

    usernames = person.get("usernames", {})
    for platform, uid in usernames.items():
        if not uid:
            continue

        collector = _get_collector(platform)
        if not collector:
            logger.warning(f"[{name}] 不支持的平台: {platform}")
            results["platforms"][platform] = {"status": "unsupported"}
            continue

        try:
            posts = collector.collect(uid)
            new_count = store_posts(posts, slug, db)
            db.commit()

            results["platforms"][platform] = {
                "status": "ok",
                "total": len(posts),
                "new": new_count,
            }
            logger.info(
                f"[{name}] {platform}: 获取 {len(posts)} 条，新增 {new_count} 条"
            )

        except Exception as e:
            logger.error(f"[{name}] {platform} 采集失败: {e}")
            results["platforms"][platform] = {"status": "error", "error": str(e)}

    # 更新 profiles 最后更新时间
    db.execute(
        """
        UPDATE profiles SET last_updated = ? WHERE slug = ?
    """,
        (datetime.now().isoformat(), slug),
    )
    db.commit()

    return results


def run_collection(people_filter: list[str] | None = None) -> list[dict]:
    watchlist = load_watchlist()
    people = watchlist.get("people", [])

    if people_filter:
        filter_lower = [n.lower() for n in people_filter]
        people = [p for p in people if p["name"].lower() in filter_lower]

    if not people:
        logger.warning("关注列表为空或过滤后无匹配")
        return []

    db = sqlite3.connect(str(DB_PATH))
    all_results = []

    logger.info(f"开始采集 {len(people)} 个人物...")

    for person in people:
        result = collect_person(person, db)
        all_results.append(result)

    subreddits = watchlist.get("subreddits", [])
    if subreddits and not people_filter:
        logger.info(f"开始采集 {len(subreddits)} 个Reddit子版...")
        for sub in subreddits:
            result = _collect_subreddit(sub, db)
            all_results.append(result)

    hn_config = watchlist.get("hackernews", {})
    story_types = hn_config.get("story_types", [])
    if story_types and not people_filter:
        logger.info(f"开始采集 Hacker News {story_types}...")
        for st in story_types:
            result = _collect_hn_stories(st, db)
            all_results.append(result)

    topics = watchlist.get("topics", [])
    if topics and not people_filter:
        logger.info(f"开始话题关键词采集 ({len(topics)} 个话题)...")
        for topic in topics:
            result = _collect_topic_keyword(topic, db)
            all_results.append(result)

    db.close()

    total_new = sum(
        p_info.get("new", 0)
        for r in all_results
        for p_info in r.get("platforms", {}).values()
    )
    logger.info(f"采集完成，共新增 {total_new} 条内容")

    return all_results


def _collect_subreddit(subreddit: str, db: sqlite3.Connection) -> dict:
    slug = f"r/{subreddit}".lower()
    result = {"name": f"r/{subreddit}", "slug": slug, "platforms": {}}

    collector = _get_collector("reddit")
    if not collector:
        return result

    try:
        posts = collector.collect(f"r/{subreddit}")
        new_count = store_posts(posts, slug, db)
        db.commit()

        db.execute(
            "INSERT OR IGNORE INTO profiles (name, slug, tags, priority, first_seen, last_updated) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (f"r/{subreddit}", slug, '["reddit","subreddit"]', "medium"),
        )
        db.execute(
            "UPDATE profiles SET last_updated = ? WHERE slug = ?",
            (datetime.now().isoformat(), slug),
        )
        db.commit()

        result["platforms"]["reddit"] = {
            "status": "ok",
            "total": len(posts),
            "new": new_count,
        }
        logger.info(
            f"[r/{subreddit}] reddit: 获取 {len(posts)} 条，新增 {new_count} 条"
        )
    except Exception as e:
        logger.error(f"[r/{subreddit}] 采集失败: {e}")
        result["platforms"]["reddit"] = {"status": "error", "error": str(e)}

    return result


def _collect_hn_stories(story_type: str, db: sqlite3.Connection) -> dict:
    slug = f"hn_{story_type}"
    result = {"name": f"HN {story_type}", "slug": slug, "platforms": {}}

    collector = _get_collector("hackernews")
    if not collector:
        return result

    try:
        posts = collector.collect(story_type)
        new_count = store_posts(posts, slug, db)
        db.commit()

        db.execute(
            "INSERT OR IGNORE INTO profiles (name, slug, tags, priority, first_seen, last_updated) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (f"HN {story_type}", slug, '["hackernews"]', "medium"),
        )
        db.execute(
            "UPDATE profiles SET last_updated = ? WHERE slug = ?",
            (datetime.now().isoformat(), slug),
        )
        db.commit()

        result["platforms"]["hackernews"] = {
            "status": "ok",
            "total": len(posts),
            "new": new_count,
        }
        logger.info(
            f"[HN {story_type}] hackernews: 获取 {len(posts)} 条，新增 {new_count} 条"
        )
    except Exception as e:
        logger.error(f"[HN {story_type}] 采集失败: {e}")
        result["platforms"]["hackernews"] = {"status": "error", "error": str(e)}

    return result


def _collect_topic_keyword(topic: dict, db: sqlite3.Connection) -> dict:
    keyword = topic.get("keyword", "")
    platforms = topic.get("platforms", [])
    slug = f"topic_{keyword.lower().replace(' ', '_')}"
    result = {"name": f"话题: {keyword}", "slug": slug, "platforms": {}}

    if not keyword:
        return result

    db.execute(
        "INSERT OR IGNORE INTO profiles (name, slug, tags, priority, first_seen, last_updated) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            f"话题: {keyword}",
            slug,
            json.dumps(["topic", keyword], ensure_ascii=False),
            "medium",
        ),
    )
    db.commit()

    if "reddit" in platforms:
        try:
            from collectors.reddit.collector import RedditCollector
            import requests as _req
            from config.settings import MAX_POSTS_PER_USER

            PULLPUSH_API = "https://api.pullpush.io/reddit/search/submission"
            params = {
                "q": keyword,
                "sort": "desc",
                "sort_type": "created_utc",
                "size": min(MAX_POSTS_PER_USER, 30),
            }
            resp = _req.get(PULLPUSH_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            collector = RedditCollector(delay=0)
            posts = [collector._normalize(item) for item in data]
            new_count = store_posts(posts, slug, db)
            db.commit()
            result["platforms"]["reddit"] = {
                "status": "ok",
                "total": len(posts),
                "new": new_count,
            }
            logger.info(f"[话题:{keyword}] reddit: {len(posts)} 条，新增 {new_count}")
        except Exception as e:
            logger.error(f"[话题:{keyword}] reddit 搜索失败: {e}")
            result["platforms"]["reddit"] = {"status": "error", "error": str(e)}

    if "hackernews" in platforms:
        try:
            import requests as _req

            HN_SEARCH_API = "https://hn.algolia.com/api/v1/search_by_date"
            params = {"query": keyword, "tags": "story", "hitsPerPage": 30}
            resp = _req.get(HN_SEARCH_API, params=params, timeout=15)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            posts = []
            for h in hits:
                created_at = h.get("created_at", "")
                posts.append(
                    {
                        "platform": "hackernews",
                        "platform_id": f"hn_{h.get('objectID', '')}",
                        "title": h.get("title", "") or "",
                        "content": "",
                        "url": h.get("url", "")
                        or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
                        "published_at": created_at[:19] if created_at else "",
                        "engagement_likes": h.get("points", 0) or 0,
                        "engagement_comments": h.get("num_comments", 0) or 0,
                        "engagement_shares": 0,
                        "engagement_views": 0,
                        "raw_json": "",
                    }
                )
            new_count = store_posts(posts, slug, db)
            db.commit()
            result["platforms"]["hackernews"] = {
                "status": "ok",
                "total": len(posts),
                "new": new_count,
            }
            logger.info(
                f"[话题:{keyword}] hackernews: {len(posts)} 条，新增 {new_count}"
            )
        except Exception as e:
            logger.error(f"[话题:{keyword}] hackernews 搜索失败: {e}")
            result["platforms"]["hackernews"] = {"status": "error", "error": str(e)}

    if "youtube" in platforms:
        try:
            import scrapetube
            from config.settings import MAX_POSTS_PER_USER

            posts = []
            for i, video in enumerate(scrapetube.get_search(keyword)):
                if i >= min(MAX_POSTS_PER_USER, 20):
                    break
                vid = video.get("videoId", "")
                title_obj = video.get("title", {})
                title = ""
                if isinstance(title_obj, dict):
                    runs = title_obj.get("runs", [])
                    title = runs[0].get("text", "") if runs else ""
                else:
                    title = str(title_obj)

                desc_obj = video.get("descriptionSnippet", {})
                desc = ""
                if isinstance(desc_obj, dict):
                    runs = desc_obj.get("runs", [])
                    desc = "".join(r.get("text", "") for r in runs)

                view_text = video.get("viewCountText", {})
                views = 0
                if isinstance(view_text, dict):
                    simple = view_text.get("simpleText", "")
                    views = int("".join(c for c in simple if c.isdigit()) or "0")

                posts.append(
                    {
                        "platform": "youtube",
                        "platform_id": f"yt_{vid}",
                        "title": title,
                        "content": desc,
                        "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                        "published_at": "",
                        "engagement_likes": 0,
                        "engagement_comments": 0,
                        "engagement_shares": 0,
                        "engagement_views": views,
                        "raw_json": "",
                    }
                )
            new_count = store_posts(posts, slug, db)
            db.commit()
            result["platforms"]["youtube"] = {
                "status": "ok",
                "total": len(posts),
                "new": new_count,
            }
            logger.info(f"[话题:{keyword}] youtube: {len(posts)} 条，新增 {new_count}")
        except Exception as e:
            logger.error(f"[话题:{keyword}] youtube 搜索失败: {e}")
            result["platforms"]["youtube"] = {"status": "error", "error": str(e)}

    db.execute(
        "UPDATE profiles SET last_updated = ? WHERE slug = ?",
        (datetime.now().isoformat(), slug),
    )
    db.commit()

    return result
