#!/usr/bin/env python3
"""Generate a daily intelligence digest as styled HTML.

Usage: python3 daily_digest.py [--days 1] [--no-open]
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.osint_data import (
    get_db, call_llm, fmt_number, is_person_slug,
    ensure_output_dir, get_style_css_link, md_to_html,
    REPORTS_OUTPUT_DIR, PLATFORM_LABELS, PLATFORM_COLORS,
)


def get_digest_posts(db, days: int = 1):
    """获取近 N 天的帖子，分为人物动态和行业资讯"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT p.profile_slug, COALESCE(pr.name, p.profile_slug) as person_name,
                  p.platform, COALESCE(p.title_zh, p.title) as title, p.content, p.url,
                  p.published_at, p.engagement_likes, p.engagement_comments, p.engagement_views,
                  t.name as topic_name
           FROM posts p
           LEFT JOIN profiles pr ON p.profile_slug = pr.slug
           LEFT JOIN topics t ON p.topic_id = t.id
           WHERE COALESCE(NULLIF(p.published_at, ''), p.collected_at) > ?
           ORDER BY COALESCE(NULLIF(p.published_at, ''), p.collected_at) DESC""",
        (cutoff,),
    ).fetchall()
    all_posts = [dict(r) for r in rows]
    person_posts = [p for p in all_posts if is_person_slug(p["profile_slug"])]
    industry_posts = [p for p in all_posts if not is_person_slug(p["profile_slug"])]
    return person_posts, industry_posts


def analyze_digest(person_posts, industry_posts, days):
    if not person_posts and not industry_posts:
        return "今日暂无新数据"

    # 准备人物动态摘要
    person_text = "\n".join(
        f"[@{p['person_name']}] [{p['platform']}] {p.get('title') or (p.get('content') or '')[:150]}"
        for p in person_posts[:40]
    )
    # 准备行业资讯摘要
    industry_text = "\n".join(
        f"[{p['platform']}] {p.get('title') or (p.get('content') or '')[:150]}"
        for p in sorted(industry_posts, key=lambda x: (x.get("engagement_likes") or 0), reverse=True)[:20]
    )

    system = f"""你是一位情报分析师，需要撰写近 {days} 天的每日情报摘要。

请输出以下结构（Markdown 格式，简体中文）：

### 📌 今日要点
5-7 条最重要的信息，每条一句话概括（优先关注人物动态 > 行业趋势）

### 👤 人物动态
按人物分组，每人 1-3 句话概括其近期动态。没有动态的不列出。

### 🔥 值得深读
从所有内容中选出 3-5 条最值得仔细阅读的帖子，附理由说明（如：影响力大/观点独特/趋势信号）

### ⚡ 异常信号
任何异常情况：某人突然沉默/高频发帖、新话题突然出现、互动量异常等

