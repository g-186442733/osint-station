import re
import logging
import requests
from datetime import datetime
from typing import Any

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://m.weibo.cn/",
    "X-Requested-With": "XMLHttpRequest",
}


class WeiboCollector(BaseCollector):

    def __init__(self, delay: float = 3.0, cookies: str | None = None):
        super().__init__(name="weibo", delay=delay)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if cookies:
            self.session.headers["Cookie"] = cookies

    def collect(self, target: str) -> list[dict]:
        uid = str(target).strip()
        logger.info(f"[weibo] 开始采集 UID={uid}")

        posts = self._fetch_user_posts(uid)
        logger.info(f"[weibo] UID={uid} 获取到 {len(posts)} 条微博")

        if posts:
            self.save_raw(posts, f"posts_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

        return [self._normalize_post(p, uid) for p in posts]

    def _fetch_user_posts(self, uid: str) -> list[dict]:
        all_posts = []
        page = 1
        max_pages = 3

        while page <= max_pages and len(all_posts) < MAX_POSTS_PER_USER:
            try:
                url = "https://m.weibo.cn/api/container/getIndex"
                params = {
                    "type": "uid",
                    "value": uid,
                    "containerid": f"107603{uid}",
                    "page": page,
                }

                resp = self.session.get(url, params=params, timeout=15)
                data = resp.json()

                if data.get("ok") != 1:
                    error_msg = data.get("msg", "unknown error")
                    logger.warning(f"[weibo] API 错误 (page={page}): {error_msg}")
                    break

                cards = data.get("data", {}).get("cards", [])
                if not cards:
                    break

                weibo_cards = []
                for card in cards:
                    if card.get("card_type") == 9:
                        mblog = card.get("mblog")
                        if mblog:
                            weibo_cards.append(mblog)

                if not weibo_cards:
                    break

                all_posts.extend(weibo_cards)
                page += 1
                self._sleep(jitter=2.0)

            except requests.RequestException as e:
                logger.error(f"[weibo] 请求异常 (page={page}): {e}")
                break

        return all_posts[:MAX_POSTS_PER_USER]

    def fetch_user_info(self, uid: str) -> dict | None:
        try:
            url = "https://m.weibo.cn/api/container/getIndex"
            params = {"type": "uid", "value": uid, "containerid": f"100505{uid}"}
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()

            if data.get("ok") == 1:
                return data.get("data", {}).get("userInfo", {})
            else:
                logger.warning(f"[weibo] 获取用户信息失败: {data.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"[weibo] 获取用户信息异常: {e}")
            return None

    @staticmethod
    def _clean_html(html_text: str) -> str:
        if not html_text:
            return ""
        text = html_text
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        return text

    @staticmethod
    def _parse_weibo_time(time_str: str) -> str:
        if not time_str:
            return ""
        now = datetime.now()
        try:
            dt = datetime.strptime(time_str, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
        if "刚刚" in time_str:
            return now.isoformat()
        m = re.match(r"(\d+)分钟前", time_str)
        if m:
            from datetime import timedelta
            dt = now - timedelta(minutes=int(m.group(1)))
            return dt.isoformat()
        m = re.match(r"(\d+)小时前", time_str)
        if m:
            from datetime import timedelta
            dt = now - timedelta(hours=int(m.group(1)))
            return dt.isoformat()
        m = re.match(r"昨天\s*(\d{2}):(\d{2})", time_str)
        if m:
            from datetime import timedelta
            dt = now - timedelta(days=1)
            dt = dt.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
            return dt.isoformat()
        m = re.match(r"(\d{2})-(\d{2})", time_str)
        if m and len(time_str) <= 5:
            dt = now.replace(month=int(m.group(1)), day=int(m.group(2)))
            return dt.strftime("%Y-%m-%dT00:00:00")
        for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.isoformat()
            except ValueError:
                continue
        return time_str

    def _normalize_post(self, mblog: dict, uid: str) -> dict:
        import json
        weibo_id = str(mblog.get("id", ""))
        text = self._clean_html(mblog.get("text", ""))
        created_at = self._parse_weibo_time(mblog.get("created_at", ""))
        url = f"https://m.weibo.cn/detail/{weibo_id}" if weibo_id else ""

        return {
            "platform": "weibo",
            "platform_id": weibo_id,
            "title": "",
            "content": text,
            "url": url,
            "published_at": created_at,
            "engagement_likes": mblog.get("attitudes_count", 0),
            "engagement_comments": mblog.get("comments_count", 0),
            "engagement_shares": mblog.get("reposts_count", 0),
            "engagement_views": 0,
            "raw_json": json.dumps(mblog, ensure_ascii=False, default=str),
        }

    def __repr__(self) -> str:
        return f"<WeiboCollector delay={self.delay}>"
