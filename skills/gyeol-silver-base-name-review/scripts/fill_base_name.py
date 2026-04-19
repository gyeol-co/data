from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.constants import STAGING_DB_URL
from etl.silver.transforms import clean_name


BRACKET_CHUNK_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
SPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[0-9A-Z가-힣]+(?:[./'&+-][0-9A-Z가-힣]+)*")
NUMERIC_TOKEN_RE = re.compile(r"^\d{2,5}[A-Z]?$")


def normalize_name(platform: str, name: str) -> str:
    normalized = clean_name(platform, name)
    normalized = normalized.replace("’", "'").upper().strip()
    normalized = BRACKET_CHUNK_RE.sub(" ", normalized)
    normalized = normalized.replace('"', " ")
    return SPACE_RE.sub(" ", normalized).strip()


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def overlap(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    shared = sum((Counter(left) & Counter(right)).values())
    return shared / max(len(left), len(right))


def common_prefix(left: list[str], right: list[str]) -> list[str]:
    prefix: list[str] = []
    for left_token, right_token in zip(left, right):
        if left_token != right_token:
            break
        prefix.append(left_token)
    return prefix


def usable_prefix(tokens: list[str]) -> bool:
    if len(tokens) >= 2:
        return True
    if len(tokens) == 1 and NUMERIC_TOKEN_RE.match(tokens[0]):
        return True
    return False


def finalize_group(group: list[dict], key_tokens: list[str]) -> tuple[str, list[dict]] | None:
    if len(group) < 2 or not usable_prefix(key_tokens):
        return None
    return " ".join(key_tokens), group


def build_groups(rows: list[dict], threshold: float) -> list[tuple[str, list[dict]]]:
    if len(rows) < 2:
        return []

    rows = sorted(rows, key=lambda row: (row["normalized_name"], row["id"]))
    groups: list[tuple[str, list[dict]]] = []
    current_group: list[dict] = []
    current_key: list[str] = []

    for previous, current in zip(rows, rows[1:]):
        pair_overlap = overlap(previous["tokens"], current["tokens"])
        pair_key = common_prefix(previous["tokens"], current["tokens"])

        if pair_overlap < threshold or not usable_prefix(pair_key):
            finalized = finalize_group(current_group, current_key)
            if finalized:
                groups.append(finalized)
            current_group = []
            current_key = []
            continue

        if not current_group:
            current_group = [previous, current]
            current_key = pair_key
            continue

        if pair_key == current_key:
            current_group.append(current)
            continue

        finalized = finalize_group(current_group, current_key)
        if finalized:
            groups.append(finalized)
        current_group = [previous, current]
        current_key = pair_key

    finalized = finalize_group(current_group, current_key)
    if finalized:
        groups.append(finalized)

    return groups


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--brand-id", required=True)
    parser.add_argument("--threshold", type=float, default=0.60)
    parser.add_argument("--db-dsn", default=STAGING_DB_URL)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with psycopg.connect(args.db_dsn) as conn:
        conn.execute("SET search_path TO staging")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, name, url
                FROM product
                WHERE platform = %s
                  AND platform_brand_id = %s
                ORDER BY id
                """,
                (args.platform, args.brand_id),
            )
            scope_rows = [
                {
                    "id": row_id,
                    "name": name,
                    "url": url,
                    "normalized_name": normalize_name(args.platform, name),
                    "tokens": tokenize(normalize_name(args.platform, name)),
                }
                for row_id, name, url in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT id::text
                FROM product
                WHERE platform = %s
                  AND platform_brand_id = %s
                  AND (base_name IS NULL OR base_name = '')
                ORDER BY id
                """,
                (args.platform, args.brand_id),
            )
            pending_ids = {row_id for (row_id,) in cur.fetchall()}

            updates: dict[str, str] = {}
            for base_name, grouped_rows in build_groups(scope_rows, args.threshold):
                for row in grouped_rows:
                    if row["id"] in pending_ids:
                        updates[row["id"]] = base_name

            if updates:
                cur.executemany(
                    """
                    UPDATE product
                    SET base_name = %s
                    WHERE id = %s::uuid
                      AND (base_name IS NULL OR base_name = '')
                    """,
                    [(base_name, row_id) for row_id, base_name in updates.items()],
                )
                conn.commit()

    for row in scope_rows:
        base_name = updates.get(row["id"])
        if base_name:
            print(f"{row['id']}|{base_name}|{row['name']}|{row['url']}")

    print(
        json.dumps(
            {
                "platform": args.platform,
                "brand_id": args.brand_id,
                "scope_rows": len(scope_rows),
                "pending_rows": len(pending_ids),
                "updated_rows": len(updates),
                "threshold": args.threshold,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
