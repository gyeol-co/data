"""Taxonomy 로더 — common/taxonomy.yaml → StrEnum + validation."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml

_TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"
_RAW: dict = yaml.safe_load(_TAXONOMY_PATH.read_text())

# ── StrEnum (single source: taxonomy.yaml) ───────────────────────────

Category = StrEnum("Category", {k.upper(): k for k in _RAW["category"]})
SubCategory = StrEnum(
    "SubCategory",
    {s.upper(): s for subs in _RAW["category"].values() for s in subs},
)
Fit = StrEnum("Fit", {v.upper(): v for v in _RAW["fit"]})
Style = StrEnum("Style", {v.upper(): v for v in _RAW["style"]})

# ── Lookup tables ────────────────────────────────────────────────────

SUB_CATEGORIES: dict[str, list[str]] = _RAW["category"]
CATEGORIES: set[str] = set(SUB_CATEGORIES)
ALL_SUB_CATEGORIES: set[str] = {s for subs in SUB_CATEGORIES.values() for s in subs}
FITS: set[str] = set(_RAW["fit"])
STYLES: set[str] = set(_RAW["style"])

# ── Category fit rules ───────────────────────────────────────────────

FIT_REQUIRED: frozenset[str] = frozenset(CATEGORIES - {"acc", "shoes"})
FIT_FORBIDDEN: frozenset[str] = frozenset({"acc", "shoes"})


def get_category(sub: str) -> str | None:
    """sub_category → parent category. 없으면 None."""
    for cat, subs in SUB_CATEGORIES.items():
        if sub in subs:
            return cat
    return None


def is_valid_sub_category(category: str, sub: str) -> bool:
    return sub in SUB_CATEGORIES.get(category, [])


def is_valid_fit(fit: str) -> bool:
    return fit in FITS
