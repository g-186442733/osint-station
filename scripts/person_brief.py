#!/usr/bin/env python3
"""Generate a person intelligence brief as styled HTML.

Usage: python3 person_brief.py "林亦LYi" [--days 30]
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.osint_data import (
    get_db, resolve_person, get_person_posts, compute_person_stats,
    call_llm, fmt_number, ensure_output_dir, get_style_css_link,
    md_to_html, REPORTS_OUTPUT_DIR, PLATFORM_LABELS, PLATFORM_COLORS,
)


def analyze_viewpoints(name: str, posts: list[dict]) -> str:
    if not posts:
        return "暂无足够数据进行分析"

    posts_text = "\n".join(
        f"[{p['platform']}] {(p.get('published_at') or '?')[:10]} | "
        f"{p.get('title') or ''} {(p.get('content') or '')[:200]}"
        for p in posts[:30]
    )

    system = f"""你是一位资深情报分析师。分析 {name} 的近期社交媒体内容，输出以下结构化分析（Markdown 格式，简体中文）：

### 核心关注领域
列出 3-5 个此人最关心的话题方向，每个附一句话说明

### 重要观点与立场
提炼 3-5 个最有价值的观点/判断，引用具体帖子内容

### 近期动态摘要
概括此人最近在做什么、发布什么、有什么值得注意的行为变化

### 商业信号与机会
如果存在任何商业洞察、投资动向、产品发布等信号，在此列出

