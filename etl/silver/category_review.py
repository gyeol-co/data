"""Launch brand-scoped category review runners in the background."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_SCRIPT = _REPO_ROOT / "skills" / "gyeol-silver-category-review" / "scripts" / "run_until_done.sh"


def _enabled() -> bool:
    raw = os.environ.get("GYEOL_CATEGORY_REVIEW_AUTOSTART", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def start_brand_review(platform: str, brand_id: str) -> bool:
    """Launch the brand-scoped category review runner without blocking ETL."""
    if not _enabled():
        logger.debug("Category review autostart disabled: %s/%s", platform, brand_id)
        return False

    if shutil.which("codex") is None:
        logger.warning("Skipping category review autostart; `codex` not found in PATH")
        return False

    if not _RUNNER_SCRIPT.exists():
        logger.warning("Skipping category review autostart; runner script not found: %s", _RUNNER_SCRIPT)
        return False

    log_dir = Path("/tmp/gyeol-silver-category-review") / f"{platform}__{brand_id}"
    env = os.environ.copy()
    env.setdefault("LOG_DIR", str(log_dir))
    if not env.get("DB_URL"):
        from common.constants import STAGING_DB_URL
        env["DB_URL"] = STAGING_DB_URL

    cmd = [
        "bash",
        str(_RUNNER_SCRIPT),
        "--platform",
        platform,
        "--brand-id",
        brand_id,
    ]

    try:
        subprocess.Popen(
            cmd,
            cwd=_REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        logger.exception("Failed to launch category review runner: %s/%s", platform, brand_id)
        return False

    logger.info("Category review runner launched: %s/%s", platform, brand_id)
    return True