要求：简洁有力，不泛泛而谈，引用具体内容。如果某个维度没有内容，说"无"。"""

    full_text = f"## 关注人物动态\n{person_text}\n\n## 行业资讯\n{industry_text}"
    return call_llm(full_text, system, max_tokens=3000) or "分析未能生成"


def render_html(person_posts, industry_posts, analysis, days):
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    style_link = get_style_css_link()

    total = len(person_posts) + len(industry_posts)
    active_persons = set(p["person_name"] for p in person_posts)
    platforms = set(p["platform"] for p in person_posts + industry_posts)

    # 统计卡片
    stat_cards = f"""
    <div class="stat-cards">
        <div class="stat-card blue">
            <div class="stat-number">{len(person_posts)}</div>
            <div class="stat-label">人物动态</div>
            <div class="stat-desc">{len(active_persons)} 位活跃人物</div>
        </div>
        <div class="stat-card orange">
            <div class="stat-number">{len(industry_posts)}</div>
            <div class="stat-label">行业资讯</div>
            <div class="stat-desc">HN/Reddit/话题</div>
        </div>
        <div class="stat-card green">
            <div class="stat-number">{len(platforms)}</div>
            <div class="stat-label">数据来源</div>
            <div class="stat-desc">{', '.join(PLATFORM_LABELS.get(p, p) or p for p in list(platforms)[:5])}</div>
        </div>
        <div class="stat-card red">
            <div class="stat-number">{total}</div>
            <div class="stat-label">总计内容</div>
            <div class="stat-desc">近 {days} 天</div>
        </div>
    </div>"""

    # 人物动态 TOP（按互动量排序）
    top_person_html = ""
    sorted_person = sorted(person_posts, key=lambda p: (p.get("engagement_likes") or 0) + (p.get("engagement_views") or 0) / 100, reverse=True)
    for p in sorted_person[:10]:
        title = p.get("title") or (p.get("content") or "")[:80] or "(无标题)"
        plat = PLATFORM_LABELS.get(p["platform"], p["platform"])
        color = PLATFORM_COLORS.get(p["platform"], "#888")
        url = p.get("url") or "#"
        likes = fmt_number(p.get("engagement_likes") or 0)
        views = fmt_number(p.get("engagement_views") or 0)
        link = f'<a href="{url}" target="_blank">{title}</a>' if url != "#" else title
        engagement = f"❤️ {likes}" + (f" · 👁 {views}" if (p.get("engagement_views") or 0) > 0 else "")
        top_person_html += f"""
        <div class="highlight-box">
            <strong><span style="color:{color}">[{plat}]</span> {link}</strong>
            <br><span style="font-size:0.85em;color:#7a7570">@{p['person_name']} · {engagement}</span>
        </div>"""

    # 行业热门
    top_industry_html = ""
    sorted_industry = sorted(industry_posts, key=lambda p: (p.get("engagement_likes") or 0), reverse=True)
    for p in sorted_industry[:5]:
        title = p.get("title") or (p.get("content") or "")[:80] or "(无标题)"
        plat = PLATFORM_LABELS.get(p["platform"], p["platform"])
        color = PLATFORM_COLORS.get(p["platform"], "#888")
        url = p.get("url") or "#"
        likes = fmt_number(p.get("engagement_likes") or 0)
        link = f'<a href="{url}" target="_blank">{title}</a>' if url != "#" else title
        top_industry_html += f"""
        <div class="highlight-box">
            <strong><span style="color:{color}">[{plat}]</span> {link}</strong>
            <br><span style="font-size:0.85em;color:#7a7570">❤️ {likes}</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日情报摘要 — {datetime.now().strftime('%Y.%m.%d')}</title>
    {style_link}
</head>
<body>
    <h1>每日情报摘要</h1>

    <div class="meta-info">
        <p><strong>数据范围：</strong>近 {days} 天 · {total} 条内容 · {len(active_persons)} 位活跃人物</p>
        <p><strong>生成时间：</strong>{now_str}</p>
    </div>

    {stat_cards}

    <h2>情报分析</h2>
    {md_to_html(analysis)}

    <h2>人物动态 · 高互动内容</h2>
    {top_person_html if top_person_html else '<p style="color:#999">今日暂无人物动态</p>'}

    <h2>行业资讯 · 热门内容</h2>
    {top_industry_html if top_industry_html else '<p style="color:#999">今日暂无行业资讯</p>'}
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate daily intelligence digest")
    parser.add_argument("--days", type=int, default=1, help="Days to look back (default: 1)")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    db = get_db()
    person_posts, industry_posts = get_digest_posts(db, days=args.days)
    db.close()

    total = len(person_posts) + len(industry_posts)
    if total == 0:
        print(f"近 {args.days} 天内无新数据")
        sys.exit(1)

    print(f"扫描到 {len(person_posts)} 条人物动态 + {len(industry_posts)} 条行业资讯")
    print("正在生成情报摘要...")
    analysis = analyze_digest(person_posts, industry_posts, args.days)

    html = render_html(person_posts, industry_posts, analysis, args.days)

    ensure_output_dir()
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = REPORTS_OUTPUT_DIR / f"每日情报_{date_str}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"已生成: {output_path}")

    if not args.no_open:
        subprocess.run(["open", str(output_path)], check=False)

    return str(output_path)


if __name__ == "__main__":
    main()
