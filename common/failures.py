"""실패 로그 저장/읽기.

실패한 상품을 JSONL로 기록하고, 나중에 읽어서 재시도할 수 있게 한다.
저장 경로: store/failed/{date}.jsonl
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from common.constants import STORE_DIR

logger = logging.getLogger(__name__)

FAILED_DIR = STORE_DIR / "failed"


class FailureEntry:
    __slots__ = ("platform", "brand_id", "product_url", "error", "timestamp")

    def __init__(
        self,
        platform: str,
        brand_id: str,
        product_url: str,
        error: str,
        timestamp: str | None = None,
    ) -> None:
        self.platform = platform
        self.brand_id = brand_id
        self.product_url = product_url
        self.error = error
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "brand_id": self.brand_id,
            "product_url": self.product_url,
            "error": self.error,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FailureEntry":
        return cls(
            platform=data["platform"],
            brand_id=data["brand_id"],
            product_url=data["product_url"],
            error=data.get("error", ""),
            timestamp=data.get("timestamp"),
        )


class FailureLogger:
    """실패 로그를 JSONL로 저장하고 읽는다."""

    def __init__(self, date: str | None = None) -> None:
        self._date = date or datetime.now().strftime("%Y-%m-%d")
        self._path = FAILED_DIR / f"{self._date}.jsonl"
        self._entries: list[FailureEntry] = []

    def add(self, entry: FailureEntry) -> None:
        self._entries.append(entry)

    def add_product(self, platform: str, brand_id: str, product_url: str, error: str) -> None:
        self.add(FailureEntry(platform, brand_id, product_url, error))

    def add_brand(self, platform: str, brand_id: str, error: str) -> None:
        """브랜드 레벨 실패 (product_url 없음)."""
        self.add(FailureEntry(platform, brand_id, product_url="", error=error))

    def save(self) -> Path | None:
        if not self._entries:
            return None

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        logger.info("Failures saved: %d entries → %s", len(self._entries), self._path)
        return self._path

    @property
    def count(self) -> int:
        return len(self._entries)

    @staticmethod
    def load(path: Path) -> list[FailureEntry]:
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(FailureEntry.from_dict(json.loads(line)))
        return entries

    @staticmethod
    def latest() -> Path | None:
        """가장 최근 실패 로그 파일을 반환한다."""
        if not FAILED_DIR.exists():
            return None
        files = sorted(FAILED_DIR.glob("*.jsonl"), reverse=True)
        return files[0] if files else None
