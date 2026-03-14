#!/usr/bin/env python3
"""OSINT 写操作管理器 — 通过自然语言触发数据操作。

支持操作：
  - 添加人物到监控列表
  - 删除人物
  - 修改优先级
  - 触发采集
  - Maigret 搜索
  - 列出当前监控列表

用法:
    python3 manage_ops.py add "Elon Musk" --twitter elonmusk --github elonmusk --tags AI,SpaceX --priority high
    python3 manage_ops.py remove "Elon Musk"
    python3 manage_ops.py priority "林亦LYi" high
    python3 manage_ops.py collect "林亦LYi"
    python3 manage_ops.py collect --all
    python3 manage_ops.py search "elonmusk"
    python3 manage_ops.py list
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.osint_data import get_db, resolve_person, DB_PATH

PROJECT_ROOT = Path(__file__).parent.parent
WATCHLIST_PATH = PROJECT_ROOT / "config" / "watchlist.json"


# ─── Watchlist I/O ──────────────────────────────────────────────────────

def load_watchlist() -> dict:
    """加载 watchlist.json"""
    if WATCHLIST_PATH.exists():
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    return {"people": [], "subreddits": [], "hackernews": {"story_types": ["top"]}, "topics": []}


def save_watchlist(data: dict):
    """保存 watchlist.json（带格式化）"""
    WATCHLIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"✅ 已更新: {WATCHLIST_PATH}")


def find_person_in_watchlist(watchlist: dict, name: str) -> int | None:
    """在 watchlist 中查找人物索引（模糊匹配名称）"""
    name_lower = name.lower()
    for i, p in enumerate(watchlist.get("people", [])):
        if p["name"].lower() == name_lower:
            return i
        # 也支持用户名匹配
        for platform, username in p.get("usernames", {}).items():
            if str(username).lower() == name_lower:
                return i
    return None


# ─── Operations ─────────────────────────────────────────────────────────

def op_add(args):
    """添加人物到监控列表 + 在数据库中创建 profile"""
    watchlist = load_watchlist()

    # 检查是否已存在
    if find_person_in_watchlist(watchlist, args.name) is not None:
        print(f"⚠️ 人物 '{args.name}' 已在监控列表中")
        return False

    # 构建 usernames
    usernames = {}
    for platform in ["twitter", "youtube", "github", "bilibili", "weibo", "reddit"]:
        val = getattr(args, platform, None)
        if val:
            usernames[platform] = val

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else ["AI"]
    priority = args.priority or "medium"
    notes = args.notes or ""

    # 添加到 watchlist
    entry = {
        "name": args.name,
        "usernames": usernames,
        "tags": tags,
        "priority": priority,
    }
    if notes:
        entry["notes"] = notes

    watchlist["people"].append(entry)
    save_watchlist(watchlist)

    # 在数据库中创建 profile
    slug = args.name.lower().replace(" ", "_").replace("/", "_")
    db = get_db()
    try:
        db.execute(
            """INSERT OR IGNORE INTO profiles (name, slug, tags, priority, first_seen, last_updated, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (args.name, slug, json.dumps(tags), priority,
             datetime.now().isoformat(), datetime.now().isoformat(), notes),
        )
        # 写入 identity_mappings
        for platform, username in usernames.items():
            db.execute(
                """INSERT OR IGNORE INTO identity_mappings
                   (profile_slug, platform, platform_username, verified, confidence, source)
                   VALUES (?, ?, ?, 1, 1.0, 'manual')""",
                (slug, platform, username),
            )
        db.commit()
        print(f"✅ 已添加人物: {args.name} (@{slug})")
        print(f"   平台: {', '.join(f'{k}={v}' for k, v in usernames.items()) or '(无)'}")
        print(f"   标签: {', '.join(tags)} | 优先级: {priority}")
    finally:
        db.close()

    return True


def op_remove(args):
    """从监控列表中移除人物（不删除已采集数据）"""
    watchlist = load_watchlist()
    idx = find_person_in_watchlist(watchlist, args.name)

    if idx is None:
        print(f"❌ 未找到人物: {args.name}")
        return False

    removed = watchlist["people"].pop(idx)
    save_watchlist(watchlist)
    print(f"✅ 已移除人物: {removed['name']}")
    print(f"   ⚠️ 已采集的数据保留在数据库中，未删除")
    return True


def op_priority(args):
    """修改人物优先级"""
    if args.level not in ("high", "medium", "low"):
        print(f"❌ 无效优先级: {args.level}（可选: high/medium/low）")
        return False

    watchlist = load_watchlist()
    idx = find_person_in_watchlist(watchlist, args.name)

    if idx is None:
        print(f"❌ 未找到人物: {args.name}")
        return False

    old_priority = watchlist["people"][idx].get("priority", "medium")
    watchlist["people"][idx]["priority"] = args.level
    save_watchlist(watchlist)

    # 同步到数据库
    slug = args.name.lower().replace(" ", "_").replace("/", "_")
    db = get_db()
    try:
        resolved = resolve_person(args.name, db)
        if resolved:
            _, slug = resolved
        db.execute("UPDATE profiles SET priority = ? WHERE slug = ?", (args.level, slug))
        db.commit()
    finally:
        db.close()

    print(f"✅ 优先级已更新: {watchlist['people'][idx]['name']}")
    print(f"   {old_priority} → {args.level}")
    return True


