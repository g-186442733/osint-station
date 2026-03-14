"""
OSINT 工作站全局配置
"""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")
DB_PATH = PROJECT_ROOT / "storage" / "osint.db"
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"

LLM_API_BASE = "https://api.147ai.cn/v1"
LLM_API_KEY = os.getenv("API_147AI_KEY", "")

MODELS = {
    "daily_summary": "gemini-2.0-flash",
    "intelligence_brief": "gpt-4o-mini",
    "deep_analysis": "gpt-5",
    "fallback": "gpt-4.1-nano",
}

WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "")
BILIBILI_COOKIES = os.getenv("BILIBILI_COOKIES", "")
WEIBO_COOKIES = os.getenv("WEIBO_COOKIES", "")

COLLECT_INTERVAL_HOURS = 12
MAX_POSTS_PER_USER = 100

WATCHLIST_PATH = PROJECT_ROOT / "config" / "watchlist.json"
IDENTITY_MAP_PATH = PROJECT_ROOT / "config" / "identity_mappings.json"