要求：简洁有力，每个要点 1-2 句话。不要泛泛而谈，要有具体内容引用。"""

    return call_llm(posts_text, system) or "分析未能生成"


def detect_anomalies(posts: list[dict], days: int) -> list[str]:
    if len(posts) < 3:
        return []

    signals = []
    dates = [
        (p.get("published_at") or p.get("collected_at", ""))[:10]
        for p in posts if (p.get("published_at") or p.get("collected_at"))
    ]
    if dates:
        unique_dates = sorted(set(dates))
        if len(unique_dates) >= 2:
            recent_3d = sum(1 for d in dates if d >= unique_dates[-1][:8] + "01")
            if recent_3d > len(posts) * 0.6:
                signals.append("近期发帖频率显著上升")

    platforms = set(p["platform"] for p in posts)
    if len(platforms) >= 4:
        signals.append(f"跨 {len(platforms)} 个平台活跃")

    high_engagement = [p for p in posts if (p.get("engagement_likes") or 0) > 1000]
    if high_engagement:
        signals.append(f"{len(high_engagement)} 条帖子获得超千赞")

    return signals


def render_html(name: str, slug: str, posts: list[dict], stats: dict,
                analysis: str, anomalies: list[str], days: int) -> str:
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    style_link = get_style_css_link()

    # Platform tags
    plat_tags = " ".join(
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:0.8em;'
        f'background:{PLATFORM_COLORS.get(p, "#888")};color:white;font-weight:600">'
        f'{PLATFORM_LABELS.get(p, p)}</span>'
        for p in stats.get("platforms", [])
    )

    # Stat cards
    stat_cards = f"""
    <div class="stat-cards">
        <div class="stat-card red">
            <div class="stat-number">{stats['total']}</div>
            <div class="stat-label">采集帖子</div>
            <div class="stat-desc">近 {days} 天</div>
        </div>
        <div class="stat-card blue">
            <div class="stat-number">{stats['platform_count']}</div>
            <div class="stat-label">覆盖平台</div>
            <div class="stat-desc">{', '.join(str(PLATFORM_LABELS.get(p, p)) for p in stats.get('platforms', []))}</div>
        </div>
        <div class="stat-card orange">
            <div class="stat-number">{fmt_number(stats['avg_likes'])}</div>
            <div class="stat-label">平均点赞</div>
            <div class="stat-desc">总计 {fmt_number(stats['total_likes'])}</div>
        </div>
        <div class="stat-card green">
            <div class="stat-number">{stats['active_days']}</div>
            <div class="stat-label">活跃天数</div>
            <div class="stat-desc">近 {days} 天内</div>
        </div>
    </div>"""

    # Anomaly callouts
    anomaly_html = ""
    if anomalies:
        items = "".join(f"<li>{s}</li>" for s in anomalies)
        anomaly_html = f"""
    <div class="callout warning">
        <div class="callout-icon">⚠️</div>
        <div class="callout-content">
            <strong>异常信号检测</strong>
            <ul>{items}</ul>
        </div>
    </div>"""

    analysis_section = f'<div class="viewpoints-content">{md_to_html(analysis)}</div>'

    # Top posts
    top_posts_html = ""
    sorted_posts = sorted(posts, key=lambda p: (p.get("engagement_likes") or 0) + (p.get("engagement_views") or 0) / 100, reverse=True)
    for p in sorted_posts[:5]:
        title = p.get("title") or (p.get("content") or "")[:80] or "(无标题)"
        plat = PLATFORM_LABELS.get(p["platform"], p["platform"])
        color = PLATFORM_COLORS.get(p["platform"], "#888")
        url = p.get("url") or "#"
        likes = fmt_number(p.get("engagement_likes") or 0)
        views = fmt_number(p.get("engagement_views") or 0)
        date = (p.get("published_at") or "")[:10]

        top_posts_html += f"""
        <div class="highlight-box">
            <strong><span style="color:{color}">[{plat}]</span> <a href="{url}" target="_blank">{title}</a></strong>
            <br><span style="font-size:0.85em;color:#7a7570">{date} · ❤️ {likes} · 👁 {views}</span>
        </div>"""

    # Timeline
    timeline_html = ""
    for p in posts[:15]:
        title = p.get("title") or (p.get("content") or "")[:80] or "(无标题)"
        plat = PLATFORM_LABELS.get(p["platform"], p["platform"])
        color = PLATFORM_COLORS.get(p["platform"], "#888")
        url = p.get("url") or "#"
        date = (p.get("published_at") or p.get("collected_at") or "")[:10]
        likes = p.get("engagement_likes") or 0

        link = f'<a href="{url}" target="_blank">{title}</a>' if url != "#" else title
        engagement = f' · ❤️ {fmt_number(likes)}' if likes else ""

        timeline_html += f"""
        <div class="timeline-item">
            <div class="timeline-time">{date} · <span style="color:{color}">{plat}</span></div>
            <div class="timeline-content">{link}{engagement}</div>
        </div>"""

    # Platform breakdown
    platform_breakdown = ""
    for plat, count in stats.get("by_platform", {}).items():
        label = PLATFORM_LABELS.get(plat, plat)
        color = PLATFORM_COLORS.get(plat, "#888")
        platform_breakdown += f'<span style="margin-right:12px"><span style="color:{color};font-weight:700">{label}</span> {count}条</span>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} — 人物情报速报</title>
    {style_link}
</head>
<body>
    <h1>{name} — 人物情报速报</h1>

    <div class="meta-info">
        <p><strong>分析对象：</strong>{name} (@{slug})</p>
        <p><strong>数据范围：</strong>近 {days} 天 · {stats['total']} 条帖子 · {stats['platform_count']} 个平台</p>
        <p><strong>生成时间：</strong>{now_str}</p>
        <p>{plat_tags}</p>
    </div>

    <section class="conclusion">
        <h3>核心发现</h3>
        <ul>
            <li>近 {days} 天在 {stats['platform_count']} 个平台产出 {stats['total']} 条内容，活跃 {stats['active_days']} 天</li>
            <li>平均每条内容获得 {fmt_number(stats['avg_likes'])} 点赞、{fmt_number(stats['avg_views'])} 浏览</li>
            <li>平台分布：{platform_breakdown}</li>
        </ul>
    </section>

    {stat_cards}

    {anomaly_html}

    <h2>观点分析与洞察</h2>
    {analysis_section}

    <h2>高互动内容 TOP 5</h2>
    {top_posts_html}

    <h2>活跃时间线</h2>
    <div class="timeline">
        {timeline_html}
    </div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate person intelligence brief")
    parser.add_argument("name", help="Person name or slug")
    parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open in browser")
    args = parser.parse_args()

    db = get_db()
    resolved = resolve_person(args.name, db)
    if not resolved:
        print(f"未找到人物: {args.name}")
        print("可用人物：")
        for r in db.execute("SELECT name, slug FROM profiles ORDER BY name").fetchall():
            print(f"  {r['name']} ({r['slug']})")
        db.close()
        sys.exit(1)

    name, slug = resolved
    print(f"正在分析: {name} (@{slug})...")

    posts = get_person_posts(slug, db, days=args.days)
    if not posts:
        print(f"近 {args.days} 天内无帖子数据")
        db.close()
        sys.exit(1)

    stats = compute_person_stats(posts)
    print(f"  找到 {stats['total']} 条帖子，{stats['platform_count']} 个平台")

    print("  正在调用 LLM 分析观点...")
    analysis = analyze_viewpoints(name, posts)

    anomalies = detect_anomalies(posts, args.days)
    if anomalies:
        print(f"  检测到 {len(anomalies)} 个异常信号")

    html = render_html(name, slug, posts, stats, analysis, anomalies, args.days)

    ensure_output_dir()
    date_str = datetime.now().strftime("%Y%m%d")
    safe_name = name.replace(" ", "_").replace("/", "_")
    output_path = REPORTS_OUTPUT_DIR / f"{safe_name}_{date_str}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"  已生成: {output_path}")

    db.close()

    if not args.no_open:
        subprocess.run(["open", str(output_path)], check=False)

    return str(output_path)


if __name__ == "__main__":
    main()
