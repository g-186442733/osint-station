#!/usr/bin/env python3
"""Generate a business opportunity radar report as styled HTML.

Usage: python3 opportunity_radar.py [--days 7]
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.osint_data import (
    get_db, get_all_recent_posts, call_llm, fmt_number,
    ensure_output_dir, get_style_css_link, md_to_html,
    REPORTS_OUTPUT_DIR, PLATFORM_LABELS, PLATFORM_COLORS,
)


def analyze_opportunities(posts: list[dict], days: int) -> str:
    if not posts:
        return "暂无数据"

    posts_text = "\n".join(
        f"[{p['platform']}] @{p['person_name']} | {p.get('title') or ''} {(p.get('content') or '')[:200]}"
        for p in posts[:50]
    )

    system = f"""你是一位商业情报分析师。从以下近 {days} 天的社交媒体内容中，提取商业机会和行业洞察。

请输出以下结构（Markdown 格式，简体中文）：

### 🔥 本周 TOP 5 商业信号
按商业价值排序，每条包含：信号描述 + 来源人物 + 可执行建议

### 📊 趋势聚类
将相关内容按主题聚合（如"Agent 基础设施"、"开源工具爆发"等），每个主题列出相关帖子和关键人物

### 💡 行动建议
基于以上数据，给出 3-5 条具体可执行的建议（关注什么、跟进什么、警惕什么）

### ⚡ 关键人物动向
哪些人在推动重要趋势，他们最近的核心动作是什么

要求：具体引用内容，不要泛泛而谈。每条建议都要有数据支撑。"""

    return call_llm(posts_text, system, max_tokens=4000) or "分析未能生成"


def render_html(posts: list[dict], analysis: str, days: int) -> str:
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    style_link = get_style_css_link()

    persons = set(p["person_name"] for p in posts)
    platforms = set(p["platform"] for p in posts)

    stat_cards = f"""
    <div class="stat-cards">
        <div class="stat-card red">
            <div class="stat-number">{len(posts)}</div>
            <div class="stat-label">分析帖子</div>
            <div class="stat-desc">近 {days} 天</div>
        </div>
        <div class="stat-card blue">
            <div class="stat-number">{len(persons)}</div>
            <div class="stat-label">涉及人物</div>
            <div class="stat-desc">{', '.join(list(persons)[:5])}</div>
        </div>
        <div class="stat-card orange">
            <div class="stat-number">{len(platforms)}</div>
            <div class="stat-label">数据来源</div>
            <div class="stat-desc">{', '.join(str(PLATFORM_LABELS.get(p, p)) for p in platforms)}</div>
        </div>
        <div class="stat-card green">
            <div class="stat-number">{days}</div>
            <div class="stat-label">扫描天数</div>
            <div class="stat-desc">数据窗口</div>
        </div>
    </div>"""

    top_engagement = sorted(posts, key=lambda p: (p.get("engagement_likes") or 0), reverse=True)[:8]
    top_html = ""
    for p in top_engagement:
        title = p.get("title") or (p.get("content") or "")[:80] or "(无标题)"
        plat = PLATFORM_LABELS.get(p["platform"], p["platform"])
        color = PLATFORM_COLORS.get(p["platform"], "#888")
        url = p.get("url") or "#"
        likes = fmt_number(p.get("engagement_likes") or 0)
        link = f'<a href="{url}" target="_blank">{title}</a>' if url != "#" else title
        top_html += f"""
        <div class="highlight-box">
            <strong><span style="color:{color}">[{plat}]</span> {link}</strong>
            <br><span style="font-size:0.85em;color:#7a7570">@{p['person_name']} · ❤️ {likes}</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>商业机会雷达 — {datetime.now().strftime('%Y.%m.%d')}</title>
    {style_link}
</head>
<body>
    <h1>商业机会雷达</h1>

    <div class="meta-info">
        <p><strong>扫描范围：</strong>近 {days} 天 · {len(posts)} 条帖子 · {len(persons)} 位关注人物</p>
        <p><strong>生成时间：</strong>{now_str}</p>
    </div>

    {stat_cards}

    <h2>商业洞察分析</h2>
    {md_to_html(analysis)}

    <h2>高互动内容</h2>
    {top_html}
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate business opportunity radar")
    parser.add_argument("--days", type=int, default=7, help="Days to scan (default: 7)")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    db = get_db()
    posts = get_all_recent_posts(db, days=args.days, persons_only=True)
    db.close()

    if not posts:
        print(f"近 {args.days} 天内无帖子数据")
        sys.exit(1)

    print(f"扫描到 {len(posts)} 条帖子，正在分析商业机会...")
    analysis = analyze_opportunities(posts, args.days)

    html = render_html(posts, analysis, args.days)

    ensure_output_dir()
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = REPORTS_OUTPUT_DIR / f"商业机会_{date_str}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"已生成: {output_path}")

    if not args.no_open:
        subprocess.run(["open", str(output_path)], check=False)

    return str(output_path)


if __name__ == "__main__":
    main()
