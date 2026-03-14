import json
import logging
from datetime import datetime

import requests

from collectors.base import BaseCollector
from config.settings import MAX_POSTS_PER_USER

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "OSINT-Station/1.0",
}


class GitHubCollector(BaseCollector):
    def __init__(self, delay: float = 1.0):
        super().__init__(name="github", delay=delay)

    def collect(self, target: str) -> list[dict]:
        username = str(target).strip()
        logger.info(f"[github] 开始采集用户 {username}")

        events = self._collect_events(username)
        repos = self._collect_repos(username)

        all_posts = events + repos
        seen_ids = set()
        deduped = []
        for p in all_posts:
            if p["platform_id"] not in seen_ids:
                seen_ids.add(p["platform_id"])
                deduped.append(p)

        deduped.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        deduped = deduped[:MAX_POSTS_PER_USER]

        if deduped:
            self.save_raw(
                deduped,
                f"user_{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )

        logger.info(f"[github] {username} 获取到 {len(deduped)} 条动态")
        return deduped

    def _collect_events(self, username: str) -> list[dict]:
        url = f"https://api.github.com/users/{username}/events/public"
        try:
            resp = requests.get(
                url, headers=HEADERS, params={"per_page": 30}, timeout=15
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception as e:
            logger.error(f"[github] {username} events 获取失败: {e}")
            return []

        posts = []
        for ev in events:
            normalized = self._normalize_event(ev, username)
            if normalized:
                posts.append(normalized)
        return posts

    def _collect_repos(self, username: str) -> list[dict]:
        url = f"https://api.github.com/users/{username}/repos"
        try:
            resp = requests.get(
                url,
                headers=HEADERS,
                params={"sort": "updated", "per_page": 10},
                timeout=15,
            )
            resp.raise_for_status()
            repos = resp.json()
        except Exception as e:
            logger.error(f"[github] {username} repos 获取失败: {e}")
            return []

        return [self._normalize_repo(r, username) for r in repos if not r.get("fork")]

    @staticmethod
    def _normalize_event(ev: dict, username: str) -> dict | None:
        ev_type = ev.get("type", "")
        repo_name = ev.get("repo", {}).get("name", "")
        created_at = ev.get("created_at", "")
        if created_at:
            created_at = created_at.replace("Z", "").replace("T", "T")

        payload = ev.get("payload", {})
        ev_id = ev.get("id", "")

        if ev_type == "PushEvent":
            commits = payload.get("commits", [])
            if not commits:
                return None
            messages = [c.get("message", "").split("\n")[0] for c in commits[:3]]
            title = f"Push to {repo_name}"
            content = "; ".join(messages)
        elif ev_type == "CreateEvent":
            ref_type = payload.get("ref_type", "")
            ref = payload.get("ref", "")
            title = f"Created {ref_type} {ref} in {repo_name}"
            content = payload.get("description", "") or ""
        elif ev_type == "WatchEvent":
            title = f"Starred {repo_name}"
            content = ""
        elif ev_type == "ForkEvent":
            title = f"Forked {repo_name}"
            content = ""
        elif ev_type == "IssuesEvent":
            action = payload.get("action", "")
            issue = payload.get("issue", {})
            title = f"Issue {action}: {issue.get('title', '')}"
            content = (issue.get("body", "") or "")[:300]
        elif ev_type == "PullRequestEvent":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            title = f"PR {action}: {pr.get('title', '')}"
            content = (pr.get("body", "") or "")[:300]
        elif ev_type == "ReleaseEvent":
            release = payload.get("release", {})
            title = f"Released {release.get('tag_name', '')} in {repo_name}"
            content = (release.get("body", "") or "")[:300]
        else:
            return None

        return {
            "platform": "github",
            "platform_id": f"gh_ev_{ev_id}",
            "title": title,
            "content": content,
            "url": f"https://github.com/{repo_name}",
            "published_at": created_at,
            "engagement_likes": 0,
            "engagement_comments": 0,
            "engagement_shares": 0,
            "engagement_views": 0,
            "raw_json": json.dumps(
                {
                    "event_type": ev_type,
                    "repo": repo_name,
                    "username": username,
                },
                ensure_ascii=False,
            ),
        }

    @staticmethod
    def _normalize_repo(repo: dict, username: str) -> dict:
        updated_at = repo.get("updated_at", "")
        if updated_at:
            updated_at = updated_at.replace("Z", "").replace("T", "T")

        name = repo.get("full_name", "")
        description = repo.get("description", "") or ""
        stars = repo.get("stargazers_count", 0)
        forks = repo.get("forks_count", 0)
        language = repo.get("language", "") or ""

        title = f"[{language}] {name}" if language else name
        content = description

        return {
            "platform": "github",
            "platform_id": f"gh_repo_{repo.get('id', '')}",
            "title": title,
            "content": content,
            "url": repo.get("html_url", ""),
            "published_at": updated_at,
            "engagement_likes": stars,
            "engagement_comments": repo.get("open_issues_count", 0),
            "engagement_shares": forks,
            "engagement_views": 0,
            "raw_json": json.dumps(
                {
                    "full_name": name,
                    "description": description,
                    "stars": stars,
                    "forks": forks,
                    "language": language,
                    "username": username,
                    "topics": repo.get("topics", []),
                },
                ensure_ascii=False,
            ),
        }
