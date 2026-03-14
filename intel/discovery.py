"""
关系网络自动发现

扫描所有帖子中的@提及、转发、回复对象：
  1. 统计每个被提及者的频率
  2. 排除已在watchlist中的人
  3. 用LLM判断是否AI领域相关
  4. 按频率排名，返回推荐关注列表
"""

import re
import json
import sqlite3
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config.settings import DB_PATH, LLM_API_BASE, LLM_API_KEY, MODELS, WATCHLIST_PATH

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"@(\w{1,30})")
RT_PATTERN = re.compile(r"^RT @(\w+):")

# GitHub patterns: mentions in commit messages, PR references
GH_MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38})")


def _get_watched_usernames() -> set[str]:
    """从watchlist中提取所有已关注的用户名（小写）"""
    try:
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            watchlist = json.load(f)
    except Exception:
        return set()

    watched = set()
    for person in watchlist.get("people", []):
        # Add the person name slug
        watched.add(person["name"].lower().replace(" ", "_"))
        # Add all platform usernames
        for _plat, uid in person.get("usernames", {}).items():
            if uid:
                watched.add(str(uid).lower())
    return watched


def _call_llm(prompt: str, system: str = "") -> str:
    models = [MODELS["daily_summary"], MODELS["fallback"]]
    for m in models:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                f"{LLM_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": m,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 2000,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"LLM call failed (model={m}): {e}")
    return ""


def scan_mentions(days: int = 30, min_count: int = 2) -> list[dict]:
    """
    扫描最近N天的帖子，提取所有@提及和转发对象。

    返回: [{"username": str, "count": int, "platforms": list, "sample_contexts": list}, ...]
    按count降序，已排除watchlist中的人。
    """
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    since = (datetime.now() - timedelta(days=days)).isoformat()
    posts = db.execute(
        "SELECT profile_slug, platform, content, raw_json FROM posts WHERE collected_at > ?",
        (since,),
    ).fetchall()

    watched = _get_watched_usernames()

    # Count mentions with platform tracking and sample contexts
    mention_data: dict[str, dict] = {}  # username -> {count, platforms, contexts}

    for p in posts:
        text = p["content"] or ""
        platform = p["platform"]

        found_names = set()

        # RT pattern
        rt = RT_PATTERN.match(text)
        if rt:
            found_names.add(rt.group(1).lower())

        # @mention pattern
        for m in MENTION_PATTERN.findall(text):
            found_names.add(m.lower())

        # Raw JSON retweet info
        raw = p["raw_json"] or ""
        if raw:
            try:
                raw_data = json.loads(raw)
                retweeted = raw_data.get("retweeted_status", {})
                if isinstance(retweeted, dict):
                    rt_user = retweeted.get("user", {})
                    if isinstance(rt_user, dict) and rt_user.get("screen_name"):
                        found_names.add(rt_user["screen_name"].lower())
            except (json.JSONDecodeError, AttributeError):
                pass

        # Store each mention
        for name in found_names:
            # Skip self-mentions and watched people
            if name in watched:
                continue
            # Skip very short or numeric-only usernames (noise)
            if len(name) < 2 or name.isdigit():
                continue

            if name not in mention_data:
                mention_data[name] = {
                    "count": 0,
                    "platforms": set(),
                    "contexts": [],
                    "mentioned_by": set(),
                }
            mention_data[name]["count"] += 1
            mention_data[name]["platforms"].add(platform)
            mention_data[name]["mentioned_by"].add(p["profile_slug"] or "unknown")
            # Keep up to 3 sample contexts
            if len(mention_data[name]["contexts"]) < 3:
                snippet = text[:150].strip()
                if snippet:
                    mention_data[name]["contexts"].append(snippet)

    db.close()

    # Filter by min_count and sort
    results = []
    for username, data in mention_data.items():
        if data["count"] >= min_count:
            results.append(
                {
                    "username": username,
                    "count": data["count"],
                    "platforms": sorted(data["platforms"]),
                    "mentioned_by": sorted(data["mentioned_by"]),
                    "sample_contexts": data["contexts"],
                }
            )

    results.sort(key=lambda x: x["count"], reverse=True)
    return results


