import os
from pathlib import Path

from common.schemas import Platform

# --- Paths (override via .env or environment variables) ---
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = Path(os.environ.get("STORE_DIR", _PROJECT_ROOT / "store"))
BRONZE_DIR = STORE_DIR / "bronze"
BRANDS_FILE = Path(os.environ.get("BRANDS_FILE", _PROJECT_ROOT / "brands.yaml"))

# --- Database ---
DB_URL = os.environ.get(
    "DB_URL",
    "postgresql://gyeol:gyeol_secret@localhost:5432/gyeol",
)
# ETL용: writer에서 SET search_path TO staging으로 전환
STAGING_DB_URL = os.environ.get("STAGING_DB_URL", DB_URL)

PLATFORMS = {
    # Select shops (편집샵)
    "etcseoul": Platform(name="etcseoul", base_url="https://www.etcseoul.com"),
    "barbershop": Platform(name="barbershop", base_url="https://barbershop.co.kr"),
    "modeman": Platform(name="modeman", base_url="https://mode-man.com"),
    "sculpstore": Platform(name="sculpstore", base_url="https://sculpstore.com"),
    # Brand shops (브랜드 자체몰)
    "bastong": Platform(name="bastong", base_url="https://m.bastong.co.kr", type="brand_shop"),
}
