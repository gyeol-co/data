from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.constants import STORE_DIR
from etl.silver.transforms import clean_name


BRACKET_CHUNK_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
SPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[0-9A-Z가-힣]+(?:[./'&+-][0-9A-Z가-힣]+)*")


def normalize_for_grouping(platform: str, name: str) -> str:
    normalized = clean_name(platform, name)
    normalized = normalized.replace("’", "'").upper().strip()
    normalized = BRACKET_CHUNK_RE.sub(" ", normalized)
    normalized = normalized.replace('"', " ")
    return SPACE_RE.sub(" ", normalized).strip()


def tokenize(name: str) -> list[str]:
    return TOKEN_RE.findall(name)


def token_sort_overlap(left: list[str], right: list[str]) -> float:
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


def load_products(path: Path) -> list[dict]:
    platform = path.parts[-3]
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for row_index, line in enumerate(handle):
            if not line.strip():
                continue
            raw = json.loads(line)
            normalized_name = normalize_for_grouping(platform, raw.get("name", ""))
            rows.append(
                {
                    "row_index": row_index,
                    "platform": platform,
                    "brand": raw.get("brand", "").strip(),
                    "source_path": str(path),
                    "platform_product_id": str(raw.get("platform_product_id", "")),
                    "name": raw.get("name", ""),
                    "normalized_name": normalized_name,
                    "tokens": tokenize(normalized_name),
                    "url": raw.get("url", ""),
                    "thumbnail_url": raw.get("thumbnail_url"),
                }
            )
    return rows


def finalize_group(group: list[dict], key_tokens: list[str], threshold: float) -> dict | None:
    if len(group) < 2:
        return None

    prefix = " ".join(key_tokens).strip() or group[0]["normalized_name"]
    overlaps: list[float] = []
    for left, right in zip(group, group[1:]):
        overlaps.append(token_sort_overlap(left["tokens"], right["tokens"]))

    return {
        "platform": group[0]["platform"],
        "brand": group[0]["brand"],
        "source_path": group[0]["source_path"],
        "prefix": prefix,
        "count": len(group),
        "threshold": threshold,
        "mean_adjacent_overlap": round(mean(overlaps), 3) if overlaps else 1.0,
        "row_indices": [item["row_index"] for item in group],
        "variants": [
            {
                "row_index": item["row_index"],
                "platform_product_id": item["platform_product_id"],
                "name": item["name"],
                "normalized_name": item["normalized_name"],
                "url": item["url"],
                "thumbnail_url": item["thumbnail_url"],
            }
            for item in group
        ],
    }


def prune_short_bridge_groups(groups: list[dict]) -> list[dict]:
    pruned: list[dict] = []
    for index, group in enumerate(groups):
        if group["count"] != 2:
            pruned.append(group)
            continue

        key_len = len(group["prefix"].split())
        previous = groups[index - 1] if index > 0 else None
        following = groups[index + 1] if index + 1 < len(groups) else None

        drop = False
        if previous and previous["source_path"] == group["source_path"]:
            if previous["row_indices"][-1] == group["row_indices"][0]:
                if key_len < len(previous["prefix"].split()):
                    drop = True

        if following and following["source_path"] == group["source_path"]:
            if group["row_indices"][-1] == following["row_indices"][0]:
                if key_len < len(following["prefix"].split()):
                    drop = True

        if not drop:
            pruned.append(group)

    return pruned


def build_groups(rows: list[dict], threshold: float) -> list[dict]:
    if len(rows) < 2:
        return []

    groups: list[dict] = []
    current_group: list[dict] = []
    current_key: list[str] = []

    for previous, current in zip(rows, rows[1:]):
        if not previous["tokens"] or not current["tokens"]:
            maybe_group = finalize_group(current_group, current_key, threshold)
            if maybe_group:
                groups.append(maybe_group)
            current_group = []
            current_key = []
            continue

        overlap = token_sort_overlap(previous["tokens"], current["tokens"])
        pair_key = common_prefix(previous["tokens"], current["tokens"])

        if overlap < threshold or not pair_key:
            maybe_group = finalize_group(current_group, current_key, threshold)
            if maybe_group:
                groups.append(maybe_group)
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

        maybe_group = finalize_group(current_group, current_key, threshold)
        if maybe_group:
            groups.append(maybe_group)
        current_group = [previous, current]
        current_key = pair_key

    maybe_group = finalize_group(current_group, current_key, threshold)
    if maybe_group:
        groups.append(maybe_group)

    return prune_short_bridge_groups(groups)


def build_summary(groups: list[dict]) -> dict:
    by_platform: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "products": 0})
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "products": 0})

    for group in groups:
        by_platform[group["platform"]]["groups"] += 1
        by_platform[group["platform"]]["products"] += group["count"]
        by_source[group["source_path"]]["groups"] += 1
        by_source[group["source_path"]]["products"] += group["count"]

    return {
        "group_count": len(groups),
        "grouped_product_count": sum(group["count"] for group in groups),
        "by_platform": dict(sorted(by_platform.items())),
        "by_source_path": dict(sorted(by_source.items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-glob",
        default="store/bronze/*/*/*.jsonl",
        help="Bronze JSONL glob pattern.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.60,
        help="Minimum token-sort overlap needed to keep the current group.",
    )
    parser.add_argument(
        "--output",
        default=str(STORE_DIR / "training" / "variant_groups_sequential.jsonl"),
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--summary-output",
        default=str(STORE_DIR / "training" / "variant_groups_sequential_summary.json"),
        help="Output summary JSON path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_paths = [Path(path) for path in sorted(glob.glob(args.input_glob))]
    all_groups: list[dict] = []

    for path in input_paths:
        rows = load_products(path)
        all_groups.extend(build_groups(rows, args.threshold))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for group in all_groups:
            handle.write(json.dumps(group, ensure_ascii=False) + "\n")

    summary = build_summary(all_groups)
    summary["threshold"] = args.threshold
    summary["input_glob"] = args.input_glob

    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "group_count": summary["group_count"],
                "grouped_product_count": summary["grouped_product_count"],
                "output": str(output_path),
                "summary_output": str(summary_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
