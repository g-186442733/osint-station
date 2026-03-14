#!/usr/bin/env python3
"""
OSINT 工作站 — CLI 入口
用法：
    python3 main.py init              初始化数据库 + 导入 watchlist
    python3 main.py collect           采集所有关注人物
    python3 main.py collect 稚晖君    采集指定人物
    python3 main.py analyze           对新帖子进行话题分类
    python3 main.py brief             生成每日情报简报
    python3 main.py push              生成简报并推送到企微
    python3 main.py search <username> Maigret 用户名搜索
    python3 main.py run               完整流程：采集 → 分类 → 简报 → 推送
    python3 main.py status            查看数据库状态
"""
import sys
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import DB_PATH, WATCHLIST_PATH
from storage.schema import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("osint")


def cmd_init():
    """初始化数据库并导入 watchlist 中的人物"""
    print("📦 初始化数据库...")
    init_db()

    print("\n📋 导入 watchlist 人物...")
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        wl = json.load(f)

    db = sqlite3.connect(str(DB_PATH))
    count = 0

    for person in wl.get("people", []):
        name = person["name"]
        slug = name.lower().replace(" ", "_")
        tags = json.dumps(person.get("tags", []), ensure_ascii=False)
        priority = person.get("priority", "medium")
        notes = person.get("notes", "")
        now = datetime.now().isoformat()

        db.execute("""
            INSERT OR IGNORE INTO profiles (name, slug, tags, priority, notes, first_seen, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, slug, tags, priority, notes, now, now))

        for platform, uid in person.get("usernames", {}).items():
            if uid:
                url = ""
                if platform == "bilibili":
                    url = f"https://space.bilibili.com/{uid}"
                elif platform == "weibo":
                    url = f"https://m.weibo.cn/u/{uid}"
                db.execute("""
                    INSERT OR IGNORE INTO identity_mappings
                    (profile_slug, platform, platform_username, platform_url, source, confidence, verified)
                    VALUES (?, ?, ?, ?, 'manual', 1.0, 1)
                """, (slug, platform, uid, url))
        count += 1

    db.commit()
    db.close()
    print(f"✅ 已导入 {count} 个人物")


def cmd_collect(names: list[str] | None = None):
    """采集数据"""
    from etl.pipeline import run_collection

    filter_list = names if names else None
    results = run_collection(filter_list)

    print("\n📊 采集结果：")
    for r in results:
        print(f"  {r['name']}:")
        for platform, info in r["platforms"].items():
            status = info.get("status", "unknown")
            if status == "ok":
                print(f"    {platform}: ✅ {info['total']}条 (新增{info['new']})")
            elif status == "unsupported":
                print(f"    {platform}: ⏭️ 暂不支持")
            else:
                print(f"    {platform}: ❌ {info.get('error', 'failed')}")


def cmd_analyze():
    """话题分类"""
    from etl.analyzer import classify_posts
    count = classify_posts(hours=48)
    print(f"✅ 完成分类，共处理 {count} 条帖子")


def cmd_brief():
    """生成情报简报"""
    from etl.analyzer import generate_daily_brief
    brief = generate_daily_brief(hours=48)
    print("\n" + brief)
    return brief


def cmd_push():
    """生成简报并推送"""
    from etl.analyzer import generate_daily_brief
    from reports.pusher import push_daily_brief

    print("📝 生成情报简报...")
    brief = generate_daily_brief(hours=48)

    if not brief or brief.startswith("⚠️"):
        print("❌ 简报生成失败，跳过推送")
        return

    print("📤 推送到企业微信...")
    success = push_daily_brief(brief)
    if success:
        print("✅ 推送成功")
    else:
        print("❌ 推送失败")


def cmd_search(username: str, top: int = 500):
    """Maigret 用户名搜索"""
    from collectors.maigret.search import run_maigret, store_results

    print(f"🔍 搜索用户名: {username}")
    init_db()  # 确保数据库存在
    data = run_maigret(username, top)
    if "error" not in data:
        store_results(username, data)


def cmd_run():
    """完整流程：采集 → 分类 → 简报 → 推送"""
    print("🚀 开始完整流程...\n")

    print("=" * 40)
    print("步骤 1/4: 数据采集")
    print("=" * 40)
    cmd_collect()

    print("\n" + "=" * 40)
    print("步骤 2/4: 话题分类")
    print("=" * 40)
    cmd_analyze()

    print("\n" + "=" * 40)
    print("步骤 3/4: 生成简报")
    print("=" * 40)
    brief = cmd_brief()

    print("\n" + "=" * 40)
    print("步骤 4/4: 推送到企微")
    print("=" * 40)
    if brief and not brief.startswith("⚠️"):
        from reports.pusher import push_daily_brief
        success = push_daily_brief(brief)
        if success:
            print("✅ 推送成功")
        else:
            print("❌ 推送失败")

    print("\n🎉 完整流程执行完毕")


def cmd_status():
    """查看数据库状态"""
    if not DB_PATH.exists():
        print("❌ 数据库不存在，请先运行 init")
        return

    db = sqlite3.connect(str(DB_PATH))

    tables = {
        "profiles": "人物档案",
        "posts": "帖子内容",
        "topics": "话题分类",
        "identity_mappings": "身份映射",
        "alerts": "推送记录",
    }

    print("📊 OSINT 工作站状态\n")
    for table, label in tables.items():
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {label} ({table}): {count} 条")

    # 最近采集
    recent = db.execute("""
        SELECT profile_slug, platform, COUNT(*) as cnt, MAX(collected_at) as latest
        FROM posts
        GROUP BY profile_slug, platform
        ORDER BY latest DESC
        LIMIT 10
    """).fetchall()

    if recent:
        print("\n  最近采集记录：")
        for r in recent:
            print(f"    {r[0]} / {r[1]}: {r[2]}条 (最新: {r[3][:16]})")

    db.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "init":
        cmd_init()
    elif cmd == "collect":
        names = sys.argv[2:] if len(sys.argv) > 2 else None
        cmd_collect(names)
    elif cmd == "analyze":
        cmd_analyze()
    elif cmd == "brief":
        cmd_brief()
    elif cmd == "push":
        cmd_push()
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法: python3 main.py search <username> [--top N]")
            return
        username = sys.argv[2]
        top = 500
        if "--top" in sys.argv:
            idx = sys.argv.index("--top")
            top = int(sys.argv[idx + 1])
        cmd_search(username, top)
    elif cmd == "run":
        cmd_run()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
