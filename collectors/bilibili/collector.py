"""
B站采集器 — 基于 bilibili-api-python（自动处理 Wbi 签名和反爬）
"""
import asyncio
import json
import logging
from datetime import datetime

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER, BILIBILI_COOKIES

logger = logging.getLogger(__name__)


class BilibiliCollector(BaseCollector):

    def __init__(self, delay: float = 2.0):
        super().__init__(name="bilibili", delay=delay)

    def collect(self, target: str) -> list[dict]:
        uid = int(str(target).strip())
        logger.info(f"[bilibili] 开始采集 UID={uid}")

        try:
            videos = asyncio.run(self._fetch_videos(uid))
        except RuntimeError:
            # Already inside an event loop — create new one
            loop = asyncio.new_event_loop()
            try:
                videos = loop.run_until_complete(self._fetch_videos(uid))
            finally:
                loop.close()

        logger.info(f"[bilibili] UID={uid} 获取到 {len(videos)} 条视频")

        if videos:
            self.save_raw(
                videos,
                f"videos_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        return videos

    async def _fetch_videos(self, uid: int) -> list[dict]:
        """使用 bilibili-api-python 获取用户视频列表"""
        from bilibili_api import user, Credential

        # 如果配置了 cookies，使用它们获得更高速率限制
        credential = None
        if BILIBILI_COOKIES:
            try:
                parts = {}
                for kv in BILIBILI_COOKIES.split(";"):
                    kv = kv.strip()
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        parts[k.strip()] = v.strip()
                if parts.get("SESSDATA"):
                    credential = Credential(
                        sessdata=parts.get("SESSDATA", ""),
                        bili_jct=parts.get("bili_jct", ""),
                        buvid3=parts.get("buvid3", ""),
                    )
            except Exception as e:
                logger.debug(f"[bilibili] 解析 cookies 失败，使用匿名模式: {e}")

        u = user.User(uid=uid, credential=credential)
        all_videos = []

        try:
            # 分页获取视频
            page = 1
            page_size = 30
            max_pages = (MAX_POSTS_PER_USER // page_size) + 1

            while page <= max_pages and len(all_videos) < MAX_POSTS_PER_USER:
                data = await asyncio.wait_for(
                    u.get_videos(pn=page, ps=page_size),
                    timeout=20,
                )
                vlist = data.get("list", {}).get("vlist", [])
                if not vlist:
                    break

                for v in vlist:
                    all_videos.append(self._normalize(v))

                # 如果本页数量不足，说明已经是最后一页
                if len(vlist) < page_size:
                    break

                page += 1
                self._sleep(jitter=0.5)

        except asyncio.TimeoutError:
            logger.warning(f"[bilibili] UID={uid} 请求超时")
        except Exception as e:
            logger.error(f"[bilibili] UID={uid} 采集失败: {e}")

        return all_videos[:MAX_POSTS_PER_USER]

    @staticmethod
    def _normalize(v: dict) -> dict:
        """将 bilibili-api 返回的视频信息标准化为通用格式"""
        bvid = v.get("bvid", "")
        created = v.get("created", 0)
        published_at = ""
        if created:
            try:
                published_at = datetime.fromtimestamp(created).isoformat()
            except (ValueError, OSError):
                pass

        return {
            "platform": "bilibili",
            "platform_id": bvid,
            "title": v.get("title", ""),
            "content": v.get("description", "") or v.get("desc", ""),
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "published_at": published_at,
            "engagement_likes": v.get("like", 0),
            "engagement_comments": v.get("comment", 0),
            "engagement_shares": v.get("share", 0),
            "engagement_views": v.get("play", 0),
            "raw_json": json.dumps(v, ensure_ascii=False, default=str),
        }
