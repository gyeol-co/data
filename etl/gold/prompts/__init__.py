"""프롬프트 로더 — category별 txt 파일을 읽어 프롬프트 조합."""

from __future__ import annotations

from pathlib import Path

from common.taxonomy import FITS, STYLES, SUB_CATEGORIES

_DIR = Path(__file__).parent
_SYSTEM: str | None = None
_CATEGORY_CACHE: dict[str, str] = {}


def _load(name: str) -> str:
    return (_DIR / name).read_text().strip()


def _system_prompt() -> str:
    global _SYSTEM
    if _SYSTEM is None:
        _SYSTEM = _load("system.txt").format(fits=list(FITS), styles=sorted(STYLES))
    return _SYSTEM


def _category_prompt(category: str) -> str:
    if category not in _CATEGORY_CACHE:
        subs = ", ".join(SUB_CATEGORIES.get(category, []))
        _CATEGORY_CACHE[category] = _load(f"{category}.txt").format(subs=subs)
    return _CATEGORY_CACHE[category]


def build_prompt(
    category: str,
    name: str,
    brand: str,
    description: str | None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt)."""
    focus = _category_prompt(category)

    parts = [
        focus,
        "",
        "Output objective:",
        "- sub_category, fit, style should be English canonical codes.",
        "- sub_category_detail, material, pattern, details, description_ko should be Korean user-facing text.",
        "- Do NOT output color_name — color is handled separately.",
        "- description_ko should be a 1-2 sentence Korean semantic summary that complements the structured fields.",
        "- Do not begin description_ko with the brand name or rewrite every field in prose.",
        "- Do not turn structured fields into a long sentence-by-sentence restatement.",
        "",
        "--- Product ---",
        f"Name: {name}",
    ]
    if brand:
        parts.append(f"Brand: {brand}")
    if description:
        desc = description[:2000] if len(description) > 2000 else description
        parts.append(f"Description:\n{desc}")

    return _system_prompt(), "\n".join(parts)
