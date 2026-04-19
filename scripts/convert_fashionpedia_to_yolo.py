from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Fashionpedia-style COCO annotations into a Gyeol menswear YOLO dataset."
    )
    parser.add_argument("--annotations", required=True, help="Path to Fashionpedia COCO JSON annotation file")
    parser.add_argument("--images-dir", required=True, help="Directory containing source images")
    parser.add_argument("--mapping", required=True, help="Path to mapping.yaml")
    parser.add_argument("--output-root", required=True, help="Root directory containing images/<split> and labels/<split>")
    parser.add_argument("--split", required=True, choices=["train", "val", "test"], help="Output split name")
    parser.add_argument(
        "--copy-mode",
        choices=["symlink", "copy", "skip"],
        default="symlink",
        help="How to materialize images into output images/<split> (default: symlink)",
    )
    parser.add_argument(
        "--min-box-area",
        type=float,
        default=16.0,
        help="Minimum bbox pixel area to keep (default: 16.0)",
    )
    parser.add_argument(
        "--include-empty-images",
        action="store_true",
        help="Create empty label files for mapped-empty images instead of skipping them",
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


def coco_bbox_to_yolo(bbox: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = bbox
    x_center = (x + w / 2.0) / width
    y_center = (y + h / 2.0) / height
    return x_center, y_center, w / width, h / height


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def find_image_path(images_dir: Path, file_name: str) -> Path | None:
    direct = images_dir / file_name
    if direct.exists():
        return direct

    stem = Path(file_name).stem
    for ext in IMG_EXTS:
        candidate = images_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate

    matches = list(images_dir.rglob(file_name))
    if matches:
        return matches[0]

    for ext in IMG_EXTS:
        matches = list(images_dir.rglob(f"{stem}{ext}"))
        if matches:
            return matches[0]
    return None


def materialize_image(src: Path, dest: Path, mode: str) -> None:
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if mode == "skip":
        return
    if mode == "copy":
        shutil.copy2(src, dest)
        return
    os.symlink(src, dest)


def main() -> None:
    args = parse_args()
    mapping = load_yaml(Path(args.mapping))
    target_names = mapping["target_names"]
    class_to_id = {normalize_name(k): int(v) for k, v in mapping["class_to_id"].items()}

    annotations_path = Path(args.annotations)
    images_dir = Path(args.images_dir)
    output_root = Path(args.output_root)
    images_out_dir = output_root / "images" / args.split
    labels_out_dir = output_root / "labels" / args.split
    images_out_dir.mkdir(parents=True, exist_ok=True)
    labels_out_dir.mkdir(parents=True, exist_ok=True)

    with annotations_path.open("r", encoding="utf-8") as f:
        coco = json.load(f)

    categories = {
        int(category["id"]): normalize_name(category["name"])
        for category in coco.get("categories", [])
    }
    images = {int(image["id"]): image for image in coco.get("images", [])}
    annos_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for anno in coco.get("annotations", []):
        annos_by_image[int(anno["image_id"])] .append(anno)

    stats = Counter()
    mapped_counts = Counter()
    unmapped_source = Counter()
    dropped_source = Counter()

    for image_id, image in images.items():
        width = int(image["width"])
        height = int(image["height"])
        file_name = image["file_name"]
        label_lines: list[str] = []
        saw_mapped_or_dropped = False

        for anno in annos_by_image.get(image_id, []):
            source_name = categories.get(int(anno["category_id"]), "")
            if not source_name:
                stats["missing_category_id"] += 1
                continue

            target_name = resolve_target(source_name, mapping)
            if target_name is None:
                if normalize_name(source_name) in {normalize_name(x) for x in mapping.get("drop_categories", [])}:
                    dropped_source[source_name] += 1
                    saw_mapped_or_dropped = True
                else:
                    unmapped_source[source_name] += 1
                continue

            saw_mapped_or_dropped = True
            bbox = anno.get("bbox")
            if not bbox or len(bbox) != 4:
                stats["invalid_bbox"] += 1
                continue

            _, _, bw, bh = bbox
            if bw <= 0 or bh <= 0:
                stats["non_positive_bbox"] += 1
                continue
            if bw * bh < args.min_box_area:
                stats["too_small_bbox"] += 1
                continue

            class_id = class_to_id[normalize_name(target_name)]
            x_center, y_center, norm_w, norm_h = coco_bbox_to_yolo(bbox, width, height)
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

        src_image_path = find_image_path(images_dir, file_name)
        if src_image_path is None:
            stats["missing_images"] += 1
            continue

        dest_image_path = images_out_dir / src_image_path.name
        materialize_image(src_image_path, dest_image_path, args.copy_mode)

        label_path = labels_out_dir / f"{src_image_path.stem}.txt"
        label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
        stats["written_images"] += 1
        if label_lines:
            stats["written_label_files"] += 1
        else:
            stats["written_empty_label_files"] += 1

    summary = {
        "mapping_name": mapping.get("name"),
        "split": args.split,
        "target_names": target_names,
        "stats": dict(stats),
        "mapped_box_counts": dict(mapped_counts),
        "dropped_source_counts": dict(dropped_source),
        "unmapped_source_counts": dict(unmapped_source),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
