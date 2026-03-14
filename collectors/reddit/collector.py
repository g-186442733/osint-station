import json
import logging
from datetime import datetime

import requests

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)

PULLPUSH_API = "https://api.pullpush.io/reddit/search/submission"


class RedditCollector(BaseCollector):
    def __init__(self, delay: float = 2.0):
        super().__init__(name="reddit", delay=delay)

    def collect(self, target: str) -> list[dict]:
        target = str(target).strip()
        if target.startswith("r/"):
            return self._collect_subreddit(target[2:])
        return self._collect_user(target)

    def _collect_user(self, username: str) -> list[dict]:
        logger.info(f"[reddit] 开始采集用户 u/{username}")
        params = {"author": username, "sort": "desc", "size": MAX_POSTS_PER_USER}
        posts = self._fetch(params, f"u/{username}")
        if posts:
            self.save_raw(
                posts,
                f"user_{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return posts

    def _collect_subreddit(self, subreddit: str) -> list[dict]:
        logger.info(f"[reddit] 开始采集子版 r/{subreddit}")
        params = {
            "subreddit": subreddit,
            "sort": "desc",
            "sort_type": "created_utc",
            "size": MAX_POSTS_PER_USER,
        }
        posts = self._fetch(params, f"r/{subreddit}")
        if posts:
            self.save_raw(
                posts,
                f"sub_{subreddit}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return posts

    def _fetch(self, params: dict, label: str) -> list[dict]:
        all_posts = []
        try:
            resp = requests.get(PULLPUSH_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for item in data[:MAX_POSTS_PER_USER]:
                all_posts.append(self._normalize(item))
        except Exception as e:
            logger.error(f"[reddit] {label} 采集失败: {e}")

        logger.info(f"[reddit] {label} 获取到 {len(all_posts)} 条帖子")
        return all_posts

    @staticmethod
    def _normalize(post: dict) -> dict:
        post_id = post.get("id", "")
        subreddit = post.get("subreddit", "")
        author = post.get("author", "")
        title = post.get("title", "")
        selftext = post.get("selftext", "")
        permalink = post.get("permalink", "")

        created_utc = post.get("created_utc", post.get("created", 0))
        published_at = ""
        if created_utc:
            published_at = datetime.utcfromtimestamp(int(created_utc)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

        return {
            "platform": "reddit",
            "platform_id": f"reddit_{post_id}",
            "title": title,
            "content": selftext[:500] if selftext else "",
            "url": f"https://reddit.com{permalink}" if permalink else "",
            "published_at": published_at,
            "engagement_likes": post.get("score", 0),
            "engagement_comments": post.get("num_comments", 0),
            "engagement_shares": 0,
            "engagement_views": 0,
            "raw_json": json.dumps(
                {
                    "id": post_id,
                    "subreddit": subreddit,
                    "author": author,
                    "title": title,
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                    "url": post.get("url", ""),
                    "is_self": post.get("is_self", False),
                },
                ensure_ascii=False,
            ),
        }
