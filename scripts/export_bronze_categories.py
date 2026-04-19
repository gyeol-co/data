import argparse
import csv
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRONZE_DIR = ROOT / "store" / "bronze"

LABELS = ("outer", "top", "bottom", "shoes", "acc")

DIRECT = {
    "outer": "outer",
    "top": "top",
    "bottom": "bottom",
    "shoes": "shoes",
    "shoe": "shoes",
    "acc": "acc",
    "accessory": "acc",
    "accessories": "acc",
    "knit": "top",
    "knits": "top",
    "belt": "acc",
    "belts": "acc",
    "bag": "acc",
    "bags": "acc",
}

TOKENS = {
    "shoes": [
        "shoe",
        "shoes",
        "sneaker",
        "sneakers",
        "loafer",
        "loafers",
        "boot",
        "boots",
        "derby",
        "oxford",
        "moc",
        "mule",
        "sandals",
        "sandal",
        "clog",
        "clogs",
        "trainer",
        "trainers",
        "slipper",
        "slippers",
        "슈즈",
        "신발",
        "스니커즈",
        "로퍼",
        "부츠",
        "샌들",
        "클로그",
        "구두",
    ],
    "acc": [
        "cap",
        "caps",
        "hat",
        "hats",
        "beanie",
        "beret",
        "socks",
        "sock",
        "scarf",
        "muffler",
        "bag",
        "bags",
        "backpack",
        "tote",
        "pouch",
        "wallet",
        "belt",
        "belts",
        "keyring",
        "key holder",
        "necklace",
        "ring",
        "bracelet",
        "glasses",
        "eyewear",
        "watch",
        "case",
        "handkerchief",
        "bandana",
        "tie",
        "ties",
        "mask",
        "brooch",
        "glove",
        "gloves",
        "wallet chain",
        "모자",
        "캡",
        "햇",
        "비니",
        "양말",
        "삭스",
        "스카프",
        "머플러",
        "가방",
        "백팩",
        "토트",
        "파우치",
        "지갑",
        "벨트",
        "키링",
        "목걸이",
        "반지",
        "팔찌",
        "안경",
        "케이스",
        "손수건",
        "반다나",
        "타이",
        "장갑",
    ],
    "bottom": [
        "pants",
        "pant",
        "trouser",
        "trousers",
        "jeans",
        "denim",
        "fatigue",
        "cargo",
        "chino",
        "shorts",
        "short",
        "skirt",
        "slacks",
        "easy pant",
        "track pant",
        "sweatpant",
        "sweatpants",
        "leggings",
        "팬츠",
        "바지",
        "진",
        "데님",
        "쇼츠",
        "반바지",
        "스커트",
        "슬랙스",
        "치노",
        "카고",
        "레깅스",
    ],
    "outer": [
        "jacket",
        "jaket",
        "coat",
        "parka",
        "blouson",
        "bomber",
        "coverall",
        "anorak",
        "windbreaker",
        "flight",
        "chester",
        "duffle",
        "trucker",
        "vest",
        "blazer",
        "liner",
        "1st type",
        "2nd type",
        "pleated front blouse",
        "cardigan jacket",
        "shirt jacket",
        "overshirt",
        "coach jacket",
        "fishtail",
        "jacket shirt",
        "재킷",
        "자켓",
        "코트",
        "파카",
        "점퍼",
        "블루종",
        "블레이저",
        "라이너",
        "봄버",
        "커버올",
        "아노락",
        "윈드브레이커",
        "베스트",
        "조끼",
        "트러커",
        "피쉬테일",
    ],
    "top": [
        "shirt",
        "shirts",
        "tee",
        "tees",
        "t shirt",
        "tshirt",
        "sweat",
        "sweatshirt",
        "hoodie",
        "hood",
        "knit",
        "sweater",
        "cardigan",
        "crewneck",
        "mock neck",
        "turtleneck",
        "v neck",
        "top",
        "blouse",
        "polo",
        "henley",
        "jersey",
        "singlet",
        "pullover",
        "raglan",
        "long sleeve",
        "longsleeve",
        "short sleeve",
        "half sleeve",
        "sleeveless",
        "tank",
        "thermal",
        "dress",
        "셔츠",
        "티셔츠",
        "스웨트",
        "스웻",
        "후디",
        "후드",
        "니트",
        "스웨터",
        "가디건",
        "크루넥",
        "터틀넥",
        "브이넥",
        "탑",
        "블라우스",
        "폴로",
        "헨리",
        "저지",
        "싱글렛",
        "풀오버",
        "래글런",
        "롱슬리브",
        "슬리브리스",
        "탱크",
        "드레스",
        "써멀",
    ],
}


