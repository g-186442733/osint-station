"""
每日情报简报生成器 — 从数据库汇总数据，生成 Markdown 格式简报
"""
import json
import logging
from datetime import datetime, timedelta

from storage.db import Database
from config.settings import MODELS

logger = logging.getLogger(__name__)


class BriefingGenerator:
    """情报简报生成器"""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def generate_daily_brief(self, hours: int = 24) -> str:
        """
        生成每日情报简报（Markdown 格式）

        Args:
            hours: 回顾最近多少小时的数据

        Returns:
            Markdown 格式的简报内容
        """
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        stats = self.db.get_stats()

        # 获取最新帖子
        recent_posts = self.db.get_posts(limit=50, since=since)

        # 按平台分组
        by_platform: dict[str, list[dict]] = {}
        for post in recent_posts:
            platform = post.get("platform", "unknown")
            by_platform.setdefault(platform, []).append(post)

        # 按人物分组
        by_profile: dict[str, list[dict]] = {}
        for post in recent_posts:
            slug = post.get("profile_slug", "unknown")
            by_profile.setdefault(slug, []).append(post)

        # 生成简报
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        brief = f"# 📋 OSINT 每日情报简报\n\n"
        brief += f"> 生成时间: {now} | 回顾: 最近 {hours} 小时\n\n"

        # === 概览 ===
        brief += "## 📊 概览\n\n"
        brief += f"| 指标 | 数量 |\n|---|---|\n"
        brief += f"| 关注人物 | {stats.get('profiles', 0)} |\n"
        brief += f"| 帖子总量 | {stats.get('posts', 0)} |\n"
        brief += f"| 新增帖子 | {len(recent_posts)} |\n"
        brief += f"| 身份映射 | {stats.get('identity_mappings', 0)} |\n"
        brief += f"| 话题数 | {stats.get('topics', 0)} |\n\n"

        # === 按平台统计 ===
        if by_platform:
            brief += "## 📡 平台动态\n\n"
            platform_icons = {
                "bilibili": "📺",
                "weibo": "📱",
                "twitter": "🐦",
                "github": "💻",
                "xiaohongshu": "📕",
                "douyin": "🎵",
            }
            for platform, posts in sorted(by_platform.items()):
                icon = platform_icons.get(platform, "📌")
                brief += f"### {icon} {platform} ({len(posts)} 条)\n\n"
                for post in posts[:5]:  # 每平台最多展示5条
                    title = post.get("title") or post.get("content", "")[:60]
                    title = title.replace("\n", " ").strip()
                    url = post.get("url", "")
                    likes = post.get("engagement_likes", 0)
                    views = post.get("engagement_views", 0)

                    if url:
                        brief += f"- [{title}]({url})"
                    else:
                        brief += f"- {title}"

                    metrics = []
                    if views:
                        metrics.append(f"👀{views}")
                    if likes:
                        metrics.append(f"👍{likes}")
                    if metrics:
                        brief += f" ({' '.join(metrics)})"
                    brief += "\n"

                if len(posts) > 5:
                    brief += f"- ... 还有 {len(posts) - 5} 条\n"
                brief += "\n"

        # === 按人物统计 ===
        if by_profile:
            brief += "## 👤 人物动态\n\n"
            for slug, posts in sorted(
                by_profile.items(), key=lambda x: len(x[1]), reverse=True
            ):
                if slug == "unknown":
                    continue
                profile = self.db.get_profile(slug)
                name = profile.get("name", slug) if profile else slug
                brief += f"**{name}** — {len(posts)} 条新动态\n"
                for post in posts[:3]:
                    title = post.get("title") or post.get("content", "")[:50]
                    title = title.replace("\n", " ").strip()
                    platform = post.get("platform", "")
                    brief += f"  - [{platform}] {title}\n"
                brief += "\n"

        # === 无数据时 ===
        if not recent_posts:
            brief += "## 📭 暂无新数据\n\n"
            brief += f"最近 {hours} 小时内没有采集到新的帖子。\n"
            brief += "请检查采集器是否正常运行。\n\n"

        # === 脚注 ===
        brief += "---\n"
        brief += f"*OSINT Station | 模型: {MODELS.get('daily_summary', 'N/A')} | "
        brief += f"数据库: {stats.get('posts', 0)} 条记录*\n"

        return brief

    def generate_profile_report(self, slug: str) -> str:
        """
        生成单个人物的详细档案报告

        Args:
            slug: 人物 slug

        Returns:
            Markdown 格式的人物报告
        """
        profile = self.db.get_profile(slug)
        if not profile:
            return f"❌ 未找到人物: {slug}"

        identities = self.db.get_identities(slug)
        posts = self.db.get_posts(profile_slug=slug, limit=20)

        report = f"# 👤 人物档案: {profile['name']}\n\n"
        report += f"> Slug: `{slug}` | 优先级: {profile.get('priority', 'medium')}\n\n"

        if profile.get("bio"):
            report += f"**简介**: {profile['bio']}\n\n"

        if profile.get("tags"):
            try:
                tags = json.loads(profile["tags"])
                report += f"**标签**: {', '.join(tags)}\n\n"
            except (json.JSONDecodeError, TypeError):
                report += f"**标签**: {profile['tags']}\n\n"

        # 跨平台账号
        if identities:
            report += "## 🔗 跨平台账号\n\n"
            report += "| 平台 | 用户名 | 链接 | 来源 | 置信度 |\n"
            report += "|------|--------|------|------|--------|\n"
            for ident in identities:
                verified = "✅" if ident.get("verified") else "❓"
                url = ident.get("platform_url", "")
                link = f"[链接]({url})" if url else "-"
                conf = f"{ident.get('confidence', 0):.0%}"
                report += (
                    f"| {ident['platform']} {verified} | "
                    f"{ident['platform_username']} | {link} | "
                    f"{ident.get('source', '-')} | {conf} |\n"
                )
            report += "\n"

        # 最近动态
        if posts:
            report += f"## 📝 最近动态 ({len(posts)} 条)\n\n"
            for post in posts:
                title = post.get("title") or post.get("content", "")[:80]
                title = title.replace("\n", " ").strip()
                platform = post.get("platform", "?")
                date = post.get("published_at", "")[:10]
                report += f"- **[{platform}]** {date} — {title}\n"
            report += "\n"

        report += "---\n"
        report += f"*生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

        return report
