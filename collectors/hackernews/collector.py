import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"


class HackerNewsCollector(BaseCollector):
    def __init__(self, delay: float = 0.5):
        super().__init__(name="hackernews", delay=delay)

    def collect(self, target: str) -> list[dict]:
        target = str(target).strip()
        if target in ("top", "best", "new"):
            return self._collect_stories(target)
        return self._collect_user(target)

    def _collect_stories(self, story_type: str = "top") -> list[dict]:
        logger.info(f"[hackernews] 开始采集 {story_type} stories")
        try:
            resp = requests.get(f"{HN_API}/{story_type}stories.json", timeout=10)
            resp.raise_for_status()
            story_ids = resp.json()[:MAX_POSTS_PER_USER]
        except Exception as e:
            logger.error(f"[hackernews] 获取{story_type}stories失败: {e}")
            return []

        posts = self._fetch_items(story_ids, f"{story_type}stories")
        if posts:
            self.save_raw(
                posts,
                f"{story_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return posts

    def _collect_user(self, username: str) -> list[dict]:
        logger.info(f"[hackernews] 开始采集用户 {username}")
        try:
            resp = requests.get(f"{HN_API}/user/{username}.json", timeout=10)
            resp.raise_for_status()
            user_data = resp.json()
            if not user_data:
                logger.warning(f"[hackernews] 用户不存在: {username}")
                return []
        except Exception as e:
            logger.error(f"[hackernews] 获取用户{username}失败: {e}")
            return []

        submitted = user_data.get("submitted", [])[:MAX_POSTS_PER_USER]
        posts = self._fetch_items(submitted, f"user/{username}")
        story_posts = [p for p in posts if p.get("title")]
        if story_posts:
            self.save_raw(
                story_posts,
                f"user_{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return story_posts

    def _fetch_items(self, item_ids: list[int], label: str) -> list[dict]:
        results = []

        def fetch_one(item_id: int) -> dict | None:
            try:
                r = requests.get(f"{HN_API}/item/{item_id}.json", timeout=8)
                r.raise_for_status()
                return r.json()
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch_one, iid): iid for iid in item_ids}
            for future in as_completed(futures):
                item = future.result()
                if item and item.get("type") == "story" and not item.get("deleted"):
                    results.append(self._normalize(item))

        results.sort(key=lambda x: x.get("engagement_likes", 0), reverse=True)
        logger.info(f"[hackernews] {label} 获取到 {len(results)} 条故事")
        return results[:MAX_POSTS_PER_USER]

    @staticmethod
    def _normalize(item: dict) -> dict:
        item_id = item.get("id", "")
        published_at = ""
        ts = item.get("time", 0)
        if ts:
            published_at = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")

        title = item.get("title", "")
        url = item.get("url", "")
        hn_url = f"https://news.ycombinator.com/item?id={item_id}"

        return {
            "platform": "hackernews",
            "platform_id": f"hn_{item_id}",
            "title": title,
            "content": item.get("text", "") or "",
            "url": url or hn_url,
            "published_at": published_at,
            "engagement_likes": item.get("score", 0),
            "engagement_comments": item.get("descendants", 0),
            "engagement_shares": 0,
            "engagement_views": 0,
            "raw_json": json.dumps(
                {
                    "id": item_id,
                    "by": item.get("by", ""),
                    "title": title,
                    "url": url,
                    "score": item.get("score", 0),
                    "descendants": item.get("descendants", 0),
                    "hn_url": hn_url,
                },
                ensure_ascii=False,
            ),
        }
