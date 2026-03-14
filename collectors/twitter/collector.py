"""
Twitter/X 采集器 — 基于 twikit（无需官方 API，cookie 登录）
"""

import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)

# Cookie 文件路径（项目根目录下）
COOKIES_PATH = Path(__file__).parent.parent.parent / "config" / "twitter_cookies.json"


class TwitterCollector(BaseCollector):
    def __init__(self, delay: float = 3.0):
        super().__init__(name="twitter", delay=delay)
        self._client = None

    async def _get_client(self):
        """延迟初始化 twikit 客户端（复用 session）"""
        if self._client is not None:
            return self._client

        from twikit import Client

        client = Client("en-US")

        if COOKIES_PATH.exists():
            client.load_cookies(str(COOKIES_PATH))
            logger.info("[twitter] 已加载 cookie 文件")
        else:
            logger.error(f"[twitter] Cookie 文件不存在: {COOKIES_PATH}")
            raise FileNotFoundError(f"Twitter cookie 文件不存在: {COOKIES_PATH}")

        self._client = client
        return client

    def collect(self, target: str) -> list[dict]:
        """
        采集用户推文时间线。
        target: Twitter 用户的 screen_name（不含 @）或 user_id
        """
        screen_name = str(target).strip().lstrip("@")
        logger.info(f"[twitter] 开始采集 @{screen_name}")

        try:
            posts = asyncio.run(self._fetch_user_tweets(screen_name))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                posts = loop.run_until_complete(self._fetch_user_tweets(screen_name))
            finally:
                loop.close()

        logger.info(f"[twitter] @{screen_name} 获取到 {len(posts)} 条推文")

        if posts:
            self.save_raw(
                posts,
                f"tweets_{screen_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return posts

    async def _fetch_user_tweets(self, screen_name: str) -> list[dict]:
        """获取用户最新推文"""
        client = await self._get_client()

        all_tweets = []
        try:
            # 先通过 screen_name 获取用户信息
            user = await asyncio.wait_for(
                client.get_user_by_screen_name(screen_name),
                timeout=20,
            )
            if not user:
                logger.warning(f"[twitter] 用户不存在: @{screen_name}")
                return []

            logger.info(
                f"[twitter] 用户信息: @{screen_name} "
                f"(id={user.id}, followers={getattr(user, 'followers_count', '?')})"
            )

            # 获取用户推文
            tweets = await asyncio.wait_for(
                client.get_user_tweets(user.id, "Tweets", count=MAX_POSTS_PER_USER),
                timeout=30,
            )

            for tweet in tweets:
                all_tweets.append(self._normalize_tweet(tweet, screen_name))
                if len(all_tweets) >= MAX_POSTS_PER_USER:
                    break

            # 保存 cookies 以延长 session
            client.save_cookies(str(COOKIES_PATH))

        except asyncio.TimeoutError:
            logger.warning(f"[twitter] @{screen_name} 请求超时")
        except Exception as e:
            logger.error(f"[twitter] @{screen_name} 采集失败: {e}")

        return all_tweets

    @staticmethod
    def _normalize_tweet(tweet, screen_name: str) -> dict:
        """标准化推文为通用格式"""
        tweet_id = str(getattr(tweet, "id", ""))
        text = getattr(tweet, "text", "") or ""
        created_at = ""
        raw_created = getattr(tweet, "created_at", None)
        if raw_created:
            try:
                if isinstance(raw_created, str):
                    # Twitter 格式: "Thu Mar 13 15:30:00 +0000 2026"
                    dt = datetime.strptime(raw_created, "%a %b %d %H:%M:%S %z %Y")
                    created_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    created_at = str(raw_created)
            except (ValueError, TypeError):
                created_at = str(raw_created)

        return {
            "platform": "twitter",
            "platform_id": tweet_id,
            "title": "",
            "content": text,
            "url": f"https://x.com/{screen_name}/status/{tweet_id}" if tweet_id else "",
            "published_at": created_at,
            "engagement_likes": getattr(tweet, "favorite_count", 0) or 0,
            "engagement_comments": getattr(tweet, "reply_count", 0) or 0,
            "engagement_shares": getattr(tweet, "retweet_count", 0) or 0,
            "engagement_views": getattr(tweet, "view_count", 0) or 0,
            "raw_json": json.dumps(
                {
                    "id": tweet_id,
                    "text": text,
                    "created_at": str(raw_created),
                    "favorite_count": getattr(tweet, "favorite_count", 0),
                    "retweet_count": getattr(tweet, "retweet_count", 0),
                    "reply_count": getattr(tweet, "reply_count", 0),
                    "view_count": getattr(tweet, "view_count", 0),
                    "screen_name": screen_name,
                },
                ensure_ascii=False,
                default=str,
            ),
        }

    def __repr__(self) -> str:
        return f"<TwitterCollector delay={self.delay}>"
