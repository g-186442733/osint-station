"""
YouTube 采集器 — 基于 scrapetube（无需 API Key）
"""

import json
import logging
import re
from datetime import datetime, timedelta

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)


class YouTubeCollector(BaseCollector):
    def __init__(self, delay: float = 2.0):
        super().__init__(name="youtube", delay=delay)

    def collect(self, target: str) -> list[dict]:
        channel_id = str(target).strip()
        logger.info(f"[youtube] 开始采集 channel={channel_id}")

        posts = self._fetch_channel_videos(channel_id)
        logger.info(f"[youtube] channel={channel_id} 获取到 {len(posts)} 条视频")

        if posts:
            self.save_raw(
                posts,
                f"videos_{channel_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return posts

    def _fetch_channel_videos(self, channel_id: str) -> list[dict]:
        import scrapetube

        all_videos = []
        try:
            for i, video in enumerate(scrapetube.get_channel(channel_id)):
                if i >= MAX_POSTS_PER_USER:
                    break
                all_videos.append(self._normalize(video, channel_id))
        except Exception as e:
            logger.error(f"[youtube] channel={channel_id} 采集失败: {e}")

        return all_videos

    def _normalize(self, v: dict, channel_id: str) -> dict:
        video_id = v.get("videoId", "")

        title_obj = v.get("title", {})
        if isinstance(title_obj, dict):
            runs = title_obj.get("runs", [])
            title = runs[0].get("text", "") if runs else ""
        else:
            title = str(title_obj)

        description_snippet = ""
        desc_obj = v.get("descriptionSnippet", {})
        if isinstance(desc_obj, dict):
            runs = desc_obj.get("runs", [])
            description_snippet = "".join(r.get("text", "") for r in runs)

        view_text = v.get("viewCountText", {})
        views = 0
        if isinstance(view_text, dict):
            simple = view_text.get("simpleText", "")
            views = self._parse_view_count(simple)
        elif isinstance(view_text, str):
            views = self._parse_view_count(view_text)

        published_text = ""
        pub_obj = v.get("publishedTimeText", {})
        if isinstance(pub_obj, dict):
            published_text = pub_obj.get("simpleText", "")
        elif isinstance(pub_obj, str):
            published_text = pub_obj

        published_at = self._parse_relative_time(published_text)

        length_text = ""
        len_obj = v.get("lengthText", {})
        if isinstance(len_obj, dict):
            length_text = len_obj.get("simpleText", "")

        thumbnail_url = ""
        thumbs = v.get("thumbnail", {}).get("thumbnails", [])
        if thumbs:
            thumbnail_url = thumbs[-1].get("url", "")

        return {
            "platform": "youtube",
            "platform_id": video_id,
            "title": title,
            "content": description_snippet,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            "published_at": published_at,
            "engagement_likes": 0,
            "engagement_comments": 0,
            "engagement_shares": 0,
            "engagement_views": views,
            "raw_json": json.dumps(
                {
                    "videoId": video_id,
                    "title": title,
                    "description": description_snippet,
                    "views": views,
                    "published_text": published_text,
                    "duration": length_text,
                    "thumbnail": thumbnail_url,
                    "channel_id": channel_id,
                },
                ensure_ascii=False,
                default=str,
            ),
        }

    @staticmethod
    def _parse_view_count(text: str) -> int:
        if not text:
            return 0
        cleaned = re.sub(r"[^\d]", "", text)
        try:
            return int(cleaned)
        except ValueError:
            m = re.search(r"([\d.]+)\s*万", text)
            if m:
                return int(float(m.group(1)) * 10000)
            m = re.search(r"([\d.]+)\s*[Kk]", text)
            if m:
                return int(float(m.group(1)) * 1000)
            m = re.search(r"([\d.]+)\s*[Mm]", text)
            if m:
                return int(float(m.group(1)) * 1000000)
            return 0

    @staticmethod
    def _parse_relative_time(text: str) -> str:
        if not text:
            return ""
        now = datetime.now()

        patterns = [
            (r"(\d+)\s*minute", lambda m: now - timedelta(minutes=int(m))),
            (r"(\d+)\s*hour", lambda m: now - timedelta(hours=int(m))),
            (r"(\d+)\s*day", lambda m: now - timedelta(days=int(m))),
            (r"(\d+)\s*week", lambda m: now - timedelta(weeks=int(m))),
            (r"(\d+)\s*month", lambda m: now - timedelta(days=int(m) * 30)),
            (r"(\d+)\s*year", lambda m: now - timedelta(days=int(m) * 365)),
            (r"(\d+)\s*分钟前", lambda m: now - timedelta(minutes=int(m))),
            (r"(\d+)\s*小时前", lambda m: now - timedelta(hours=int(m))),
            (r"(\d+)\s*天前", lambda m: now - timedelta(days=int(m))),
            (r"(\d+)\s*周前", lambda m: now - timedelta(weeks=int(m))),
            (r"(\d+)\s*个月前", lambda m: now - timedelta(days=int(m) * 30)),
            (r"(\d+)\s*年前", lambda m: now - timedelta(days=int(m) * 365)),
        ]

        for pattern, calc in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dt = calc(int(match.group(1)))
                return dt.strftime("%Y-%m-%dT%H:%M:%S")

        return text

    def __repr__(self) -> str:
        return f"<YouTubeCollector delay={self.delay}>"
