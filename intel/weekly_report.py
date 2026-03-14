"""
人物情报周报生成器

每周自动为高优先级人物生成情报摘要：
  1. 遍历 watchlist 中 priority=high 的人物
  2. 调用 profiler.run_profile_intel() 获取完整分析
  3. 生成 Markdown 报告保存到 reports/weekly/
  4. 写入 alerts 表 (alert_type='weekly_intel')
  5. 可通过 Dashboard /reports 页面查看
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from config.settings import DB_PATH, REPORTS_DIR, WATCHLIST_PATH
from intel.profiler import run_profile_intel

logger = logging.getLogger(__name__)

WEEKLY_DIR = REPORTS_DIR / "weekly"


def _load_high_priority_people() -> list[dict]:
    """从watchlist加载高优先级人物"""
    try:
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            watchlist = json.load(f)
    except Exception as e:
        logger.error(f"无法加载watchlist: {e}")
        return []

    return [p for p in watchlist.get("people", []) if p.get("priority") == "high"]


def _format_intel_markdown(intel: dict) -> str:
    """将 profiler 结果格式化为 Markdown 报告"""
    name = intel.get("name", "未知")
    slug = intel.get("slug", "")
    generated = intel.get("generated_at", "")

    lines = [
        f"# 📋 人物情报报告: {name}",
        f"",
        f"> 生成时间: {generated}",
        f"> 标识: @{slug}",
        f"",
    ]

    # Bio
    bio = intel.get("bio", "")
    if bio:
        lines.extend([f"**简介**: {bio}", ""])

    # Influence stats
    inf = intel.get("influence", {})
    if inf.get("total_posts", 0) > 0:
        lines.extend(
            [
                "## 📊 影响力指标",
                "",
                f"| 指标 | 数值 |",
                f"|------|------|",
                f"| 采集帖子 | {inf.get('total_posts', 0)} |",
                f"| 覆盖平台 | {inf.get('platform_count', 0)} ({', '.join(inf.get('platforms', []))}) |",
                f"| 平均点赞 | {inf.get('avg_likes', 0)} |",
                f"| 平均播放 | {inf.get('avg_views', 0)} |",
                f"| 总点赞 | {inf.get('total_likes', 0)} |",
                f"| 总播放 | {inf.get('total_views', 0)} |",
                "",
            ]
        )

        # Per platform breakdown
        bp = inf.get("by_platform", {})
        if bp:
            lines.append("### 各平台数据")
            lines.append("")
            lines.append("| 平台 | 帖子数 | 点赞 | 播放 | 评论 |")
            lines.append("|------|--------|------|------|------|")
            for plat, stats in bp.items():
                lines.append(
                    f"| {plat} | {stats.get('count', 0)} | {stats.get('likes', 0)} | {stats.get('views', 0)} | {stats.get('comments', 0)} |"
                )
            lines.append("")

    # Identity map
    identity = intel.get("identity", [])
    if identity:
        lines.extend(
            [
                "## 🌐 跨平台身份",
                "",
                "| 平台 | 用户名 | 置信度 | 来源 |",
                "|------|--------|--------|------|",
            ]
        )
        for id_entry in identity:
            conf = f"{int((id_entry.get('confidence', 0)) * 100)}%"
            url = id_entry.get("platform_url", "")
            uname = id_entry.get("platform_username", "-")
            if url:
                uname = f"[{uname}]({url})"
            lines.append(
                f"| {id_entry.get('platform', '-')} | {uname} | {conf} | {id_entry.get('source', '-')} |"
            )
        lines.append("")

    # Relationships
    rel = intel.get("relationships", {})
    top_interact = rel.get("top_interactions", {})
    if top_interact:
        lines.extend(
            [
                "## 🕸️ 关系网络",
                "",
                f"独立联系人数: {rel.get('unique_contacts', 0)}",
                "",
                "**高频互动对象:**",
                "",
            ]
        )
        for username, count in list(top_interact.items())[:10]:
            mention_count = rel.get("mentions", {}).get(username, 0)
            rt_count = rel.get("retweets", {}).get(username, 0)
            tags = []
            if mention_count:
                tags.append(f"提及{mention_count}次")
            if rt_count:
                tags.append(f"转发{rt_count}次")
            lines.append(
                f"- **@{username}** ({count}次互动) — {', '.join(tags) if tags else '互动'}"
            )
        lines.append("")

    # Viewpoints
    viewpoints = intel.get("viewpoints", "")
    if viewpoints and viewpoints != "暂无内容数据" and viewpoints != "分析失败":
        lines.extend(
            [
                "## 🧠 观点分析",
                "",
                viewpoints,
                "",
            ]
        )

    # Recent activity
    timeline = intel.get("timeline", [])
    if timeline:
        lines.extend(
            [
                "## 📅 近期活动",
                "",
            ]
        )
        for item in timeline[:10]:
            title = item.get("title") or (item.get("content") or "")[:60]
            platform = item.get("platform", "")
            date = item.get("published_at", "")[:10] if item.get("published_at") else ""
            url = item.get("url", "")
            if url and title:
                lines.append(f"- [{platform}] {date} — [{title}]({url})")
            elif title:
                lines.append(f"- [{platform}] {date} — {title}")
        lines.append("")

    lines.append("---")
    lines.append(f"*报告由 OSINT 工作站自动生成*")

    return "\n".join(lines)


def generate_weekly_reports(people_filter: list[str] | None = None) -> list[dict]:
    """
    为高优先级人物生成周报。

    Args:
        people_filter: 指定人物名称列表，None则使用watchlist中所有high priority人物

    Returns:
        [{"name": str, "slug": str, "report_file": str, "status": str}, ...]
    """
    if people_filter:
        people_names = people_filter
    else:
        high_priority = _load_high_priority_people()
        people_names = [p["name"] for p in high_priority]

    if not people_names:
        logger.warning("[weekly] 无高优先级人物可生成报告")
        return []

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    results = []
    date_str = datetime.now().strftime("%Y%m%d")

    for name in people_names:
        logger.info(f"[weekly] 生成 {name} 的情报报告...")
        try:
            intel = run_profile_intel(name)

            if intel.get("error"):
                logger.warning(f"[weekly] {name}: {intel['error']}")
                results.append(
                    {"name": name, "status": "error", "error": intel["error"]}
                )
                continue

            # Generate markdown
            md_content = _format_intel_markdown(intel)

            # Save to file
            slug = intel.get("slug", name.lower().replace(" ", "_"))
            filename = f"{date_str}_{slug}.md"
            filepath = WEEKLY_DIR / filename
            filepath.write_text(md_content, encoding="utf-8")

            # Save to alerts table
            db.execute(
                """INSERT INTO alerts (alert_type, title, content, related_profiles, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "weekly_intel",
                    f"周报: {name}",
                    md_content,
                    json.dumps([slug]),
                    datetime.now().isoformat(),
                ),
            )
            db.commit()

            results.append(
                {
                    "name": name,
                    "slug": slug,
                    "report_file": str(filepath),
                    "status": "ok",
                    "report_length": len(md_content),
                }
            )
            logger.info(f"[weekly] {name}: 报告已保存 ({len(md_content)} 字符)")

        except Exception as e:
            logger.error(f"[weekly] {name} 生成失败: {e}")
            results.append({"name": name, "status": "error", "error": str(e)})

    db.close()

    ok_count = sum(1 for r in results if r["status"] == "ok")
    logger.info(f"[weekly] 完成: {ok_count}/{len(results)} 个报告生成成功")
    return results


def list_reports(limit: int = 50) -> list[dict]:
    """列出已生成的周报"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    rows = db.execute(
        """SELECT id, title, content, related_profiles, created_at
           FROM alerts WHERE alert_type = 'weekly_intel'
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    db.close()

    return [
        {
            "id": r["id"],
            "title": r["title"],
            "content": r["content"],
            "related_profiles": r["related_profiles"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_report(report_id: int) -> dict | None:
    """获取单个报告"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    row = db.execute(
        "SELECT id, title, content, related_profiles, created_at FROM alerts WHERE id = ? AND alert_type = 'weekly_intel'",
        (report_id,),
    ).fetchone()
    db.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "related_profiles": row["related_profiles"],
        "created_at": row["created_at"],
    }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    # Allow specifying people from CLI
    if len(sys.argv) > 1:
        people = sys.argv[1:]
        results = generate_weekly_reports(people_filter=people)
    else:
        results = generate_weekly_reports()

    for r in results:
        status = "✅" if r["status"] == "ok" else "❌"
        print(f"  {status} {r['name']}: {r.get('report_file', r.get('error', ''))}")