def norm(text: str | None) -> str:
    text = (text or "").lower()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^0-9a-z가-힣 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()



def latest_dirs() -> list[Path]:
    dirs = []
    for platform_dir in sorted(BRONZE_DIR.iterdir()):
        if not platform_dir.is_dir():
            continue
        dates = sorted((d for d in platform_dir.iterdir() if d.is_dir()), reverse=True)
        if dates:
            dirs.append(dates[0])
    return dirs



def read_rows() -> list[dict]:
    rows = []
    for date_dir in latest_dirs():
        for path in sorted(date_dir.glob("*.jsonl")):
            with open(path, encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
    return rows



def detect(text: str, label: str) -> str | None:
    padded = f" {text} "
    for token in TOKENS[label]:
        part = f" {token} "
        if part in padded:
            return token
    return None



def classify(row: dict) -> tuple[str, str]:
    raw = norm(row.get("category"))
    if raw in DIRECT:
        return DIRECT[raw], f"source:{raw}"

    text = " ".join(
        norm(value)
        for value in [
            row.get("category"),
            row.get("name"),
            row.get("description_txt"),
            row.get("url"),
        ]
        if value
    )

    for label in ("shoes", "acc", "bottom", "outer", "top"):
        hit = detect(text, label)
        if hit:
            return label, f"keyword:{hit}"

    return "acc", "fallback:acc"



def export(rows: list[dict], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "product_categories.jsonl"
    csv_path = out_dir / "product_categories.csv"
    summary_path = out_dir / "summary.json"

    fields = [
        "platform",
        "brand",
        "platform_product_id",
        "name",
        "source_category",
        "normalized_category",
        "reason",
        "original_price",
        "discounted_price",
        "sold_out",
        "url",
        "thumbnail_url",
        "crawled_at",
    ]

    counts = Counter()
    source = Counter()
    reasons = Counter()

    with open(jsonl, "w", encoding="utf-8") as jf, open(csv_path, "w", encoding="utf-8", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            label, reason = classify(row)
            item = {
                "platform": row.get("metadata", {}).get("platform"),
                "brand": row.get("brand"),
                "platform_product_id": row.get("platform_product_id"),
                "name": row.get("name"),
                "source_category": row.get("category"),
                "normalized_category": label,
                "reason": reason,
                "original_price": row.get("original_price"),
                "discounted_price": row.get("discounted_price"),
                "sold_out": row.get("sold_out"),
                "url": row.get("url"),
                "thumbnail_url": row.get("thumbnail_url"),
                "crawled_at": row.get("metadata", {}).get("crawled_at"),
            }
            jf.write(json.dumps(item, ensure_ascii=False) + "\n")
            writer.writerow(item)
            counts[label] += 1
            source[str(row.get("metadata", {}).get("platform"))] += 1
            reasons[reason] += 1

    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "records": len(rows),
        "labels": list(LABELS),
        "counts": dict(counts),
        "platform_counts": dict(source),
        "reason_counts": dict(reasons.most_common()),
        "jsonl": str(jsonl),
        "csv": str(csv_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary



def main() -> None:
    parser = argparse.ArgumentParser(description="Export normalized 5-way category labels from bronze crawler data.")
    parser.add_argument(
        "--output-dir",
        default=str(BRONZE_DIR.parent / "derived" / "category_labels" / "latest"),
        help="Directory where product_categories.jsonl/csv and summary.json are written.",
    )
    args = parser.parse_args()

    rows = read_rows()
    summary = export(rows, Path(args.output_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
