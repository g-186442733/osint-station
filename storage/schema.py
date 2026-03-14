"""
OSINT 工作站 — SQLite Schema 初始化
五张核心表：profiles, posts, topics, identity_mappings, alerts
"""

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
-- 人物档案表
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- 显示名
    slug TEXT UNIQUE NOT NULL,             -- 唯一标识（英文小写，用于关联）
    bio TEXT,                              -- 简介
    tags TEXT,                             -- JSON 数组，如 ["AI","创业"]
    priority TEXT DEFAULT 'medium',        -- high/medium/low
    first_seen TEXT,                       -- 首次发现时间
    last_updated TEXT,                     -- 最后更新时间
    notes TEXT,                            -- 备注
    created_at TEXT DEFAULT (datetime('now'))
);

-- 帖子/内容表
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_slug TEXT,                     -- 关联 profiles.slug
    platform TEXT NOT NULL,                -- bilibili/weibo/twitter/xiaohongshu/douyin
    platform_id TEXT,                      -- 平台原始ID（去重用）
    content TEXT,                          -- 正文内容
    title TEXT,                            -- 标题（如有）
    url TEXT,                              -- 原文链接
    published_at TEXT,                     -- 发布时间
    engagement_likes INTEGER DEFAULT 0,
    engagement_comments INTEGER DEFAULT 0,
    engagement_shares INTEGER DEFAULT 0,
    engagement_views INTEGER DEFAULT 0,
    topic_id INTEGER,                      -- 关联 topics.id（分类后填入）
    sentiment TEXT,                        -- positive/negative/neutral
    raw_json TEXT,                         -- 原始 JSON 缓存
    collected_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (profile_slug) REFERENCES profiles(slug),
    FOREIGN KEY (topic_id) REFERENCES topics(id),
    UNIQUE(platform, platform_id)          -- 同平台同 ID 去重
);

-- 话题/分类表
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- 话题名称
    description TEXT,                      -- 话题描述
    keywords TEXT,                         -- JSON 数组，关键词列表
    category TEXT,                         -- business_opportunity/tech_trend/industry_news/gossip
    post_count INTEGER DEFAULT 0,         -- 关联帖子数
    trend_score REAL DEFAULT 0.0,         -- 趋势热度分（LLM 评估）
    first_seen TEXT,
    last_updated TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 跨平台身份映射表
CREATE TABLE IF NOT EXISTS identity_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_slug TEXT NOT NULL,            -- 关联 profiles.slug
    platform TEXT NOT NULL,                -- 平台名
    platform_username TEXT NOT NULL,       -- 平台用户名/ID
    platform_url TEXT,                     -- 个人主页链接
    verified BOOLEAN DEFAULT 0,           -- 是否已人工确认
    confidence REAL DEFAULT 0.0,          -- 匹配置信度（0-1）
    source TEXT,                           -- 来源：maigret/manual/llm_match
    discovered_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (profile_slug) REFERENCES profiles(slug),
    UNIQUE(platform, platform_username)    -- 同平台同用户名唯一
);

-- 告警/情报推送记录
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,              -- daily_brief/trend_alert/new_profile/anomaly
    title TEXT NOT NULL,
    content TEXT,                          -- 推送正文（Markdown）
    related_profiles TEXT,                 -- JSON 数组，涉及的 profile slugs
    related_topics TEXT,                   -- JSON 数组，涉及的 topic ids
    pushed_to TEXT,                        -- wecom/feishu/email
    pushed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_posts_profile ON posts(profile_slug);
CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published_at);
CREATE INDEX IF NOT EXISTS idx_posts_collected ON posts(collected_at);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic_id);
CREATE INDEX IF NOT EXISTS idx_identity_profile ON identity_mappings(profile_slug);
CREATE INDEX IF NOT EXISTS idx_identity_platform ON identity_mappings(platform);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
"""

FTS_SQL = """
-- FTS5 全文搜索虚拟表
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    title,
    content,
    content='posts',
    content_rowid='id',
    tokenize='unicode61'
);

-- 触发器: 自动同步 FTS 索引
CREATE TRIGGER IF NOT EXISTS posts_fts_insert AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_delete AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_update AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO posts_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""


def init_db(db_path: str | Path = None):
    """初始化数据库，创建表和索引"""
    if db_path is None:
        from config.settings import DB_PATH

        db_path = DB_PATH

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # 初始化 FTS5 全文搜索
    try:
        conn.executescript(FTS_SQL)
        conn.commit()
        # 如果 FTS 表为空但 posts 表有数据，重建 FTS 索引
        fts_count = conn.execute("SELECT COUNT(*) FROM posts_fts").fetchone()[0]
        posts_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        if fts_count == 0 and posts_count > 0:
            print(f"重建 FTS 索引 ({posts_count} 条帖子)...")
            conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
            conn.commit()
            print("FTS 索引重建完成")
    except Exception as e:
        print(f"FTS5 初始化提示: {e}")

    # 验证
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    print(f"数据库已初始化: {db_path}")
    print(f"创建的表: {', '.join(tables)}")
    return tables


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        init_db(sys.argv[1])
    else:
        init_db()
