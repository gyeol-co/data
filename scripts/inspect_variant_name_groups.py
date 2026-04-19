from __future__ import annotations

import argparse
import re
from collections import defaultdict

import psycopg
from common.constants import STAGING_DB_URL


COLOR_WORDS = {
    "black", "white", "offwhite", "off-white", "ivory", "cream", "ecru", "natural",
    "beige", "sand", "tan", "khaki", "camel", "brown", "charcoal", "gray", "grey",
    "silver", "navy", "blue", "sax", "indigo", "denim", "red", "burgundy", "wine",
    "pink", "orange", "yellow", "mustard", "green", "olive", "lime", "purple",
    "violet", "mint", "teal", "turquoise", "gold", "bronze", "copper", "ginger",
    "drab", "milk", "oatmeal", "walnut", "mocha", "coffee", "chocolate", "stone",
    "ash", "sky", "lt", "dk", "light", "dark", "washed", "vintage",
    "블랙", "화이트", "아이보리", "크림", "에크루", "내추럴", "베이지", "샌드", "탄",
    "카키", "카멜", "브라운", "차콜", "그레이", "실버", "네이비", "블루", "인디고",
    "레드", "버건디", "와인", "핑크", "오렌지", "옐로우", "머스타드", "그린", "올리브",
    "퍼플", "민트", "골드", "브론즈",
}

SEPARATOR_RE = re.compile(r"\s+")
TRAILING_PUNCT_RE = re.compile(r"^[\s\-/_,.()]+|[\s\-/_,.()]+$")
COLOR_SPLIT_RE = re.compile(r"[\/,&+]|(?:\s+and\s+)")
MAJOR_SPLIT_RE = re.compile(r"\s+-\s+")


def clean_token(token: str) -> str:
    token = TRAILING_PUNCT_RE.sub("", token.strip().lower())
    token = token.replace("grey", "gray")
    return token


def is_color_phrase(text: str) -> bool:
    text = text.strip().lower()
    if not text:
        return False

    normalized = (
        text.replace(" / ", "/")
        .replace(" & ", "&")
        .replace(" + ", "+")
        .replace("-", " ")
    )
    parts = [clean_token(p) for p in COLOR_SPLIT_RE.split(normalized)]
    parts = [p for p in parts if p]
    if not parts:
        return False

    tokens: list[str] = []
    for part in parts:
        tokens.extend(clean_token(tok) for tok in part.split())
    tokens = [tok for tok in tokens if tok]
    if not tokens:
        return False

    return all(tok in COLOR_WORDS for tok in tokens)


def normalize_name(name: str) -> str:
    original = SEPARATOR_RE.sub(" ", name).strip()
    lowered = original.lower()

    segments = [seg.strip() for seg in MAJOR_SPLIT_RE.split(lowered) if seg.strip()]
    if len(segments) >= 2 and is_color_phrase(" - ".join(segments[1:])):
        lowered = segments[0]

    tokens = [clean_token(tok) for tok in lowered.split()]
    tokens = [tok for tok in tokens if tok]

    while len(tokens) > 1 and tokens[-1] in COLOR_WORDS:
        tokens.pop()

    return " ".join(tokens).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    sql = """
        SELECT platform, brand, name, platform_product_id, url
        FROM product
        WHERE category IS NOT NULL
    """

    groups: dict[tuple[str, str, str], list[tuple[str, str, str]]] = defaultdict(list)
    with psycopg.connect(STAGING_DB_URL) as conn:
        conn.execute("SET search_path TO staging")
        with conn.cursor() as cur:
            cur.execute(sql)
            for platform, brand, name, platform_product_id, url in cur.fetchall():
                normalized = normalize_name(name)
                if not normalized or normalized == name.lower().strip():
                    continue
                groups[(platform, brand, normalized)].append((name, platform_product_id, url))

    candidate_groups = []
    for (platform, brand, normalized), items in groups.items():
        distinct_names = sorted({name for name, _, _ in items})
        if len(distinct_names) < 2:
            continue
        candidate_groups.append(
            {
                "platform": platform,
                "brand": brand,
                "normalized_name": normalized,
                "count": len(items),
                "distinct_names": distinct_names,
            }
        )

    candidate_groups.sort(key=lambda row: (-row["count"], row["platform"], row["brand"], row["normalized_name"]))

    covered_products = sum(row["count"] for row in candidate_groups)
    print(f"candidate_groups={len(candidate_groups)} covered_products={covered_products}")
    for row in candidate_groups[: args.limit]:
        names = " || ".join(row["distinct_names"][:6])
        print(
            f"{row['count']:>3} | {row['platform']} | {row['brand']} | "
            f"{row['normalized_name']} | {names}"
        )


if __name__ == "__main__":
    main()
