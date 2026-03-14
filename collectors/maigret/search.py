#!/usr/bin/env python3
import subprocess
import json
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent.parent  # osint-station/
DB_PATH = PROJECT_ROOT / "storage" / "osint.db"
VENV_MAIGRET = PROJECT_ROOT / ".venv" / "bin" / "maigret"
RAW_DIR = PROJECT_ROOT / "data" / "raw"


def run_maigret(username: str, top_sites: int = 500) -> dict:
    output_dir = RAW_DIR / "maigret"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(VENV_MAIGRET),
        username,
        "--top-sites",
        str(top_sites),
        "-J",
        "simple",
        "--folderoutput",
        str(output_dir),
        "--no-color",
        "--timeout",
        "10",
    ]

    print(f"🔍 正在搜索用户名: {username} (前 {top_sites} 个站点)")
    print(f"   命令: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    print(f"\n--- stdout (最后30行) ---")
    for line in result.stdout.strip().split("\n")[-30:]:
        print(f"  {line}")

    if result.stderr:
        print(f"\n--- stderr (最后10行) ---")
        for line in result.stderr.strip().split("\n")[-10:]:
            print(f"  {line}")

    json_files = list(output_dir.glob(f"report_{username}*.json"))
    if not json_files:
        json_files = list(output_dir.glob(f"*{username}*.json"))
    if json_files:
        latest = max(json_files, key=lambda f: f.stat().st_mtime)
        with open(latest) as f:
            data = json.load(f)
        print(f"\n✅ 结果已保存: {latest}")
        return data
    else:
        print(f"\n⚠️ 未找到 JSON 输出文件")
        return {"username": username, "sites": [], "error": "no output"}


def store_results(username: str, data: dict):
    conn = sqlite3.connect(str(DB_PATH))

    slug = username.lower().replace(" ", "_")
    conn.execute(
        """
        INSERT OR IGNORE INTO profiles (name, slug, first_seen, last_updated)
        VALUES (?, ?, datetime('now'), datetime('now'))
    """,
        (username, slug),
    )
    conn.execute(
        """
        UPDATE profiles SET last_updated = datetime('now') WHERE slug = ?
    """,
        (slug,),
    )

    sites = data.get("sites", data) if isinstance(data, dict) else data
    found_count = 0

    # 已知误报站点（搜索型页面，非真实账号）
    FALSE_POSITIVE_PREFIXES = ["op.gg", "roblox"]

    if isinstance(sites, dict):
        for site_name, info in sites.items():
            if isinstance(info, dict) and info.get("url_user"):
                url = info["url_user"]
                platform = site_name.lower()

                is_false_positive = (
                    any(
                        platform.startswith(prefix)
                        for prefix in FALSE_POSITIVE_PREFIXES
                    )
                    or "search?" in url
                )

                if is_false_positive:
                    print(f"  ⚠️ 跳过疑似误报: {site_name} → {url}")
                    continue

                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO identity_mappings
                        (profile_slug, platform, platform_username, platform_url, source, confidence)
                        VALUES (?, ?, ?, ?, 'maigret', 0.8)
                    """,
                        (slug, platform, username, url),
                    )
                    found_count += 1
                except Exception as e:
                    print(f"  跳过 {site_name}: {e}")

    conn.commit()
    conn.close()
    print(f"\n📊 存储完成: {found_count} 个平台账号已写入 identity_mappings")
    return found_count


def main():
    if len(sys.argv) < 2:
        print("用法: python3 maigret_search.py <username> [--top N] [--auto-add]")
        sys.exit(1)

    username = sys.argv[1]
    top = 500
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        top = int(sys.argv[idx + 1])

    data = run_maigret(username, top)
    if "error" not in data:
        store_results(username, data)
        if "--auto-add" in sys.argv:
            added = sync_to_watchlist(username)
            if added:
                print(f"✅ 已将 {len(added)} 个新平台同步到 watchlist: {added}")


def sync_to_watchlist(username: str) -> list[str]:
    """
    将 Maigret 发现的平台账号同步到 watchlist.json 中对应人物的 usernames 字段。
    仅同步可采集平台（bilibili, weibo, twitter, youtube, github, reddit）。
    返回新增的平台列表。
    """
    WATCHLIST_PATH = PROJECT_ROOT / "config" / "watchlist.json"
    COLLECTABLE_PLATFORMS = {
        "bilibili",
        "weibo",
        "twitter",
        "youtube",
        "github",
        "reddit",
    }

    slug = username.lower().replace(" ", "_")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    identities = conn.execute(
        "SELECT platform, platform_username, platform_url FROM identity_mappings "
        "WHERE profile_slug = ? AND source = 'maigret'",
        (slug,),
    ).fetchall()
    conn.close()

    if not identities:
        return []

    discovered = {}
    for ident in identities:
        plat = ident["platform"].lower()
        if plat in COLLECTABLE_PLATFORMS:
            discovered[plat] = ident["platform_username"]

    if not discovered:
        return []

    try:
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            watchlist = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    added = []
    for person in watchlist.get("people", []):
        person_slug = person["name"].lower().replace(" ", "_")
        if person_slug == slug or person.get("name", "").lower() == username.lower():
            existing = person.get("usernames", {})
            for plat, uid in discovered.items():
                if plat not in existing:
                    existing[plat] = uid
                    added.append(plat)
            person["usernames"] = existing
            break

    if added:
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(watchlist, f, ensure_ascii=False, indent=2)

    return added


if __name__ == "__main__":
    main()
