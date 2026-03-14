import json
import time
import random
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import RAW_DATA_DIR

logger = logging.getLogger(__name__)


class BaseCollector(ABC):

    def __init__(self, name: str, delay: float = 2.0):
        self.name = name
        self.delay = delay
        self.raw_dir = RAW_DATA_DIR / name
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _sleep(self, jitter: float = 1.0):
        sleep_time = self.delay + random.uniform(0, jitter)
        time.sleep(sleep_time)

    @abstractmethod
    def collect(self, target: str) -> list[dict]:
        ...

    def save_raw(self, data: Any, filename: str | None = None) -> Path:
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{ts}.json"

        filepath = self.raw_dir / filename
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[{self.name}] 原始数据已保存: {filepath}")
        return filepath

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} delay={self.delay}>"
