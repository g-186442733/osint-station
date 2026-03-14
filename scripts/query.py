#!/usr/bin/env python3
"""
数据查询工具 — 从 SQLite 查询帖子/人物/统计

用法:
    python scripts/query.py stats
    python scripts/query.py posts [--profile SLUG] [--platform bilibili] [--limit 20]
    python scripts/query.py profiles
    python scripts/query.py report <slug>
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from storage.db import Database
from reports.briefing import BriefingGenerator


def main():
    parser = argparse.ArgumentParser(description="OSINT 数据查询")
    sub = parser.add_subparsers(dest="command")

    # stats
    sub.add_parser("stats", help="数据库统计")

    # posts
    p_posts = sub.add_parser("posts", help="查询帖子")
    p_posts.add_argument("--profile", help="人物 slug")
    p_posts.add_argument("--platform", help="平台名")
    p_posts.add_argument("--limit", type=int, default=20)

    # profiles
    sub.add_parser("profiles", help="列出所有人物")

    # report
    p_report = sub.add_parser("report", help="生成人物报告")
    p_report.add_argument("slug", help="人物 slug")

    args = parser.parse_args()
    db = Database()

    if args.command == "stats":
        stats = db.get_stats()
        print("📊 数据库统计:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif args.command == "posts":
        posts = db.get_posts(
            profile_slug=args.profile,
            platform=args.platform,
            limit=args.limit,
        )
        print(f"📝 帖子 ({len(posts)} 条):\n")
        for p in posts:
            title = p.get("title") or p.get("content", "")[:60]
            print(f"  [{p['platform']}] {p.get('published_at', '')[:10]} | {title}")

    elif args.command == "profiles":
        profiles = db.list_profiles()
        print(f"👤 人物 ({len(profiles)}):\n")
        for p in profiles:
            print(f"  {p['slug']} — {p['name']} ({p.get('priority', '?')})")

    elif args.command == "report":
        briefing = BriefingGenerator(db)
        report = briefing.generate_profile_report(args.slug)
        print(report)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
