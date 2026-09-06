# ============================================================
# app/utils.py
# ============================================================

import json
import logging
import random
import re
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote, urlparse


# ============================================================
# TEXT
# ============================================================


def safe_text(value):
    if value is None:
        return ""

    try:
        # Tránh dependency pandas trong utils
        if str(value).lower() == "nan":
            return ""
    except Exception:
        pass

    return str(value).strip()


def normalize_name(value):
    """
    Normalize text để matching.

    Ví dụ:

    "Minh Quân Hotel"
    ->
    "minh quan hotel"
    """

    text = safe_text(value)

    if not text:
        return ""

    text = unicodedata.normalize(
        "NFD",
        text,
    )

    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    text = text.replace(
        "đ",
        "d",
    ).replace(
        "Đ",
        "D",
    )

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def normalize_address(value):
    return normalize_name(value)


def build_cache_key(title, address):
    title_normalized = normalize_name(title)
    address_normalized = normalize_address(address)

    return f"{title_normalized}|{address_normalized}"


# ============================================================
# SIMILARITY
# ============================================================


def token_similarity(a, b):
    if not a or not b:
        return 0.0

    tokens_a = set(a.split())
    tokens_b = set(b.split())

    if not tokens_a or not tokens_b:
        return 0.0

    intersection = len(tokens_a & tokens_b)

    union = len(tokens_a | tokens_b)

    if union == 0:
        return 0.0

    return intersection / union * 100


def sequence_similarity(a, b):
    if not a or not b:
        return 0.0

    return (
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()
        * 100
    )


# ============================================================
# URL
# ============================================================


def is_google_maps_url(url):
    """
    Kiểm tra URL có thuộc Google Maps hay không.

    Không yêu cầu /maps/place/.
    """

    value = safe_text(url)

    if not value:
        return False

    try:
        parsed = urlparse(value)
    except ValueError:
        return False

    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        in {
            "google.com",
            "www.google.com",
            "maps.google.com",
        }
        and parsed.path.lower().startswith("/maps")
    )


def is_google_maps_place_url(url):
    """
    Giữ lại hàm này để tương thích code cũ.

    Nhưng KHÔNG dùng nó để xác định
    candidate có đúng địa điểm hay không.
    """

    value = safe_text(url)

    if not value or not is_google_maps_url(value):
        return False

    try:
        path = urlparse(value).path.lower()
    except ValueError:
        return False

    return path.startswith("/maps/place/")


def clean_google_maps_url(url):
    """
    Clean Google Maps URL.

    Không phụ thuộc /maps/place/.

    Có thể nhận:
        /maps/place/
        /maps/search/
        /maps/dir/
        /maps/@...
        các dạng Google Maps URL khác

    Miễn là URL thuộc Google Maps.
    """

    url = safe_text(url)

    if not url:
        return ""

    if not is_google_maps_url(url):
        return ""

    # Remove fragment
    url = url.split("#")[0]

    return url.strip()


def build_google_maps_search_url(
    title,
    address,
):
    title = safe_text(title)
    address = safe_text(address)

    parts = []

    if title:
        parts.append(title)

    if address:
        parts.append(address)

    query = ", ".join(parts).strip()

    if not query:
        return ""

    return "https://www.google.com/maps/search/?api=1&query=" + quote(query)


# ============================================================
# RANDOM / DELAY
# ============================================================


def random_delay(
    minimum,
    maximum,
):
    if maximum <= minimum:
        time.sleep(max(0, minimum))
        return

    time.sleep(
        random.uniform(
            minimum,
            maximum,
        )
    )


# ============================================================
# TIME
# ============================================================


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ============================================================
# JSON
# ============================================================


def load_json(
    path,
    default=None,
):
    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except Exception:
        return default if default is not None else {}


def save_json_atomic(
    data,
    path,
):
    path = str(path)

    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    import os

    os.replace(
        temp_path,
        path,
    )


# ============================================================
# LOGGING
# ============================================================


def setup_logger(log_file):
    logger = logging.getLogger("google_maps_tool")

    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(
        log_file,
        encoding="utf-8",
    )

    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()

    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    return logger
