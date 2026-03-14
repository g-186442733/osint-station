"""
异常告警检测引擎

检测类型:
  1. 发帖频率异常 (突然高频或沉默)
  2. 互动量异常 (点赞/评论突然飙升)
  3. 新平台出现 (人物首次在某平台发帖)
"""

import sqlite3
import logging
from datetime import datetime, timedelta

from config.settings import DB_PATH

logger = logging.getLogger(__name__)

SPIKE_THRESHOLD = 3.0
SILENCE_DAYS = 14


def detect_anomalies(days: int = 7) -> list[dict]:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    alerts = []
    now = datetime.now()
    recent_cutoff = (now - timedelta(days=days)).isoformat()
    baseline_start = (now - timedelta(days=days * 4)).isoformat()
    baseline_end = (now - timedelta(days=days)).isoformat()

    profiles = db.execute(
        "SELECT slug, name, priority FROM profiles WHERE priority IN ('high', 'medium')"
    ).fetchall()

    for prof in profiles:
        slug = prof["slug"]
        name = prof["name"]

        recent_posts = db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(AVG(engagement_likes), 0) as avg_likes, "
            "COALESCE(AVG(engagement_comments), 0) as avg_comments, "
            "COALESCE(AVG(engagement_views), 0) as avg_views "
            "FROM posts WHERE profile_slug = ? AND collected_at > ?",
            (slug, recent_cutoff),
        ).fetchone()

        baseline = db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(AVG(engagement_likes), 0) as avg_likes, "
            "COALESCE(AVG(engagement_comments), 0) as avg_comments, "
            "COALESCE(AVG(engagement_views), 0) as avg_views "
            "FROM posts WHERE profile_slug = ? AND collected_at > ? AND collected_at <= ?",
            (slug, baseline_start, baseline_end),
        ).fetchone()

        baseline_weekly = (baseline["cnt"] or 0) / 3.0 if baseline["cnt"] else 0
        recent_count = recent_posts["cnt"] or 0

        if baseline_weekly > 0 and recent_count > baseline_weekly * SPIKE_THRESHOLD:
            alerts.append(
                {
                    "profile_slug": slug,
                    "profile_name": name,
                    "alert_type": "posting_spike",
                    "severity": "high",
                    "title": f"{name} 发帖频率异常飙升",
                    "detail": f"最近{days}天发帖 {recent_count} 条，基线周均 {baseline_weekly:.1f} 条 ({recent_count / baseline_weekly:.1f}x)",
                }
            )

        if baseline_weekly > 2 and recent_count == 0:
            last_post = db.execute(
                "SELECT MAX(collected_at) as last FROM posts WHERE profile_slug = ?",
                (slug,),
            ).fetchone()
            last_dt = last_post["last"] if last_post else None
            if last_dt:
                try:
                    days_silent = (now - datetime.fromisoformat(last_dt)).days
                    if days_silent >= SILENCE_DAYS:
                        alerts.append(
                            {
                                "profile_slug": slug,
                                "profile_name": name,
                                "alert_type": "silence",
                                "severity": "medium",
                                "title": f"{name} 已沉默 {days_silent} 天",
                                "detail": f"基线周均发帖 {baseline_weekly:.1f} 条，但已 {days_silent} 天无新内容",
                            }
                        )
                except (ValueError, TypeError):
                    pass

        if baseline["avg_likes"] > 0:
            likes_ratio = (recent_posts["avg_likes"] or 0) / baseline["avg_likes"]
            if likes_ratio > SPIKE_THRESHOLD:
                alerts.append(
                    {
                        "profile_slug": slug,
                        "profile_name": name,
                        "alert_type": "engagement_spike",
                        "severity": "high",
                        "title": f"{name} 互动量异常飙升",
                        "detail": f"最近{days}天平均点赞 {recent_posts['avg_likes']:.0f}，基线 {baseline['avg_likes']:.0f} ({likes_ratio:.1f}x)",
                    }
                )

        recent_platforms = db.execute(
            "SELECT DISTINCT platform FROM posts WHERE profile_slug = ? AND collected_at > ?",
            (slug, recent_cutoff),
        ).fetchall()
        all_platforms = db.execute(
            "SELECT DISTINCT platform FROM posts WHERE profile_slug = ? AND collected_at <= ?",
            (slug, recent_cutoff),
        ).fetchall()
        recent_set = {r["platform"] for r in recent_platforms}
        old_set = {r["platform"] for r in all_platforms}
        new_platforms = recent_set - old_set
        if new_platforms:
            alerts.append(
                {
                    "profile_slug": slug,
                    "profile_name": name,
                    "alert_type": "new_platform",
                    "severity": "medium",
                    "title": f"{name} 新平台出现: {', '.join(new_platforms)}",
                    "detail": f"首次在 {', '.join(new_platforms)} 发现内容",
                }
            )

    db.close()
    logger.info(f"异常检测完成，发现 {len(alerts)} 条告警")
    return alerts


def run_anomaly_detection(days: int = 7) -> int:
    from storage.db import Database

    anomalies = detect_anomalies(days)
    if not anomalies:
        return 0

    db_ops = Database()
    stored = 0
    for a in anomalies:
        db_ops.insert_alert(
            alert_type="anomaly_alert",
            title=a["title"],
            content=a["detail"],
            related_profiles=[a["profile_slug"]],
        )
        stored += 1

    logger.info(f"已存储 {stored} 条异常告警")
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    alerts = detect_anomalies(days=7)
    print(f"\n发现 {len(alerts)} 条异常:")
    for a in alerts:
        print(f"  [{a['severity']}] {a['title']}")
        print(f"    {a['detail']}")
