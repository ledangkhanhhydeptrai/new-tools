# ============================================================
# config.py
# GOOGLE MAPS TOOL CONFIGURATION
# ============================================================

from pathlib import Path

# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
INPUT_FILE = INPUT_DIR / "hotels_export_google_maps_result.xlsx"
OUTPUT_FILE = OUTPUT_DIR / "hotels_export_google_maps_checked.xlsx"
CACHE_DIR = BASE_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"

# Tự tạo folder nếu chưa tồn tại
for directory in (
    INPUT_DIR,
    OUTPUT_DIR,
    CACHE_DIR,
    LOG_DIR,
):
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

# ============================================================
# BROWSER
# ============================================================

HEADLESS = True

BROWSER_CHANNEL = None
# Ví dụ nếu muốn dùng Chrome thật:
# BROWSER_CHANNEL = "chrome"

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 900

LOCALE = "vi-VN"
TIMEZONE_ID = "Asia/Ho_Chi_Minh"

# ============================================================
# NAVIGATION
# ============================================================

PAGE_TIMEOUT = 15_000

DEFAULT_PAGE_TIMEOUT = 10_000

MAX_RETRIES = 3

# ============================================================
# GOOGLE MAPS SEARCH
# ============================================================

SEARCH_POLL_INTERVAL = 0.10

SEARCH_POLL_COUNT = 5

FINAL_SEARCH_CHECK_DELAY = 0.10

# ============================================================
# ADAPTIVE DELAY
# ============================================================

# Search thành công
SEARCH_DELAY_MIN = 0.5
SEARCH_DELAY_MAX = 1.0

# Search thất bại
FAILED_SEARCH_DELAY_MIN = 1.0
FAILED_SEARCH_DELAY_MAX = 1.8

# Retry navigation
RETRY_DELAY_BASE = 1.0

RETRY_DELAY_MAX = 5.0

# ============================================================
# PAGE RECOVERY
# ============================================================

# Sau bao nhiêu SEARCH thật thì tạo Page mới
RESET_PAGE_EVERY_SEARCHES = 120

PAGE_RECREATE_DELAY_MIN = 0.4
PAGE_RECREATE_DELAY_MAX = 0.8

# ============================================================
# CHECKPOINT
# ============================================================

CHECKPOINT_EVERY_ROWS = 100

# ============================================================
# MATCHING
# ============================================================

# Fuzzy score tối thiểu để chấp nhận
FUZZY_MIN_SCORE = 88

# Name score rất cao
STRONG_NAME_SCORE = 92

# Address score rất cao
STRONG_ADDRESS_SCORE = 88

# ============================================================
# FILE OUTPUT
# ============================================================

RESULT_SUFFIX = "_google_maps_result.xlsx"

MISSING_SUFFIX = "_google_maps_missing.xlsx"

REPORT_SUFFIX = "_google_maps_report.json"

# ============================================================
# CACHE
# ============================================================

CACHE_FILE_NAME = "google_maps_cache.json"

# ============================================================
# LOGGING
# ============================================================

LOG_FILE_NAME = "google_maps.log"

# ============================================================
# EXCEL COLUMNS
# ============================================================

TITLE_COLUMNS = [
    "title",
    "name",
    "hotel_name",
    "business_name",
    "Tên",
    "Tên khách sạn",
    "Tên cơ sở",
]

ADDRESS_COLUMNS = [
    "address",
    "full_address",
    "location",
    "Địa chỉ",
    "Địa chỉ đầy đủ",
]

URL_COLUMNS = [
    "url",
    "website",
    "link",
]

GOOGLE_MAPS_COLUMNS = [
    "google_maps_url",
    "google_map_url",
    "maps_url",
    "google maps",
    "Google Maps",
    "Google Maps URL",
]
