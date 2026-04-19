"""브랜드 레지스트리.

현재: YAML 파일. Airflow 전환 시 AirflowBrandRegistry 등으로 교체.
"""

from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from common.constants import BRANDS_FILE


class BrandEntry:
    """브랜드 하나의 정보."""

    __slots__ = ("brand_id", "url")

    def __init__(self, brand_id: str, url: str) -> None:
        self.brand_id = brand_id
        self.url = url

    def __repr__(self) -> str:
        return f"BrandEntry({self.brand_id!r}, {self.url!r})"


class BrandRegistry(ABC):
    """플랫폼별 브랜드 목록을 제공하는 인터페이스."""

    @abstractmethod
    def load_all(self) -> dict[str, list[BrandEntry]]:
        """전체 플랫폼-브랜드 매핑을 반환한다.

        Returns:
            {"etcseoul": [BrandEntry("patagonia", "https://..."), ...], ...}
        """

    @abstractmethod
    def load_platform(self, platform: str) -> list[BrandEntry]:
        """특정 플랫폼의 브랜드 리스트를 반환한다."""


class YamlBrandRegistry(BrandRegistry):
    """YAML 파일 기반 브랜드 레지스트리."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or BRANDS_FILE
        self._cache: dict[str, list[BrandEntry]] | None = None

    def _read(self) -> dict[str, list[BrandEntry]]:
        if self._cache is None:
            with open(self._path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._cache = {}
            for platform, brands in data.items():
                if not brands:
                    continue
                self._cache[platform] = [
                    BrandEntry(brand_id, url)
                    for brand_id, url in brands.items()
                ]
        return self._cache

    def load_all(self) -> dict[str, list[BrandEntry]]:
        return self._read()

    def load_platform(self, platform: str) -> list[BrandEntry]:
        return self._read().get(platform, [])
