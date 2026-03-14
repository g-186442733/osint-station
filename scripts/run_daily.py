#!/usr/bin/env python3
"""
每日定时任务主入口 — crontab 调用此脚本

流程:
    1. 初始化 / 同步 watchlist 到数据库
    2. 执行各平台采集（B站 → 微博 → Twitter → YouTube → Reddit → HN → GitHub）
    3. LLM 话题分类
    4. 生成情报简报
    5. 推送到企微
    6. (周日) 生成人物情报周报

用法:
    python scripts/run_daily.py
    python scripts/run_daily.py --no-push    # 不推送，仅采集+分析
    python scripts/run_daily.py --brief-only # 仅生成简报+推送（跳过采集）
    python scripts/run_daily.py --hours 48   # 回顾48小时数据
    python scripts/run_daily.py --weekly     # 强制生成周报（不论星期几）
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
)

from config.settings import REPORTS_DIR
from storage.schema import init_db
from etl.pipeline import run_collection
from etl.analyzer import (
    classify_posts,
    generate_daily_brief,
    update_trend_scores,
    translate_titles_to_zh,
)
from reports.pusher import push_daily_brief

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daily_run")


def main():
    parser = argparse.ArgumentParser(description="OSINT 每日定时任务")
    parser.add_argument("--no-push", action="store_true", help="不推送到企微")
    parser.add_argument("--brief-only", action="store_true", help="仅生成简报+推送")
    parser.add_argument("--hours", type=int, default=48, help="简报回顾小时数")
    parser.add_argument("--weekly", action="store_true", help="强制生成周报")
    args = parser.parse_args()

    start_time = datetime.now()
    logger.info(f"{'=' * 50}")
    logger.info(f"🚀 OSINT 每日任务启动 — {start_time.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"{'=' * 50}")

    # 确保数据库已初始化
    init_db()

    # Step 1: 采集数据
    if not args.brief_only:
        logger.info("\n📡 Step 1: 执行数据采集...")
        results = run_collection()
        total_new = sum(
            p_info.get("new", 0) for r in results for p_info in r["platforms"].values()
        )
        logger.info(f"  采集完成: 共新增 {total_new} 条内容")
    else:
        logger.info("\n⏩ Step 1: 跳过采集（--brief-only）")

    # Step 2: 话题分类
    logger.info(f"\n🏷️ Step 2: 话题分类 (最近 {args.hours}h)...")
    classified = classify_posts(hours=args.hours)
    logger.info(f"  分类完成: {classified} 条帖子")

    # Step 2.5: 趋势评分
    logger.info("\n📈 Step 2.5: 更新话题趋势评分...")
    trend_count = update_trend_scores()
    logger.info(f"  趋势评分完成: {trend_count} 个话题")

    # Step 2.6: 翻译英文标题
    logger.info("\n🌐 Step 2.6: 翻译英文标题为中文...")
    translated = translate_titles_to_zh()
    logger.info(f"  翻译完成: {translated} 条标题")

    # Step 3: 生成情报简报
    logger.info(f"\n📝 Step 3: 生成情报简报 (回顾 {args.hours}h)...")
    brief = generate_daily_brief(hours=args.hours)
    logger.info(f"  简报长度: {len(brief)} 字符")

    # 保存简报到文件
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"brief_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    report_file.write_text(brief, encoding="utf-8")
    logger.info(f"  保存至: {report_file}")

    # Step 4: 推送
    if not args.no_push and brief and not brief.startswith("⚠️"):
        logger.info("\n📤 Step 4: 推送情报简报...")
        success = push_daily_brief(brief)
        if success:
            logger.info("  ✅ 推送成功")
        else:
            logger.error("  ❌ 推送失败")
    elif args.no_push:
        logger.info("\n⏩ Step 4: 跳过推送（--no-push）")
    else:
        logger.info("\n⚠️ Step 4: 简报为空或生成失败，跳过推送")

    # Step 5: 周报（周日或 --weekly）
    is_sunday = datetime.now().weekday() == 6
    if args.weekly or is_sunday:
        logger.info("\n📋 Step 5: 生成人物情报周报...")
        from intel.weekly_report import generate_weekly_reports

        weekly_results = generate_weekly_reports()
        ok_count = sum(1 for r in weekly_results if r["status"] == "ok")
        logger.info(f"  周报完成: {ok_count}/{len(weekly_results)} 个报告")
    else:
        logger.info("\n⏩ Step 5: 非周日，跳过周报（用 --weekly 强制生成）")

    # Step 6: 异常检测
    logger.info("\n🚨 Step 6: 异常行为检测...")
    from intel.anomaly import run_anomaly_detection

    anomaly_count = run_anomaly_detection(days=7)
    logger.info(f"  异常检测完成: {anomaly_count} 条告警")

    # 完成
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\n{'=' * 50}")
    logger.info(f"✅ 每日任务完成 — 耗时 {elapsed:.1f}s")
    logger.info(f"{'=' * 50}")


if __name__ == "__main__":
    main()
