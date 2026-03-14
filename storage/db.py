"""
数据库操作封装 — 基于 SQLite 的 CRUD 接口
"""
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import DB_PATH
from storage.schema import init_db

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库操作封装"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        # 确保数据库已初始化
        init_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ==================== profiles ====================

    def upsert_profile(self, slug: str, name: str, **kwargs) -> int:
        """插入或更新人物档案，返回 rowid"""
        conn = self._connect()
        try:
            now = datetime.now().isoformat()
            existing = conn.execute(
                "SELECT id FROM profiles WHERE slug = ?", (slug,)
            ).fetchone()

            if existing:
                serialized = {}
                for k, v in kwargs.items():
                    if isinstance(v, list):
                        v = json.dumps(v, ensure_ascii=False)
                    serialized[k] = v
                fields = ", ".join(f"{k} = ?" for k in serialized)
                values = list(serialized.values()) + [now, slug]
                conn.execute(
                    f"UPDATE profiles SET {fields}, last_updated = ? WHERE slug = ?",
                    values,
                )
                conn.commit()
                return existing["id"]
            else:
                tags = kwargs.pop("tags", None)
                if tags and isinstance(tags, list):
                    tags = json.dumps(tags, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO profiles (name, slug, bio, tags, priority, first_seen, last_updated, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        name,
                        slug,
                        kwargs.get("bio", ""),
                        tags or kwargs.get("tags", ""),
                        kwargs.get("priority", "medium"),
                        now,
                        now,
                        kwargs.get("notes", ""),
                    ),
                )
                conn.commit()
                return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

    def get_profile(self, slug: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM profiles WHERE slug = ?", (slug,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_profiles(self, priority: str | None = None) -> list[dict]:
        conn = self._connect()
        try:
            if priority:
                rows = conn.execute(
                    "SELECT * FROM profiles WHERE priority = ? ORDER BY name",
                    (priority,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM profiles ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ==================== posts ====================

    def insert_posts(self, posts: list[dict], profile_slug: str | None = None) -> int:
        """
        批量插入帖子，自动去重（基于 platform + platform_id）

        Args:
            posts: 标准化的帖子列表
            profile_slug: 关联的人物 slug

        Returns:
            实际插入的数量
        """
        conn = self._connect()
        inserted = 0
        try:
            for post in posts:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO posts
                        (profile_slug, platform, platform_id, content, title, url,
                         published_at, engagement_likes, engagement_comments,
                         engagement_shares, engagement_views, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            profile_slug or post.get("profile_slug"),
                            post["platform"],
                            post["platform_id"],
                            post.get("content", ""),
                            post.get("title", ""),
                            post.get("url", ""),
                            post.get("published_at", ""),
                            post.get("engagement_likes", 0),
                            post.get("engagement_comments", 0),
                            post.get("engagement_shares", 0),
                            post.get("engagement_views", 0),
                            post.get("raw_json", ""),
                        ),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        inserted += 1
                except sqlite3.IntegrityError:
                    # 去重：同平台同ID已存在
                    continue
            conn.commit()
            logger.info(f"插入 {inserted}/{len(posts)} 条帖子 (去重后)")
        finally:
            conn.close()
        return inserted

    def get_posts(
        self,
        profile_slug: str | None = None,
        platform: str | None = None,
        limit: int = 50,
        since: str | None = None,
    ) -> list[dict]:
        """查询帖子，since 基于 collected_at（入库时间）筛选"""
        conn = self._connect()
        try:
            conditions = []
            params = []
            if profile_slug:
                conditions.append("profile_slug = ?")
                params.append(profile_slug)
            if platform:
                conditions.append("platform = ?")
                params.append(platform)
            if since:
                conditions.append("collected_at >= ?")
                params.append(since)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"SELECT * FROM posts {where} ORDER BY published_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_posts(self, profile_slug: str | None = None) -> int:
        conn = self._connect()
        try:
            if profile_slug:
                row = conn.execute(
                    "SELECT COUNT(*) FROM posts WHERE profile_slug = ?",
                    (profile_slug,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
            return row[0]
        finally:
            conn.close()

    # ==================== identity_mappings ====================

    def upsert_identity(
        self,
        profile_slug: str,
        platform: str,
        platform_username: str,
        **kwargs,
    ) -> int:
        """插入或更新身份映射"""
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id FROM identity_mappings WHERE platform = ? AND platform_username = ?",
                (platform, platform_username),
            ).fetchone()

            if existing:
                return existing["id"]

            conn.execute(
                """INSERT OR IGNORE INTO identity_mappings
                (profile_slug, platform, platform_username, platform_url, verified, confidence, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_slug,
                    platform,
                    platform_username,
                    kwargs.get("platform_url", ""),
                    kwargs.get("verified", 0),
                    kwargs.get("confidence", 0.0),
                    kwargs.get("source", "manual"),
                ),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

    def get_identities(self, profile_slug: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM identity_mappings WHERE profile_slug = ? ORDER BY platform",
                (profile_slug,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ==================== topics ====================

    def upsert_topic(self, name: str, **kwargs) -> int:
        conn = self._connect()
        try:
            now = datetime.now().isoformat()
            existing = conn.execute(
                "SELECT id FROM topics WHERE name = ?", (name,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE topics SET last_updated = ?, post_count = post_count + ? WHERE id = ?",
                    (now, kwargs.get("post_count_delta", 0), existing["id"]),
                )
                conn.commit()
                return existing["id"]

            keywords = kwargs.get("keywords")
            if keywords and isinstance(keywords, list):
                keywords = json.dumps(keywords, ensure_ascii=False)

            conn.execute(
                """INSERT INTO topics (name, description, keywords, category, trend_score, first_seen, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    kwargs.get("description", ""),
                    keywords or "",
                    kwargs.get("category", ""),
                    kwargs.get("trend_score", 0.0),
                    now,
                    now,
                ),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

    # ==================== alerts ====================

    def insert_alert(self, alert_type: str, title: str, content: str, **kwargs) -> int:
        conn = self._connect()
        try:
            related_profiles = kwargs.get("related_profiles")
            related_topics = kwargs.get("related_topics")
            if related_profiles and isinstance(related_profiles, list):
                related_profiles = json.dumps(related_profiles)
            if related_topics and isinstance(related_topics, list):
                related_topics = json.dumps(related_topics)

            conn.execute(
                """INSERT INTO alerts (alert_type, title, content, related_profiles, related_topics, pushed_to, pushed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    alert_type,
                    title,
                    content,
                    related_profiles or "",
                    related_topics or "",
                    kwargs.get("pushed_to", ""),
                    kwargs.get("pushed_at", ""),
                ),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

    def get_recent_alerts(self, limit: int = 10) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        """获取数据库概览统计"""
        conn = self._connect()
        try:
            stats = {}
            for table in ["profiles", "posts", "topics", "identity_mappings", "alerts"]:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                stats[table] = row[0]
            return stats
        finally:
            conn.close()
