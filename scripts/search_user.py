#!/usr/bin/env python3
"""
按需搜索用户 — 使用 Maigret 搜索跨平台账号

用法:
    python scripts/search_user.py <username>
    python scripts/search_user.py <username> --top 100
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from collectors.maigret.search import run_maigret, store_results


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/search_user.py <username> [--top N]")
        sys.exit(1)

    username = sys.argv[1]
    top = 500
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        top = int(sys.argv[idx + 1])

    print(f"🔍 搜索用户名: {username}")
    data = run_maigret(username, top)
    if "error" not in data:
        count = store_results(username, data)
        print(f"\n✅ 完成: {count} 个平台账号已入库")
    else:
        print(f"\n❌ 搜索失败: {data.get('error', '未知错误')}")


if __name__ == "__main__":
    main()
