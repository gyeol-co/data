"""Launch base_name fill jobs in the background."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_SCRIPT = _REPO_ROOT / "skills" / "gyeol-silver-base-name-review" / "scripts" / "run_until_done.sh"


_NEEDS_LLM_PLATFORMS = {"sculpstore", "modeman"}


def _enabled() -> bool:
    raw = os.environ.get("GYEOL_BASE_NAME_REVIEW_AUTOSTART", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def start_base_name_review(platform: str, brand_id: str) -> bool:
    """Launch the base_name runner without blocking ETL.

    패턴 기반 추출이 안 되는 플랫폼(sculpstore, modeman)만 대상.
    """
    if platform not in _NEEDS_LLM_PLATFORMS:
        return False

    if not _enabled():
        logger.debug("Base name review autostart disabled: %s/%s", platform, brand_id)
        return False

    if shutil.which("codex") is None:
        logger.warning("Skipping base name review autostart; `codex` not found in PATH")
        return False

    if not _RUNNER_SCRIPT.exists():
        logger.warning("Skipping base name review autostart; runner script not found: %s", _RUNNER_SCRIPT)
        return False

    log_dir = Path("/tmp/gyeol-silver-base-name-review") / f"{platform}__{brand_id}"
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
        logger.exception("Failed to launch base name review runner: %s/%s", platform, brand_id)
        return False

    logger.info("Base name review runner launched: %s/%s", platform, brand_id)
    return True
