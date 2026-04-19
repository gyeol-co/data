"""Launch brand profile generation jobs in the background."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOG_ROOT = Path("/tmp/gyeol-brand-profile")


def _db_url() -> str:
    """codex 서브프로세스에 내려 줄 Postgres URL."""
    from common.constants import STAGING_DB_URL
    return os.environ.get("DB_URL", STAGING_DB_URL)


def _enabled() -> bool:
    raw = os.environ.get("GYEOL_BRAND_PROFILE_AUTOSTART", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _build_prompt(platform: str, brand_id: str, brand_name: str) -> str:
    return (
        f"$gyeol-brand-profile {platform} {brand_name}\n"
        f"Target row scope: platform={platform}, platform_brand_id={brand_id}, exact_name={brand_name}.\n"
        "Use that exact staging.brand row, do not ask follow-up questions, and update description, style, and name_ko."
    )


def _scope_slug(platform: str, brand_id: str) -> str:
    return f"{platform}__{brand_id}"


def start_brand_profile(platform: str, brand_id: str, brand_name: str) -> bool:
    """Launch the brand profile skill without blocking ETL."""
    if not _enabled():
        logger.debug("Brand profile autostart disabled: %s/%s", platform, brand_id)
        return False

    if shutil.which("codex") is None:
        logger.warning("Skipping brand profile autostart; `codex` not found in PATH")
        return False
    if shutil.which("flock") is None:
        logger.warning("Skipping brand profile autostart; `flock` not found in PATH")
        return False

    scope = _scope_slug(platform, brand_id)
    log_dir = _LOG_ROOT / scope
    log_dir.mkdir(parents=True, exist_ok=True)

    lock_file = Path(f"/tmp/gyeol-brand-profile.{scope}.lock")
    last_message_file = log_dir / "last_message.txt"

    prompt = _build_prompt(platform, brand_id, brand_name)
    cmd = [
        "flock",
        "-n",
        str(lock_file),
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "-o",
        str(last_message_file),
        "-C",
        str(_REPO_ROOT),
        prompt,
    ]

    env = os.environ.copy()
    env.setdefault("DB_URL", _db_url())

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
        logger.exception("Failed to launch brand profile job: %s/%s", platform, brand_id)
        return False

    logger.info(
        "Brand profile job launched: %s/%s (%s) lock=%s",
        platform,
        brand_id,
        brand_name,
        lock_file,
    )
    return True