def filter_ai_relevant(candidates: list[dict], top_n: int = 30) -> list[dict]:
    """
    使用LLM过滤出AI领域相关的人物。

    输入: scan_mentions的结果（前top_n个）
    输出: 每个候选人增加 ai_relevant (bool) 和 relevance_note (str) 字段
    """
    if not candidates:
        return []

    # Take top N for LLM evaluation
    to_evaluate = candidates[:top_n]

    # Build prompt
    entries = []
    for i, c in enumerate(to_evaluate):
        contexts = (
            " | ".join(c["sample_contexts"][:2]) if c["sample_contexts"] else "无上下文"
        )
        entries.append(
            f"{i + 1}. @{c['username']} (被提及{c['count']}次, 平台:{','.join(c['platforms'])}) 上下文: {contexts}"
        )

    system = """你是一个AI领域情报分析师。判断以下被频繁提及的社交媒体用户是否与AI/人工智能/机器学习/科技创业领域相关。

对每个用户输出一行JSON格式:
{"index": 数字, "relevant": true/false, "note": "简短说明原因"}

注意：
- 如果上下文中包含AI/ML/LLM/GPT/模型/训练等关键词，倾向判为相关
- 知名AI研究者、AI公司CEO/CTO、科技KOL判为相关
- 纯娱乐/生活博主判为不相关
- 无法判断时标记为相关（宁可误收不可漏掉）

只输出JSON行，不要其他文字。"""

    prompt = "以下是需要判断的用户列表:\n\n" + "\n".join(entries)

    llm_result = _call_llm(prompt, system)

    # Parse LLM response
    relevance_map = {}
    if llm_result:
        for line in llm_result.strip().split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
                idx = obj.get("index", 0)
                relevance_map[idx] = {
                    "relevant": obj.get("relevant", True),
                    "note": obj.get("note", ""),
                }
            except json.JSONDecodeError:
                continue

    # Merge results
    for i, c in enumerate(to_evaluate):
        info = relevance_map.get(
            i + 1, {"relevant": True, "note": "未能评估，默认保留"}
        )
        c["ai_relevant"] = info["relevant"]
        c["relevance_note"] = info["note"]

    # Return only relevant ones
    return [c for c in to_evaluate if c.get("ai_relevant", True)]


def discover_new_people(
    days: int = 30, min_count: int = 2, use_llm: bool = True
) -> dict:
    """
    完整的发现流程：扫描 → 过滤 → 返回推荐列表

    返回:
    {
        "scan_date": str,
        "total_mentions_scanned": int,
        "candidates_before_filter": int,
        "recommendations": [...],
    }
    """
    logger.info(f"[discovery] 扫描最近{days}天的提及数据...")

    all_candidates = scan_mentions(days=days, min_count=min_count)
    logger.info(f"[discovery] 发现 {len(all_candidates)} 个未关注的被频繁提及用户")

    if use_llm and all_candidates:
        logger.info("[discovery] 使用LLM过滤AI领域相关用户...")
        recommendations = filter_ai_relevant(all_candidates, top_n=30)
        logger.info(f"[discovery] LLM过滤后保留 {len(recommendations)} 个推荐")
    else:
        recommendations = all_candidates[:30]
        for r in recommendations:
            r["ai_relevant"] = True
            r["relevance_note"] = "未使用LLM过滤"

    return {
        "scan_date": datetime.now().isoformat(),
        "days_scanned": days,
        "total_candidates": len(all_candidates),
        "recommendations": recommendations,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    result = discover_new_people(days=30, min_count=2, use_llm=False)
    print(
        f"\n发现 {result['total_candidates']} 个候选人，推荐 {len(result['recommendations'])} 个:"
    )
    for r in result["recommendations"][:20]:
        print(
            f"  @{r['username']} — 被提及{r['count']}次, 平台:{r['platforms']}, 来源:{r['mentioned_by']}"
        )
