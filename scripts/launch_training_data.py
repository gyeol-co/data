"""학습 데이터 생성 — style_brands_reference.yaml 기반으로 codex 병렬 실행."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]  # gyeol/
_BRANDS_PATH = Path(__file__).resolve().parent.parent / "store" / "training" / "style_brands_reference.yaml"
_LOG_ROOT = Path("/tmp/gyeol-training-data")


def _load_brands() -> dict:
    return yaml.safe_load(_BRANDS_PATH.read_text())


def launch_one(style: str, brand_name: str) -> bool:
    """단일 브랜드 학습 데이터 생성 — codex 백그라운드 실행."""
    if shutil.which("codex") is None:
        logger.error("codex not found in PATH")
        return False

    slug = f"{style}__{brand_name.replace(' ', '_').lower()}"
    log_dir = _LOG_ROOT / slug
    log_dir.mkdir(parents=True, exist_ok=True)
    last_message = log_dir / "last_message.txt"
    lock_file = Path(f"/tmp/gyeol-training-data.{slug}.lock")

    prompt = (
        f"$training-data-generator {style} {brand_name}\n"
        f"Do not ask follow-up questions."
    )

    cmd = [
        "flock", "-n", str(lock_file),
        "codex", "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color", "never",
        "-o", str(last_message),
        "-C", str(_REPO_ROOT),
        prompt,
    ]

    try:
        subprocess.Popen(
            cmd,
            cwd=_REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        logger.exception("Failed to launch: %s %s", style, brand_name)
        return False

    logger.info("Launched: %s / %s", style, brand_name)
    return True


def launch_style(target_style: str) -> int:
    """한 스타일의 모든 브랜드 실행."""
    brands = _load_brands()
    config = brands.get(target_style)
    if not config:
        logger.error("Unknown style: %s", target_style)
        return 0

    count = 0
    for brand in config["brands"]:
        if launch_one(target_style, brand["name"]):
            count += 1
    return count


def launch_all() -> int:
    """전체 브랜드 실행."""
    brands = _load_brands()
    count = 0
    for style, config in brands.items():
        for brand in config["brands"]:
            if launch_one(style, brand["name"]):
                count += 1
    return count


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--all":
            n = launch_all()
            print(f"Launched {n} jobs")
        else:
            # style name
            n = launch_style(arg)
            print(f"Launched {n} jobs for {arg}")
    else:
        print("Usage:")
        print("  python -m scripts.launch_training_data workwear    # 한 스타일")
        print("  python -m scripts.launch_training_data --all       # 전체")
