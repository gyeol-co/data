from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


IMAGE_FORMAT = "JPEG"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Hugging Face Fashionpedia to a reduced Gyeol YOLO dataset."
    )
    parser.add_argument(
        "--dataset-name",
        default="detection-datasets/fashionpedia",
        help="Hugging Face dataset id (default: detection-datasets/fashionpedia)",
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Path to mapping.yaml",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Directory where images/<split> and labels/<split> will be written",
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["train", "val"],
        choices=["train", "val", "test"],
        help="Dataset splits to export",
    )
    parser.add_argument(
        "--min-box-area",
        type=float,
        default=16.0,
        help="Minimum bbox pixel area to keep",
    )
    parser.add_argument(
        "--include-empty-images",
        action="store_true",
        help="Write empty label files when an image only contains dropped classes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max images per split for smoke tests (0 = all)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete existing images/<split> and labels/<split> before exporting",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").split())


def resolve_target(name: str, mapping: dict[str, Any]) -> str | None:
    aliases = {normalize_name(k): normalize_name(v) for k, v in mapping.get("aliases", {}).items()}
    category_mapping = {
        normalize_name(k): normalize_name(v) for k, v in mapping.get("category_mapping", {}).items()
    }
    drop_categories = {normalize_name(x) for x in mapping.get("drop_categories", [])}

    canonical = aliases.get(normalize_name(name), normalize_name(name))
    if canonical in drop_categories:
        return None
    return category_mapping.get(canonical)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def pascal_bbox_to_yolo(bbox: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    bw = x2 - x1
    bh = y2 - y1
    x_center = (x1 + bw / 2.0) / width
    y_center = (y1 + bh / 2.0) / height
    return x_center, y_center, bw / width, bh / height


def main() -> None:
    args = parse_args()
    mapping = load_yaml(Path(args.mapping))
    output_root = Path(args.output_root)
    class_to_id = {normalize_name(k): int(v) for k, v in mapping["class_to_id"].items()}

    try:
        from datasets import load_dataset
    except ImportError as e:
        raise SystemExit(
            "datasets package is required. Install with: pip install datasets pillow pyarrow"
        ) from e

    dataset = load_dataset(args.dataset_name)
    category_names = dataset["train"].features["objects"]["category"].feature.names

    overall = {
        "dataset_name": args.dataset_name,
        "mapping_name": mapping.get("name"),
        "splits": {},
    }

    for split in args.splits:
        if split not in dataset:
            print(json.dumps({"warning": f"split {split} not present in dataset"}, ensure_ascii=False))
            continue

        split_ds = dataset[split]
        if args.limit > 0:
            split_ds = split_ds.select(range(min(args.limit, len(split_ds))))

        images_out_dir = output_root / "images" / split
        labels_out_dir = output_root / "labels" / split
        if args.clean:
            shutil.rmtree(images_out_dir, ignore_errors=True)
            shutil.rmtree(labels_out_dir, ignore_errors=True)
        images_out_dir.mkdir(parents=True, exist_ok=True)
        labels_out_dir.mkdir(parents=True, exist_ok=True)

        stats = Counter()
        mapped_counts = Counter()
        dropped_source = Counter()
        unmapped_source = Counter()

        for row in split_ds:
            image_id = int(row["image_id"])
            image = row["image"]
            width = int(row["width"])
            height = int(row["height"])
            objects = row["objects"]

            label_lines: list[str] = []
            saw_mapped_or_dropped = False

            categories = objects.get("category", [])
            bboxes = objects.get("bbox", [])
            areas = objects.get("area", [])

            for idx, category_id in enumerate(categories):
                source_name = normalize_name(category_names[int(category_id)])
                target_name = resolve_target(source_name, mapping)
                if target_name is None:
                    if source_name in {normalize_name(x) for x in mapping.get("drop_categories", [])}:
                        dropped_source[source_name] += 1
                        saw_mapped_or_dropped = True
                    else:
                        unmapped_source[source_name] += 1
                    continue

                saw_mapped_or_dropped = True
                bbox = bboxes[idx]
                area = float(areas[idx]) if idx < len(areas) else None
                if not bbox or len(bbox) != 4:
                    stats["invalid_bbox"] += 1
                    continue

                x1, y1, x2, y2 = [float(v) for v in bbox]
                bw = x2 - x1
                bh = y2 - y1
                if bw <= 0 or bh <= 0:
                    stats["non_positive_bbox"] += 1
                    continue
                if area is not None and area < args.min_box_area:
                    stats["too_small_bbox"] += 1
                    continue

                class_id = class_to_id[normalize_name(target_name)]
                x_center, y_center, norm_w, norm_h = pascal_bbox_to_yolo([x1, y1, x2, y2], width, height)
                x_center = clamp01(x_center)
                y_center = clamp01(y_center)
                norm_w = clamp01(norm_w)
                norm_h = clamp01(norm_h)
                if norm_w <= 0 or norm_h <= 0:
                    stats["degenerate_normalized_bbox"] += 1
                    continue

                label_lines.append(
                    f"{class_id} {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}"
                )
                mapped_counts[target_name] += 1
                stats["kept_boxes"] += 1

            if not label_lines and not (args.include_empty_images and saw_mapped_or_dropped):
                stats["skipped_images_without_labels"] += 1
                continue

            image_path = images_out_dir / f"{image_id}.jpg"
            label_path = labels_out_dir / f"{image_id}.txt"
            image.save(image_path, format=IMAGE_FORMAT, quality=95)
            label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            stats["written_images"] += 1
            if label_lines:
                stats["written_label_files"] += 1
            else:
                stats["written_empty_label_files"] += 1

        overall["splits"][split] = {
            "stats": dict(stats),
            "mapped_box_counts": dict(mapped_counts),
            "dropped_source_counts": dict(dropped_source),
            "unmapped_source_counts": dict(unmapped_source),
        }

    print(json.dumps(overall, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