def op_collect(args):
    """触发数据采集"""
    if args.all:
        print("🚀 触发全量采集...")
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py"), "--no-push"],
            cwd=str(PROJECT_ROOT),
            capture_output=False,
        )
        return result.returncode == 0
    elif args.name:
        print(f"🚀 触发采集: {args.name}")
        # 直接调用 pipeline 采集指定人物
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from etl.pipeline import collect_person
            result = collect_person(args.name)
            print(f"✅ 采集完成: {result}")
            return True
        except ImportError:
            # fallback: 执行全量采集
            print("⚠️ 单人采集不可用，执行全量采集...")
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py"), "--no-push"],
                cwd=str(PROJECT_ROOT),
                capture_output=False,
            )
            return result.returncode == 0
    else:
        print("❌ 请指定人物名称或使用 --all")
        return False


def op_search(args):
    """Maigret 用户名搜索"""
    search_script = PROJECT_ROOT / "scripts" / "search_user.py"
    if not search_script.exists():
        print("❌ search_user.py 不存在")
        return False

    print(f"🔍 搜索用户名: {args.username}")
    result = subprocess.run(
        [sys.executable, str(search_script), args.username],
        cwd=str(PROJECT_ROOT),
        capture_output=False,
    )
    return result.returncode == 0


def op_list(args):
    """列出当前监控列表"""
    watchlist = load_watchlist()
    people = watchlist.get("people", [])

    if not people:
        print("📋 监控列表为空")
        return True

    print(f"📋 监控列表 ({len(people)} 人)\n")
    print(f"{'名称':<20} {'优先级':<8} {'平台':<30} {'标签'}")
    print("─" * 80)

    for p in sorted(people, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("priority", "medium"), 1)):
        name = p["name"]
        priority = p.get("priority", "medium")
        platforms = ", ".join(p.get("usernames", {}).keys())
        tags = ", ".join(p.get("tags", []))
        priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
        print(f"{name:<20} {priority_icon} {priority:<6} {platforms:<30} {tags}")

    # 也显示 subreddit 和 topics
    subs = watchlist.get("subreddits", [])
    topics = watchlist.get("topics", [])
    if subs:
        print(f"\n📡 Subreddits: {', '.join(subs)}")
    if topics:
        print(f"🏷️ 话题: {', '.join(t['keyword'] for t in topics)}")

    return True


def op_stats(args):
    """显示数据库统计信息"""
    db = get_db()
    try:
        post_count = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        profile_count = db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        platform_stats = db.execute(
            "SELECT platform, COUNT(*) as cnt FROM posts GROUP BY platform ORDER BY cnt DESC"
        ).fetchall()
        alert_count = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

        print(f"📊 数据库统计\n")
        print(f"  人物数: {profile_count}")
        print(f"  帖子数: {post_count}")
        print(f"  告警数: {alert_count}")
        print(f"\n  平台分布:")
        for row in platform_stats:
            print(f"    {row['platform']:<12} {row['cnt']} 条")
    finally:
        db.close()
    return True


# ─── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OSINT 监控管理器 — 添加/删除/修改监控人物",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s add "Elon Musk" --twitter elonmusk --priority high --tags AI,SpaceX
  %(prog)s remove "Elon Musk"
  %(prog)s priority "林亦LYi" high
  %(prog)s collect --all
  %(prog)s search "elonmusk"
  %(prog)s list
  %(prog)s stats
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = subparsers.add_parser("add", help="添加人物到监控列表")
    p_add.add_argument("name", help="人物名称")
    p_add.add_argument("--twitter", help="Twitter 用户名")
    p_add.add_argument("--youtube", help="YouTube 频道 ID")
    p_add.add_argument("--github", help="GitHub 用户名")
    p_add.add_argument("--bilibili", help="B站 UID")
    p_add.add_argument("--weibo", help="微博 UID")
    p_add.add_argument("--reddit", help="Reddit 用户名")
    p_add.add_argument("--tags", default="", help="标签（逗号分隔）")
    p_add.add_argument("--priority", choices=["high", "medium", "low"], default="medium")
    p_add.add_argument("--notes", default="", help="备注")
    p_add.set_defaults(func=op_add)

    # remove
    p_rm = subparsers.add_parser("remove", help="移除人物")
    p_rm.add_argument("name", help="人物名称")
    p_rm.set_defaults(func=op_remove)

    # priority
    p_pri = subparsers.add_parser("priority", help="修改优先级")
    p_pri.add_argument("name", help="人物名称")
    p_pri.add_argument("level", choices=["high", "medium", "low"])
    p_pri.set_defaults(func=op_priority)

    # collect
    p_col = subparsers.add_parser("collect", help="触发数据采集")
    p_col.add_argument("name", nargs="?", help="人物名称（不指定则需 --all）")
    p_col.add_argument("--all", action="store_true", help="全量采集")
    p_col.set_defaults(func=op_collect)

    # search
    p_search = subparsers.add_parser("search", help="Maigret 用户名搜索")
    p_search.add_argument("username", help="要搜索的用户名")
    p_search.set_defaults(func=op_search)

    # list
    p_list = subparsers.add_parser("list", help="列出监控列表")
    p_list.set_defaults(func=op_list)

    # stats
    p_stats = subparsers.add_parser("stats", help="数据库统计")
    p_stats.set_defaults(func=op_stats)

    args = parser.parse_args()
    success = args.func(args)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
