# # ============================================================
# # check_map.py
# # ============================================================
# #
# # Google Maps SECOND-PASS CHECKER
# #
# # Mục đích:
# #   - Không search Google Maps lại
# #   - Không import search.py
# #   - Đọc trực tiếp google_maps_url từ Excel
# #   - Mở URL
# #   - Lấy Title + Address thực tế
# #   - So sánh với Title + Address trong Excel
# #   - Phát hiện Maps URL bị nhảy sang địa điểm khác
# #
# # Input columns:
# #   Title
# #   Address
# #   URL
# #   google_maps_url
# #
# # Output columns:
# #   maps_check_status
# #   maps_check_title
# #   maps_check_address
# #   maps_check_url
# #   maps_title_match
# #   maps_address_match
# #   maps_check_reason
# #   maps_check_coordinates
# #   maps_check_time
# #
# # ============================================================

# import os
# import re
# import time
# import shutil
# import logging
# import unicodedata
# from datetime import datetime
# from pathlib import Path
# from urllib.parse import unquote

# import pandas as pd

# from playwright.sync_api import (
#     sync_playwright,
#     TimeoutError as PlaywrightTimeoutError,
# )

# from config import HEADLESS, INPUT_FILE, OUTPUT_FILE, PAGE_TIMEOUT


# # ============================================================
# # CONFIG
# # ============================================================

# # INPUT_FILE = "hotels_export_google_maps_result.xlsx"

# # OUTPUT_FILE = "result_checked.xlsx"

# # CHECKPOINT_FILE = "check_map_checkpoint.json"

# # HEADLESS = True

# # PAGE_TIMEOUT = 30_000

# NAVIGATION_TIMEOUT = 30_000

# WAIT_AFTER_LOAD = 2.0

# RETRY_COUNT = 3

# CHECKPOINT_EVERY = 10

# # Nếu True:
# #   chỉ check những dòng có google_maps_url
# CHECK_ONLY_WITH_MAPS_URL = True


# # ============================================================
# # COLUMN NAMES
# # ============================================================

# TITLE_COLUMN = "Title"

# ADDRESS_COLUMN = "Address"

# URL_COLUMN = "URL"

# GOOGLE_MAPS_COLUMN = "google_maps_url"


# # ============================================================
# # OUTPUT COLUMNS
# # ============================================================

# CHECK_STATUS_COLUMN = "maps_check_status"

# # CHECK_TITLE_COLUMN = "maps_check_title"

# CHECK_ADDRESS_COLUMN = "maps_check_address"

# CHECK_URL_COLUMN = "maps_check_url"

# # TITLE_MATCH_COLUMN = "maps_title_match"

# ADDRESS_MATCH_COLUMN = "maps_address_match"

# CHECK_REASON_COLUMN = "maps_check_reason"

# CHECK_COORDINATES_COLUMN = "maps_check_coordinates"

# CHECK_TIME_COLUMN = "maps_check_time"


# # ============================================================
# # LOGGING
# # ============================================================

# logging.basicConfig(
#     level=logging.INFO,
#     format=("%(asctime)s | %(levelname)s | %(message)s"),
# )

# logger = logging.getLogger("check_map")


# # ============================================================
# # TEXT NORMALIZATION
# # ============================================================


# def normalize_text(value):
#     """
#     Normalize text để so sánh title/address.

#     Ví dụ:

#         Hotel Hoang Phuc
#         Hotel Hoàng Phúc

#     sẽ gần như giống nhau sau khi bỏ dấu.

#     Đồng thời:
#         - lowercase
#         - unicode normalize
#         - bỏ ký tự đặc biệt
#         - gom khoảng trắng
#     """

#     if value is None:
#         return ""

#     try:
#         text = str(value)
#     except Exception:
#         return ""

#     text = text.strip()

#     if not text:
#         return ""

#     # Loại bỏ Unicode control characters / private-use icon
#     cleaned_chars = []

#     for char in text:
#         category = unicodedata.category(char)

#         # Private Use Area
#         if category == "Co":
#             continue

#         # Control / format
#         if category in {"Cc", "Cf"}:
#             continue

#         cleaned_chars.append(char)

#     text = "".join(cleaned_chars)

#     text = unquote(text)

#     text = unicodedata.normalize(
#         "NFKD",
#         text,
#     )

#     text = "".join(char for char in text if not unicodedata.combining(char))

#     text = text.lower()

#     text = re.sub(
#         r"[^a-z0-9\s]",
#         " ",
#         text,
#     )

#     text = re.sub(
#         r"\s+",
#         " ",
#         text,
#     )

#     return text.strip()


# # ============================================================
# # INVALID ADDRESS DETECTION
# # ============================================================


# def is_invalid_extracted_text(value):
#     """
#     Google Maps đôi khi trả về icon dạng:

#         \\ue413
#         \\ue52e
#         \\ue5...

#     Đây KHÔNG phải address.

#     Hàm này dùng để loại bỏ chúng.
#     """

#     if value is None:
#         return True

#     try:
#         text = str(value).strip()
#     except Exception:
#         return True

#     if not text:
#         return True

#     # Chỉ có private-use unicode
#     if all(unicodedata.category(char) == "Co" for char in text):
#         return True

#     # Các pattern dạng \ue413
#     if re.fullmatch(
#         r"(\\u[0-9a-fA-F]{4})+",
#         text,
#     ):
#         return True

#     # Chuỗi quá ngắn không có giá trị
#     normalized = normalize_text(text)

#     if len(normalized) < 3:
#         return True

#     return False


# # ============================================================
# # GOOGLE MAPS URL
# # ============================================================


# def is_google_maps_url(url):
#     if not url:
#         return False

#     value = str(url).strip().lower()

#     return "google.com/maps" in value or "maps.google.com" in value


# def is_google_maps_place_url(url):
#     """
#     Chỉ chấp nhận URL dạng:

#         /maps/place/...
#     """

#     if not is_google_maps_url(url):
#         return False

#     return "/maps/place/" in str(url).lower()


# # ============================================================
# # COORDINATES
# # ============================================================


# def extract_coordinates(url):
#     """
#     Lấy coordinates từ Google Maps URL.

#     Hỗ trợ:

#         !3d14.123!4d109.123

#     hoặc:

#         @14.123,109.123
#     """

#     if not url:
#         return None

#     text = str(url)

#     # --------------------------------------------------------
#     # !3dLAT!4dLNG
#     # --------------------------------------------------------

#     match = re.search(
#         r"!3d(-?\d+(?:\.\d+)?)"
#         r"!4d(-?\d+(?:\.\d+)?)",
#         text,
#     )

#     if match:
#         lat = match.group(1)
#         lng = match.group(2)

#         return (
#             float(lat),
#             float(lng),
#         )

#     # --------------------------------------------------------
#     # @LAT,LNG
#     # --------------------------------------------------------

#     match = re.search(
#         r"@(-?\d+(?:\.\d+)?),"
#         r"(-?\d+(?:\.\d+)?)",
#         text,
#     )

#     if match:
#         lat = match.group(1)
#         lng = match.group(2)

#         return (
#             float(lat),
#             float(lng),
#         )

#     return None


# # ============================================================
# # SAFE PAGE TEXT
# # ============================================================


# def get_body_text(page):
#     try:
#         return page.locator("body").inner_text(
#             timeout=5_000,
#         )
#     except Exception:
#         return ""


# # ============================================================
# # EXTRACT TITLE
# # ============================================================


# def extract_title(page):
#     """
#     Lấy title của Place.

#     Ưu tiên các selector phổ biến của Google Maps.
#     """

#     selectors = [
#         "h1.DUwDvf",
#         "h1.fontHeadlineLarge",
#         "h1",
#         "[role='main'] h1",
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = locator.count()

#             if count <= 0:
#                 continue

#             for index in range(min(count, 3)):
#                 try:
#                     text = locator.nth(index).inner_text(
#                         timeout=2_000,
#                     )

#                     if text and text.strip():
#                         text = text.strip()

#                         if not is_invalid_extracted_text(text):
#                             return text

#                 except Exception:
#                     continue

#         except Exception:
#             continue

#     # Fallback page title
#     try:
#         page_title = page.title(
#             timeout=3_000,
#         )

#         if page_title:
#             page_title = page_title.strip()

#             # Google Maps thường có:
#             # "Hotel Hoàng Phúc - Google Maps"
#             page_title = re.sub(
#                 r"\s*-\s*Google Maps\s*$",
#                 "",
#                 page_title,
#                 flags=re.IGNORECASE,
#             )

#             if not is_invalid_extracted_text(page_title):
#                 return page_title

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # ADDRESS SELECTORS
# # ============================================================


# # ============================================================
# # EXTRACT ADDRESS - ROBUST
# # ============================================================

# def extract_address(
#     page,
#     expected_address="",
# ):
#     """
#     Extract address từ Google Maps.

#     Ưu tiên:
#         1. data-item-id address
#         2. aria-label address
#         3. .Io6YTe
#         4. các selector khác

#     Nếu có expected_address:
#         chọn candidate gần với expected_address nhất.
#     """

#     selectors = [
#         "button[data-item-id='address']",
#         "button[data-item-id^='address']",
#         "div[data-item-id='address']",
#         "div[data-item-id^='address']",
#         "a[data-item-id='address']",
#         "a[data-item-id^='address']",

#         "[role='button'][data-item-id='address']",
#         "[role='button'][data-item-id^='address']",

#         "button[aria-label*='Address']",
#         "button[aria-label*='Địa chỉ']",
#         "[aria-label*='Address']",
#         "[aria-label*='Địa chỉ']",

#         "[data-tooltip*='Address']",
#         "[data-tooltip*='Địa chỉ']",

#         ".Io6YTe",

#         "[jsaction*='address']",
#     ]

#     candidates = []

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = locator.count()

#             if count <= 0:
#                 continue

#             for index in range(min(count, 30)):
#                 try:
#                     element = locator.nth(index)

#                     values = []

#                     try:
#                         text = element.inner_text(
#                             timeout=1000,
#                         )
#                         if text:
#                             values.append(text)
#                     except Exception:
#                         pass

#                     try:
#                         text = element.text_content(
#                             timeout=1000,
#                         )
#                         if text:
#                             values.append(text)
#                     except Exception:
#                         pass

#                     try:
#                         value = element.get_attribute(
#                             "aria-label"
#                         )
#                         if value:
#                             values.append(value)
#                     except Exception:
#                         pass

#                     try:
#                         value = element.get_attribute(
#                             "data-tooltip"
#                         )
#                         if value:
#                             values.append(value)
#                     except Exception:
#                         pass

#                     for value in values:
#                         value = str(value).strip()

#                         if is_invalid_extracted_text(value):
#                             continue

#                         normalized = normalize_text(value)

#                         if normalized in {
#                             "address",
#                             "dia chi",
#                             "copy address",
#                             "sao chep dia chi",
#                         }:
#                             continue

#                         if value not in candidates:
#                             candidates.append(value)

#                 except Exception:
#                     continue

#         except Exception:
#             continue

#     if not candidates:
#         return ""

#     # --------------------------------------------------------
#     # Nếu có expected address -> chọn candidate gần nhất
#     # --------------------------------------------------------

#     expected = normalize_text(expected_address)

#     if expected:
#         best_candidate = ""
#         best_score = 0.0

#         for candidate in candidates:
#             score = address_similarity(
#                 expected,
#                 candidate,
#             )

#             if score > best_score:
#                 best_score = score
#                 best_candidate = candidate

#         if best_candidate:
#             return best_candidate

#     # --------------------------------------------------------
#     # Fallback: ưu tiên text có số
#     # --------------------------------------------------------

#     for candidate in candidates:
#         if re.search(r"\d", candidate):
#             return candidate

#     return candidates[0]
# # ============================================================
# # WAIT FOR ADDRESS
# # ============================================================

# def wait_for_address(
#     page,
#     expected_address,
#     timeout_seconds=5.0,
# ):
#     """
#     Chờ Google Maps render address.

#     Poll nhanh thay vì sleep cố định lâu.
#     """

#     start_time = time.time()

#     while time.time() - start_time < timeout_seconds:
#         actual_address = extract_address(page,expected_address)

#         if not is_invalid_extracted_text(actual_address):
#             return actual_address

#         time.sleep(0.5)

#     return ""
# # ============================================================
# # ADDRESS FROM BODY
# # ============================================================


# def extract_address_from_body(
#     page,
#     input_address,
# ):
#     """
#     Fallback.

#     Không cố lấy bừa một dòng.

#     Chỉ tìm những dòng có khả năng liên quan
#     tới address input.
#     """

#     body = get_body_text(page)

#     if not body:
#         return ""

#     lines = [line.strip() for line in body.splitlines() if line.strip()]

#     input_normalized = normalize_text(input_address)

#     if not input_normalized:
#         return ""

#     input_tokens = [token for token in input_normalized.split() if len(token) >= 3]

#     if not input_tokens:
#         return ""

#     best_line = ""

#     best_score = 0.0

#     for line in lines:
#         if is_invalid_extracted_text(line):
#             continue

#         normalized_line = normalize_text(line)

#         if len(normalized_line) < 5:
#             continue

#         matched = sum(1 for token in input_tokens if token in normalized_line)

#         score = matched / max(
#             len(input_tokens),
#             1,
#         )

#         if score > best_score:
#             best_score = score
#             best_line = line

#     if best_score >= 0.40:
#         return best_line

#     return ""


# # ============================================================
# # TITLE MATCH
# # ============================================================


# # def title_similarity(
# #     input_title,
# #     actual_title,
# # ):
# #     """
# #     So sánh title theo token.

# #     Không yêu cầu giống 100%.

# #     Hotel Hoang Phuc
# #     Hotel Hoàng Phúc

# #     -> gần 100%
# #     """

# #     a = normalize_text(input_title)

# #     b = normalize_text(actual_title)

# #     if not a or not b:
# #         return 0.0

# #     if a == b:
# #         return 1.0

# #     if a in b or b in a:
# #         return 0.90

# #     tokens_a = set(a.split())
# #     tokens_b = set(b.split())

# #     if not tokens_a or not tokens_b:
# #         return 0.0

# #     intersection = tokens_a & tokens_b

# #     precision = len(intersection) / len(tokens_a)

# #     recall = len(intersection) / len(tokens_b)

# #     if precision + recall == 0:
# #         return 0.0

# #     f1 = 2 * precision * recall / (precision + recall)

# #     return f1


# # ============================================================
# # ADDRESS MATCH
# # ============================================================


# def address_similarity(
#     input_address,
#     actual_address,
# ):
#     """
#     So sánh address theo token.

#     Ví dụ:

#         Input:
#         262 Nguyen Hue, Binh Duong, Gia Lai

#         Maps:
#         262 Nguyễn Huệ, Bình Dương, Gia Lai

#     -> gần 100%
#     """

#     a = normalize_text(input_address)

#     b = normalize_text(actual_address)

#     if not a or not b:
#         return 0.0

#     if a == b:
#         return 1.0

#     if a in b or b in a:
#         return 0.90

#     tokens_a = {token for token in a.split() if len(token) >= 2}

#     tokens_b = {token for token in b.split() if len(token) >= 2}

#     if not tokens_a or not tokens_b:
#         return 0.0

#     intersection = tokens_a & tokens_b

#     precision = len(intersection) / len(tokens_a)

#     recall = len(intersection) / len(tokens_b)

#     if precision + recall == 0:
#         return 0.0

#     f1 = 2 * precision * recall / (precision + recall)

#     return f1


# # ============================================================
# # MATCH DECISION
# # ============================================================


# # ============================================================
# # MATCH DECISION - ADDRESS ONLY
# # ============================================================

# def decide_match(expected_address, actual_address):
#     """
#     Chỉ kiểm tra Address.

#     Không kiểm tra:
#         - Title
#         - tên khách sạn
#         - URL slug

#     Chỉ kiểm tra:
#         Excel Address
#             VS
#         Google Maps Address
#     """

#     expected = normalize_text(expected_address)
#     actual = normalize_text(actual_address)

#     # --------------------------------------------------------
#     # Excel không có address
#     # --------------------------------------------------------

#     if not expected:
#         return {
#             "status": "NEED_REVIEW",
#             "address_match": False,
#             "reason": "MISSING_EXCEL_ADDRESS",
#             "address_score": 0.0,
#         }

#     # --------------------------------------------------------
#     # Google Maps không lấy được address
#     # --------------------------------------------------------

#     if not actual or is_invalid_extracted_text(actual):
#         return {
#             "status": "NEED_REVIEW",
#             "address_match": False,
#             "reason": "MISSING_MAPS_ADDRESS",
#             "address_score": 0.0,
#         }

#     # --------------------------------------------------------
#     # Compare
#     # --------------------------------------------------------

#     score = address_similarity(
#         expected,
#         actual,
#     )

#     # --------------------------------------------------------
#     # Match
#     # --------------------------------------------------------

#     if score >= 0.55:
#         return {
#             "status": "MATCH",
#             "address_match": True,
#             "reason": "ADDRESS_MATCH",
#             "address_score": score,
#         }

#     # --------------------------------------------------------
#     # Mismatch
#     # --------------------------------------------------------

#     return {
#         "status": "MISMATCH",
#         "address_match": False,
#         "reason": "ADDRESS_MISMATCH",
#         "address_score": score,
#     }


# # ============================================================
# # CLOSE GOOGLE POPUPS
# # ============================================================


# def close_google_popups(page):
#     selectors = [
#         "button[aria-label='Accept all']",
#         "button[aria-label='Chấp nhận tất cả']",
#         "button:has-text('Accept all')",
#         "button:has-text('Chấp nhận tất cả')",
#         "button[aria-label='Close']",
#         "button[aria-label='Đóng']",
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = locator.count()

#             if count <= 0:
#                 continue

#             for index in range(min(count, 3)):
#                 try:
#                     button = locator.nth(index)

#                     if button.is_visible(timeout=500):
#                         button.click(
#                             timeout=2_000,
#                         )

#                         time.sleep(0.5)

#                 except Exception:
#                     continue

#         except Exception:
#             continue


# # ============================================================
# # AW SNAP
# # ============================================================


# def is_aw_snap(page):
#     try:
#         title = page.title(
#             timeout=2_000,
#         )

#         if "Aw, Snap" in title:
#             return True

#     except Exception:
#         pass

#     try:
#         body = get_body_text(page)

#         if "Aw, Snap" in body:
#             return True

#     except Exception:
#         pass

#     return False


# # ============================================================
# # OPEN MAP URL
# # ============================================================


# def open_maps_url(
#     page,
#     url,
# ):
#     """
#     Mở trực tiếp Maps URL.

#     ERR_ABORTED đôi khi xảy ra với Google Maps
#     nhưng navigation thực tế vẫn thành công.

#     Vì vậy sau exception vẫn kiểm tra current_url.
#     """

#     last_error = ""

#     for attempt in range(
#         1,
#         RETRY_COUNT + 1,
#     ):
#         logger.info(
#             "Map open | attempt=%s/%s | url=%s",
#             attempt,
#             RETRY_COUNT,
#             url,
#         )

#         try:
#             page.goto(
#                 url,
#                 wait_until="domcontentloaded",
#                 timeout=NAVIGATION_TIMEOUT,
#             )

#         except PlaywrightTimeoutError as exc:
#             last_error = f"TIMEOUT: {exc}"

#             logger.warning(
#                 "Navigation timeout | attempt=%s",
#                 attempt,
#             )

#         except Exception as exc:
#             last_error = str(exc)

#             logger.warning(
#                 "Navigation error | attempt=%s | %s",
#                 attempt,
#                 exc,
#             )

#         time.sleep(WAIT_AFTER_LOAD)

#         # ----------------------------------------------------
#         # Check Aw Snap
#         # ----------------------------------------------------

#         if is_aw_snap(page):
#             logger.warning("Aw, Snap detected")

#             try:
#                 page.reload(
#                     wait_until="domcontentloaded",
#                     timeout=NAVIGATION_TIMEOUT,
#                 )

#                 time.sleep(WAIT_AFTER_LOAD)

#             except Exception:
#                 pass

#         # ----------------------------------------------------
#         # Check current URL
#         # ----------------------------------------------------

#         try:
#             current_url = page.url

#         except Exception:
#             current_url = ""

#         logger.info(
#             "Current URL | %s",
#             current_url,
#         )

#         if is_google_maps_place_url(current_url):
#             return {
#                 "success": True,
#                 "url": current_url,
#                 "error": "",
#             }

#         # ----------------------------------------------------
#         # If original URL itself is a Place URL
#         # ----------------------------------------------------

#         if attempt == RETRY_COUNT and is_google_maps_place_url(url):
#             return {
#                 "success": False,
#                 "url": current_url or url,
#                 "error": ("URL_IS_PLACE_BUT_NAVIGATION_FAILED"),
#             }

#         time.sleep(1)

#     return {
#         "success": False,
#         "url": "",
#         "error": last_error or "NAVIGATION_FAILED",
#     }


# # ============================================================
# # CHECK ONE ROW
# # ============================================================


# # ============================================================
# # CHECK ONE ROW
# # ============================================================

# def check_one(
#     page,
#     address,
#     maps_url,
# ):
#     result = {
#         "status": "",
#         "title": "",
#         "address": "",
#         "url": "",
#         "title_match": None,
#         "address_match": None,
#         "reason": "",
#         "coordinates": None,
#     }

#     # --------------------------------------------------------
#     # Empty URL
#     # --------------------------------------------------------

#     if not maps_url:
#         result["status"] = "NO_MAPS_URL"
#         result["reason"] = "EMPTY_GOOGLE_MAPS_URL"
#         return result

#     maps_url = str(maps_url).strip()

#     # --------------------------------------------------------
#     # Invalid Google Maps URL
#     # --------------------------------------------------------

#     if not is_google_maps_url(maps_url):
#         result["status"] = "INVALID_URL"
#         result["reason"] = "NOT_GOOGLE_MAPS_URL"
#         result["url"] = maps_url
#         return result

#     # --------------------------------------------------------
#     # Not Place URL
#     # --------------------------------------------------------

#     if not is_google_maps_place_url(maps_url):
#         result["status"] = "INVALID_URL"
#         result["reason"] = "NOT_GOOGLE_MAPS_PLACE_URL"
#         result["url"] = maps_url
#         return result

#     # --------------------------------------------------------
#     # Open Maps URL
#     # --------------------------------------------------------

#     opened = open_maps_url(
#         page,
#         maps_url,
#     )

#     final_url = opened.get(
#         "url",
#         "",
#     )

#     result["url"] = final_url or maps_url

#     # --------------------------------------------------------
#     # Navigation failed
#     # --------------------------------------------------------

#     if not opened.get("success"):
#         result["status"] = "OPEN_FAILED"
#         result["reason"] = (
#             opened.get("error")
#             or "NAVIGATION_FAILED"
#         )

#         result["coordinates"] = extract_coordinates(
#             result["url"]
#         )

#         return result

#     # --------------------------------------------------------
#     # Close popups
#     # --------------------------------------------------------

#     close_google_popups(page)

#     time.sleep(
#         WAIT_AFTER_LOAD
#     )

#     # --------------------------------------------------------
#     # DO NOT CHECK TITLE
#     # --------------------------------------------------------

#     # Title intentionally ignored.

#     # --------------------------------------------------------
#     # Extract Address
#     # --------------------------------------------------------

#     actual_address = wait_for_address(
#     page,
#     address,
#     timeout_seconds=5.0,
# )

#     if is_invalid_extracted_text(
#         actual_address
#     ):
#         actual_address = ""

#     # --------------------------------------------------------
#     # Fallback Address
#     # --------------------------------------------------------

#     if not actual_address:
#         actual_address = extract_address_from_body(
#             page,
#             address,
#         )

#     if is_invalid_extracted_text(
#         actual_address
#     ):
#         actual_address = ""

#     # --------------------------------------------------------
#     # Coordinates
#     # --------------------------------------------------------

#     coordinates = extract_coordinates(
#         result["url"]
#     )

#     # --------------------------------------------------------
#     # Decide - ADDRESS ONLY
#     # --------------------------------------------------------

#     decision = decide_match(
#         address,
#         actual_address,
#     )

#     result.update(
#         {
#             "status": decision["status"],
#             "title": "",
#             "address": actual_address,
#             "title_match": None,
#             "address_match": decision["address_match"],
#             "reason": decision["reason"],
#             "coordinates": coordinates,
#         }
#     )

#     # --------------------------------------------------------
#     # Log
#     # --------------------------------------------------------

#     logger.info(
#         "CHECK RESULT | "
#         "status=%s | "
#         "address_match=%s | "
#         "input_address=%r | "
#         "actual_address=%r | "
#         "score=%.3f",
#         result["status"],
#         result["address_match"],
#         address,
#         actual_address,
#         decision.get(
#             "address_score",
#             0.0,
#         ),
#     )

#     return result


# # ============================================================
# # SAFE EXCEL SAVE
# # ============================================================


# def save_excel_atomic(
#     df,
#     output_file,
# ):
#     output_path = Path(output_file)

#     temp_path = output_path.with_suffix(".tmp.xlsx")

#     df.to_excel(
#         temp_path,
#         index=False,
#     )

#     os.replace(
#         temp_path,
#         output_path,
#     )


# # ============================================================
# # MAIN
# # ============================================================


# def main():
#     logger.info("============================================================")

#     logger.info("GOOGLE MAPS SECOND-PASS CHECKER")

#     logger.info(
#         "Input  : %s",
#         INPUT_FILE,
#     )

#     logger.info(
#         "Output : %s",
#         OUTPUT_FILE,
#     )

#     logger.info("============================================================")

#     # --------------------------------------------------------
#     # Validate input
#     # --------------------------------------------------------

#     if not INPUT_FILE.exists():
#         raise FileNotFoundError(f"Input Excel not found: {INPUT_FILE}")

#     if OUTPUT_FILE.exists():
#       logger.info(
#           "Existing output found. Resuming from: %s",
#           OUTPUT_FILE,
#       )
#       df = pd.read_excel(OUTPUT_FILE)
#     else:
#         logger.info(
#             "Loading input: %s",
#             INPUT_FILE,
#         )
#         df = pd.read_excel(INPUT_FILE)

#     logger.info(
#         "Total rows: %s",
#         len(df),
#     )

#     # --------------------------------------------------------
#     # Validate columns
#     # --------------------------------------------------------

#     required_columns = [
#         # TITLE_COLUMN,
#         ADDRESS_COLUMN,
#         GOOGLE_MAPS_COLUMN,
#     ]

#     missing_columns = [
#         column for column in required_columns if column not in df.columns
#     ]

#     if missing_columns:
#         raise ValueError("Missing required columns: " + ", ".join(missing_columns))

#     # --------------------------------------------------------
#     # Add output columns
#     # --------------------------------------------------------

#     output_columns = [
#         CHECK_STATUS_COLUMN,
#         # CHECK_TITLE_COLUMN,
#         CHECK_ADDRESS_COLUMN,
#         CHECK_URL_COLUMN,
#         # TITLE_MATCH_COLUMN,
#         ADDRESS_MATCH_COLUMN,
#         CHECK_REASON_COLUMN,
#         CHECK_COORDINATES_COLUMN,
#         CHECK_TIME_COLUMN,
#     ]

#     for column in output_columns:
#         if column not in df.columns:
#             df[column] = None

#     # --------------------------------------------------------
#     # Launch browser
#     # --------------------------------------------------------

#     with sync_playwright() as p:
#         browser = p.chromium.launch(
#             headless=HEADLESS,
#         )

#         context = browser.new_context(
#             viewport={
#                 "width": 1280,
#                 "height": 900,
#             },
#             locale="vi-VN",
#             timezone_id="Asia/Ho_Chi_Minh",
#         )

#         page = context.new_page()

#         page.set_default_timeout(PAGE_TIMEOUT)

#         # ----------------------------------------------------
#         # Counters
#         # ----------------------------------------------------

#         total = len(df)

#         checked = 0

#         match_count = 0

#         mismatch_count = 0

#         review_count = 0

#         failed_count = 0

#         no_url_count = 0

#         # ----------------------------------------------------
#         # Loop
#         # ----------------------------------------------------

#         for index, row in df.iterrows():
#             position = index + 1

#             title = str(
#                 row.get(
#                     TITLE_COLUMN,
#                     "",
#                 )
#             ).strip()

#             if title == "nan":
#                 title = ""

#             address = str(
#                 row.get(
#                     ADDRESS_COLUMN,
#                     "",
#                 )
#             ).strip()

#             if address == "nan":
#                 address = ""

#             maps_url = row.get(
#                 GOOGLE_MAPS_COLUMN,
#                 "",
#             )

#             if pd.isna(maps_url):
#                 maps_url = ""

#             maps_url = str(maps_url).strip()

#             print()
#             print("------------------------------------------------------------")

#             print(f"[{position}/{total}] {title}")

#             print(f"📍 {address}")

#             print(f"🔗 {maps_url or '(EMPTY)'}")

#             # ------------------------------------------------
#             # Skip already checked
#             # ------------------------------------------------

#             existing_status = row.get(CHECK_STATUS_COLUMN)

#             if pd.notna(existing_status) and str(existing_status).strip() in {
#                 "MATCH",
#                 "MISMATCH",
#                 "NEED_REVIEW",
#                 "OPEN_FAILED",
#                 "INVALID_URL",
#                 "NO_MAPS_URL",
#             }:
#                 logger.info(
#                     "SKIP already checked | status=%s",
#                     existing_status,
#                 )

#                 continue

#             # ------------------------------------------------
#             # No URL
#             # ------------------------------------------------

#             if not maps_url:
#                 df.at[
#                     index,
#                     CHECK_STATUS_COLUMN,
#                 ] = "NO_MAPS_URL"

#                 df.at[
#                     index,
#                     CHECK_REASON_COLUMN,
#                 ] = "EMPTY_GOOGLE_MAPS_URL"

#                 df.at[
#                     index,
#                     CHECK_TIME_COLUMN,
#                 ] = datetime.now().isoformat(timespec="seconds")

#                 no_url_count += 1

#                 print("⚪ NO MAPS URL")

#                 continue

#             # ------------------------------------------------
#             # Check
#             # ------------------------------------------------

#             try:
#                 result = check_one(
#                     page,
#                     address,
#                     maps_url,
#                 )

#                 df.at[
#                     index,
#                     CHECK_STATUS_COLUMN,
#                 ] = result["status"]

#                 # df.at[
#                 #     index,
#                 #     CHECK_TITLE_COLUMN,
#                 # ] = result["title"]

#                 df.at[
#                     index,
#                     CHECK_ADDRESS_COLUMN,
#                 ] = result["address"]

#                 df.at[
#                     index,
#                     CHECK_URL_COLUMN,
#                 ] = result["url"]

#                 # df.at[
#                 #     index,
#                 #     TITLE_MATCH_COLUMN,
#                 # ] = result["title_match"]

#                 df.at[
#                     index,
#                     ADDRESS_MATCH_COLUMN,
#                 ] = result["address_match"]

#                 df.at[
#                     index,
#                     CHECK_REASON_COLUMN,
#                 ] = result["reason"]

#                 coordinates = result.get("coordinates")

#                 if coordinates:
#                     df.at[
#                         index,
#                         CHECK_COORDINATES_COLUMN,
#                     ] = f"{coordinates[0]},{coordinates[1]}"
#                 else:
#                     df.at[
#                         index,
#                         CHECK_COORDINATES_COLUMN,
#                     ] = ""

#                 df.at[
#                     index,
#                     CHECK_TIME_COLUMN,
#                 ] = datetime.now().isoformat(timespec="seconds")

#                 # ------------------------------------------------
#                 # Counters
#                 # ------------------------------------------------

#                 status = result["status"]

#                 if status == "MATCH":
#                     match_count += 1

#                     print("✅ MATCH")

#                 elif status == "MISMATCH":
#                     mismatch_count += 1

#                     print("❌ MISMATCH")

#                 elif status == "NEED_REVIEW":
#                     review_count += 1

#                     print("⚠️ NEED REVIEW")

#                 else:
#                     failed_count += 1

#                     print(f"❌ {status}")

#                 print(f"   Maps title: {result.get('title', '')}")

#                 print(f"   Maps address: {result.get('address', '')}")

#                 print(f"   Reason: {result.get('reason', '')}")

#                 if result.get("coordinates"):
#                     print(f"   Coordinates: {result['coordinates']}")

#                 checked += 1

#             except Exception as exc:
#                 logger.exception(
#                     "Unexpected error at row %s",
#                     position,
#                 )

#                 df.at[
#                     index,
#                     CHECK_STATUS_COLUMN,
#                 ] = "ERROR"

#                 df.at[
#                     index,
#                     CHECK_REASON_COLUMN,
#                 ] = str(exc)

#                 df.at[
#                     index,
#                     CHECK_TIME_COLUMN,
#                 ] = datetime.now().isoformat(timespec="seconds")

#                 failed_count += 1

#                 print(f"💥 ERROR: {exc}")

#             # ------------------------------------------------
#             # Checkpoint
#             # ------------------------------------------------

#             if position % CHECKPOINT_EVERY == 0:
#                 logger.info(
#                     "Saving checkpoint at row %s...",
#                     position,
#                 )

#                 save_excel_atomic(
#                     df,
#                     OUTPUT_FILE,
#                 )

#         # ----------------------------------------------------
#         # Final save
#         # ----------------------------------------------------

#         logger.info("Saving final output...")

#         save_excel_atomic(
#             df,
#             OUTPUT_FILE,
#         )

#         # ----------------------------------------------------
#         # Close
#         # ----------------------------------------------------

#         context.close()

#         browser.close()

#     # ========================================================
#     # SUMMARY
#     # ========================================================

#     print()
#     print("============================================================")

#     print("GOOGLE MAPS CHECK COMPLETED")

#     print("============================================================")

#     print(f"Total rows       : {total}")

#     print(f"Checked          : {checked}")

#     print(f"MATCH            : {match_count}")

#     print(f"MISMATCH         : {mismatch_count}")

#     print(f"NEED_REVIEW      : {review_count}")

#     print(f"FAILED / ERROR   : {failed_count}")

#     print(f"NO MAPS URL      : {no_url_count}")

#     print()
#     print(f"Output file: {OUTPUT_FILE}")

#     print("============================================================")


# # ============================================================
# # ENTRY POINT
# # ============================================================

# if __name__ == "__main__":
#     main()
# # ============================================================
# # app/check_maps.py
# # ============================================================

# # ============================================================
# # check_maps.py
# #
# # GOOGLE MAPS ADDRESS CHECKER
# #
# # Flow:
# #   1. Read Excel
# #   2. MATCH / MISMATCH -> skip khi resume
# #   3. NEED_REVIEW -> search lại Google Maps từ Excel Address
# #   4. OPEN_FAILED -> retry
# #   5. NO_MAPS_URL / INVALID_URL -> search lại nếu có Address
# #   6. Open fresh Google Maps Place URL
# #   7. Compare ONLY:
# #        Excel Address <-> Google Maps Address
# #   8. Coordinate chỉ dùng để debug / lưu kết quả
# #
# # search.py KHÔNG CẦN SỬA
# # ============================================================

# # ============================================================
# # app/check_maps.py
# #
# # GOOGLE MAPS ADDRESS CHECKER
# #
# # Logic:
# #   Excel Address
# #       ↓
# #   Google Maps URL
# #       ↓
# #   Open Google Maps Place
# #       ↓
# #   Extract actual Maps Address
# #       ↓
# #   Compare:
# #       Excel Address <-> Maps Address
# #
# # IMPORTANT:
# #   - Title KHÔNG quyết định MATCH/MISMATCH.
# #   - Coordinates KHÔNG quyết định MATCH/MISMATCH.
# #   - Coordinates chỉ dùng để debug.
# #   - MATCH / MISMATCH là FINAL.
# #   - NEED_REVIEW / OPEN_FAILED / NO_MAPS_URL /
# #     INVALID_URL sẽ được research lại.
# # ============================================================

# import logging
# import math
# import os
# import re
# import sys
# import subprocess
# import time
# import unicodedata
# from datetime import datetime
# from pathlib import Path
# from urllib.parse import unquote

# import pandas as pd

# from playwright.sync_api import (
#     Page,
#     TimeoutError as PlaywrightTimeoutError,
# )


# # ============================================================
# # PROJECT ROOT
# # ============================================================

# PROJECT_ROOT = Path(__file__).resolve().parent.parent

# if str(PROJECT_ROOT) not in sys.path:
#     sys.path.insert(0, str(PROJECT_ROOT))


# # ============================================================
# # IMPORT CONFIG
# # ============================================================

# from config import (
#     HEADLESS,
#     INPUT_FILE,
#     OUTPUT_FILE,
#     PAGE_TIMEOUT,
# )


# # ============================================================
# # IMPORT SEARCH
# # ============================================================

# try:
#     from .search import search_google_maps
# except ImportError:
#     from app.search import search_google_maps


# # ============================================================
# # LOGGER
# # ============================================================

# logger = logging.getLogger("check_maps")

# if not logger.handlers:
#     logging.basicConfig(
#         level=logging.INFO,
#         format=("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
#     )


# # ============================================================
# # CONFIG
# # ============================================================

# NAVIGATION_TIMEOUT = 30_000

# # Chờ sau khi Maps load
# WAIT_AFTER_LOAD = 2.0

# # Retry mở URL
# RETRY_COUNT = 3

# # Sau bao nhiêu record thì save checkpoint
# CHECKPOINT_EVERY = 10

# # Ngưỡng Address MATCH
# ADDRESS_MATCH_THRESHOLD = 0.55

# # Status final
# RESUME_SKIP_STATUSES = {
#     "MATCH",
#     "MISMATCH",
# }

# # Những status sẽ research lại
# FORCE_RESEARCH_STATUSES = {
#     "OPEN_FAILED",
#     "NO_MAPS_URL",
#     "INVALID_URL",
# }

# # ============================================================
# # REPAIR MODE
# # ============================================================
# # Các lỗi từ pass search chính cần check_maps sửa lại Title/Address/Maps URL.
# REPAIR_DETAIL_REASONS = {
#     "NOT_VERIFIED",
#     "ADDRESS_UNAVAILABLE",
#     "ADDRESS_MISMATCH",
#     "TITLE_LOCATION_MISMATCH",
#     "TITLE_MISMATCH",
#     "PROVINCE_MISMATCH",
#     "TITLE_PREFILTER_REJECTED",
#     "TITLE_NO_CANDIDATES",
# }

# # Chỉ tự sửa khi identity của place đủ mạnh.
# REPAIR_TITLE_THRESHOLD = 0.82

# # Các cột lỗi có thể được tạo bởi tool search chính.
# SOURCE_STATUS_COLUMNS = ("status", "Status", "search_status")
# SOURCE_REASON_COLUMNS = ("detail_reason", "reason", "missing_reason", "Missing Reason")

# REPAIR_OLD_TITLE_COLUMN = "maps_old_title"
# REPAIR_OLD_ADDRESS_COLUMN = "maps_old_address"
# REPAIR_NEW_TITLE_COLUMN = "maps_repaired_title"
# REPAIR_NEW_ADDRESS_COLUMN = "maps_repaired_address"
# REPAIR_STATUS_COLUMN = "maps_repair_status"
# REPAIR_REASON_COLUMN = "maps_repair_reason"

# # Tối đa thời gian cố lấy address sau khi Maps mở
# ADDRESS_WAIT_TIMEOUT = 18.0

# # Khoảng delay giữa các lần extract
# ADDRESS_POLL_INTERVAL = 0.5

# # Reload tối đa một lần khi address không render
# ADDRESS_RELOAD_ONCE = True

# # Chờ sau reload
# ADDRESS_RELOAD_WAIT = 2.0

# # Tối đa số dòng body/main text dùng để scan
# MAX_TEXT_LINES = 300

# # Tối đa số candidate address
# MAX_ADDRESS_CANDIDATES = 150


# # ============================================================
# # EXCEL COLUMNS
# # ============================================================

# TITLE_COLUMN = "Title"
# ADDRESS_COLUMN = "Address"
# URL_COLUMN = "URL"

# GOOGLE_MAPS_COLUMN = "google_maps_url"

# CHECK_STATUS_COLUMN = "maps_check_status"
# CHECK_ADDRESS_COLUMN = "maps_check_address"
# CHECK_URL_COLUMN = "maps_check_url"
# ADDRESS_MATCH_COLUMN = "maps_address_match"
# CHECK_REASON_COLUMN = "maps_check_reason"
# CHECK_COORDINATES_COLUMN = "maps_check_coordinates"
# CHECK_TIME_COLUMN = "maps_check_time"


# # ============================================================
# # GENERIC ADDRESS TOKENS
# # ============================================================

# GENERIC_ADDRESS_TOKENS = {
#     "vietnam",
#     "viet nam",
#     "vn",
#     "street",
#     "road",
#     "avenue",
#     "boulevard",
#     "highway",
#     "ward",
#     "district",
#     "province",
#     "city",
#     "phuong",
#     "quan",
#     "huyen",
#     "tinh",
#     "xa",
#     "thanh pho",
#     "tp",
#     "thi tran",
#     "thi xa",
#     "st",
#     "rd",
#     "ave",
#     "blvd",
#     "p",
#     "q",
# }


# # ============================================================
# # PLACEHOLDER / NON ADDRESS TEXT
# # ============================================================

# INVALID_ADDRESS_TEXTS = {
#     "",
#     "address",
#     "địa chỉ",
#     "dia chi",
#     "directions",
#     "chỉ đường",
#     "chi duong",
#     "website",
#     "phone",
#     "telephone",
#     "số điện thoại",
#     "so dien thoai",
#     "copy address",
#     "sao chép địa chỉ",
#     "sao chep dia chi",
#     "open now",
#     "mở cửa",
#     "mo cua",
#     "thông tin về dữ liệu này",
#     "thong tin ve du lieu nay",
#     "about this data",
#     "more information about this data",
# }


# # ============================================================
# # BASIC VALUE HELPERS
# # ============================================================


# def clean_value(value):
#     """
#     Convert pandas NaN / None / invalid values -> "".
#     """

#     if value is None:
#         return ""

#     try:
#         if pd.isna(value):
#             return ""
#     except Exception:
#         pass

#     value = str(value).strip()

#     if value.lower() in {
#         "nan",
#         "none",
#         "null",
#         "nat",
#     }:
#         return ""

#     return value


# # ============================================================
# # TEXT NORMALIZATION
# # ============================================================


# def remove_accents(text):
#     text = clean_value(text)

#     text = unicodedata.normalize(
#         "NFD",
#         text,
#     )

#     text = "".join(char for char in text if unicodedata.category(char) != "Mn")

#     return text


# def normalize_text(text):
#     """
#     Normalize Vietnamese / address text.

#     Example:

#         "Cầu Bãi Dại, Quy Nhơn Nam, Gia Lai, Vietnam"

#     ->
#         "cau bai dai quy nhon nam gia lai vietnam"
#     """

#     text = clean_value(text)

#     if not text:
#         return ""

#     text = text.replace("Đ", "D")
#     text = text.replace("đ", "d")

#     text = remove_accents(text)

#     text = text.lower()

#     text = re.sub(
#         r"[^a-z0-9]+",
#         " ",
#         text,
#     )

#     text = re.sub(
#         r"\s+",
#         " ",
#         text,
#     )

#     return text.strip()


# # ============================================================
# # ADDRESS TOKENIZATION
# # ============================================================


# def address_tokens(address):
#     normalized = normalize_text(address)

#     if not normalized:
#         return []

#     tokens = normalized.split()

#     result = []

#     for token in tokens:
#         if not token:
#             continue

#         result.append(token)

#     return result


# def meaningful_address_tokens(address):
#     """
#     Remove generic words such as:
#         street
#         road
#         ward
#         district
#         province
#         vietnam

#     These words are not useful for identifying exact address.
#     """

#     normalized = normalize_text(address)

#     if not normalized:
#         return []

#     tokens = normalized.split()

#     result = []

#     for token in tokens:
#         if token in GENERIC_ADDRESS_TOKENS:
#             continue

#         result.append(token)

#     return result


# # ============================================================
# # INVALID ADDRESS DETECTION
# # ============================================================


# def is_invalid_address_candidate(text):
#     text = clean_value(text)

#     if not text:
#         return True

#     normalized = normalize_text(text)

#     if not normalized:
#         return True

#     if normalized in INVALID_ADDRESS_TEXTS:
#         return True

#     # Google Maps UI-only text must never be treated as an address.
#     ui_phrases = (
#         "thong tin ve du lieu",
#         "about this data",
#         "duoc tai tro",
#         "sponsored",
#     )

#     if any(phrase in normalized for phrase in ui_phrases):
#         return True

#     if normalized.startswith("address "):
#         # "Address: ..." được xử lý ở clean_address_candidate.
#         return False

#     if normalized.startswith("dia chi "):
#         return False

#     # Các text quá ngắn thường không phải address
#     if len(normalized) < 4:
#         return True

#     return False


# # ============================================================
# # CLEAN ADDRESS CANDIDATE
# # ============================================================


# def clean_address_candidate(text):
#     """
#     Làm sạch candidate lấy từ DOM.

#     Có thể nhận:
#         Address: 123 ABC...
#         Địa chỉ: 123 ABC...
#         123 ABC...
#     """

#     text = clean_value(text)

#     if not text:
#         return ""

#     text = text.replace("\r\n", "\n")
#     text = text.replace("\r", "\n")

#     # Chuyển nhiều whitespace thành space
#     text = re.sub(
#         r"[ \t]+",
#         " ",
#         text,
#     )

#     # Xử lý prefix
#     patterns = [
#         r"^\s*địa\s*chỉ\s*:\s*",
#         r"^\s*dia\s*chi\s*:\s*",
#         r"^\s*address\s*:\s*",
#         r"^\s*address\s*-\s*",
#         r"^\s*địa\s*chỉ\s*-\s*",
#         r"^\s*dia\s*chi\s*-\s*",
#     ]

#     for pattern in patterns:
#         text = re.sub(
#             pattern,
#             "",
#             text,
#             flags=re.IGNORECASE,
#         )

#     # Nếu aria-label dạng:
#     #
#     # "Địa chỉ: Cầu Bãi Dại..."
#     #
#     # thì chỉ giữ phần address.
#     text = text.strip()

#     if is_invalid_address_candidate(text):
#         return ""

#     # Loại các text UI thuần túy
#     normalized = normalize_text(text)

#     if normalized in INVALID_ADDRESS_TEXTS:
#         return ""

#     return text


# # ============================================================
# # GOOGLE MAPS URL
# # ============================================================


# def is_google_maps_url(url):
#     url = clean_value(url)

#     if not url:
#         return False

#     value = url.lower()

#     return "google.com/maps" in value or "maps.google.com" in value


# def is_google_maps_place_url(url):
#     """
#     Chỉ chấp nhận Google Maps Place URL.

#     /maps/search/...
#         -> KHÔNG phải Place URL

#     /maps/place/...
#         -> Place URL
#     """

#     url = clean_value(url)

#     if not is_google_maps_url(url):
#         return False

#     value = url.lower()

#     if "/maps/search" in value:
#         return False

#     if "/maps/place/" in value:
#         return True

#     if "/maps/place?" in value:
#         return True

#     return False


# # ============================================================
# # URL COORDINATES
# # ============================================================


# def _valid_coordinate(value, minimum, maximum):
#     try:
#         number = float(value)
#     except Exception:
#         return False

#     if not math.isfinite(number):
#         return False

#     return minimum <= number <= maximum


# def extract_coordinates_from_url(url):
#     """
#     Hỗ trợ:

#         !3d13.7528117!4d109.2142909

#     hoặc:

#         @13.7528117,109.2142909
#     """

#     url = clean_value(url)

#     if not url:
#         return None, None

#     decoded = unquote(url)

#     # --------------------------------------------------------
#     # !3dLAT!4dLNG
#     # --------------------------------------------------------

#     match = re.search(
#         r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
#         decoded,
#         flags=re.IGNORECASE,
#     )

#     if match:
#         lat = float(match.group(1))
#         lng = float(match.group(2))

#         if _valid_coordinate(lat, -90, 90) and _valid_coordinate(lng, -180, 180):
#             return lat, lng

#     # --------------------------------------------------------
#     # @LAT,LNG
#     # --------------------------------------------------------

#     match = re.search(
#         r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
#         decoded,
#         flags=re.IGNORECASE,
#     )

#     if match:
#         lat = float(match.group(1))
#         lng = float(match.group(2))

#         if _valid_coordinate(lat, -90, 90) and _valid_coordinate(lng, -180, 180):
#             return lat, lng

#     return None, None


# def coordinates_to_string(lat, lng):
#     if lat is None or lng is None:
#         return ""

#     return f"{lat:.7f}, {lng:.7f}"


# # ============================================================
# # PAGE COORDINATES
# # ============================================================


# def get_page_coordinates(page):
#     """
#     Coordinates chỉ để DEBUG.

#     KHÔNG dùng để quyết định MATCH/MISMATCH.
#     """

#     try:
#         current_url = page.url

#         lat, lng = extract_coordinates_from_url(current_url)

#         if lat is not None and lng is not None:
#             return coordinates_to_string(
#                 lat,
#                 lng,
#             )

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # BODY TEXT
# # ============================================================


# def get_main_text(page):
#     """
#     Ưu tiên [role=main].
#     Google Maps thường render place details bên trong main.
#     """

#     selectors = [
#         '[role="main"]',
#         "main",
#         "body",
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = locator.count()

#             if count <= 0:
#                 continue

#             text = locator.first.inner_text(timeout=1500)

#             text = clean_value(text)

#             if text:
#                 return text

#         except Exception:
#             continue

#     return ""


# def get_text_lines(page):
#     text = get_main_text(page)

#     if not text:
#         return []

#     lines = []

#     for raw_line in text.splitlines():
#         line = clean_value(raw_line)

#         if not line:
#             continue

#         line = re.sub(
#             r"\s+",
#             " ",
#             line,
#         ).strip()

#         if not line:
#             continue

#         if len(line) < 3:
#             continue

#         lines.append(line)

#         if len(lines) >= MAX_TEXT_LINES:
#             break

#     return lines


# # ============================================================
# # ADDRESS-LIKE LINE
# # ============================================================


# def looks_like_address(text):
#     """
#     Detect address-like text.

#     Không cần address phải giống Excel.
#     """

#     text = clean_address_candidate(text)

#     if not text:
#         return False

#     normalized = normalize_text(text)

#     if not normalized:
#         return False

#     if normalized in {
#         "address",
#         "dia chi",
#         "copy address",
#         "sao chep dia chi",
#         "directions",
#         "chi duong",
#     }:
#         return False

#     tokens = normalized.split()

#     if len(tokens) < 2:
#         return False

#     # Có số nhà / số đường
#     if re.search(r"\d", text):
#         if len(tokens) >= 2:
#             return True

#     # Có comma
#     if "," in text and len(tokens) >= 3:
#         return True

#     # Location markers
#     address_markers = {
#         "street",
#         "road",
#         "avenue",
#         "boulevard",
#         "highway",
#         "ward",
#         "district",
#         "province",
#         "city",
#         "phuong",
#         "quan",
#         "huyen",
#         "tinh",
#         "xa",
#         "thanh",
#         "pho",
#         "tp",
#         "thi",
#         "tran",
#         "vietnam",
#         "viet",
#         "nam",
#     }

#     marker_count = sum(1 for token in tokens if token in address_markers)

#     if marker_count >= 1:
#         return True

#     # Address dài
#     if len(tokens) >= 5:
#         return True

#     return False


# # ============================================================
# # TITLE EXTRACTION / SIMILARITY
# # ============================================================


# def normalize_title(text):
#     text = normalize_text(text)
#     if not text:
#         return ""

#     # Các từ loại hình không nên quyết định identity quá mạnh.
#     generic = {
#         "hotel",
#         "motel",
#         "homestay",
#         "hostel",
#         "resort",
#         "villa",
#         "villas",
#         "apartment",
#         "apartments",
#         "lodge",
#         "guesthouse",
#         "guest",
#         "house",
#         "khach",
#         "san",
#         "nha",
#         "nghi",
#     }
#     tokens = [x for x in text.split() if x not in generic]
#     return " ".join(tokens) or text


# def title_similarity(expected_title, actual_title):
#     from difflib import SequenceMatcher

#     expected = normalize_title(expected_title)
#     actual = normalize_title(actual_title)

#     if not expected or not actual:
#         return 0.0
#     if expected == actual:
#         return 1.0
#     if expected in actual or actual in expected:
#         return 0.92

#     seq = SequenceMatcher(None, expected, actual).ratio()
#     a = set(expected.split())
#     b = set(actual.split())
#     token = len(a & b) / max(len(a | b), 1)
#     return max(seq, token)


# def extract_maps_title(page):
#     """Lấy tên place đang mở, không lấy Sponsored/UI text."""
#     selectors = [
#         "h1.DUwDvf",
#         '[role="main"] h1',
#         "h1",
#     ]

#     for selector in selectors:
#         try:
#             loc = page.locator(selector)
#             for i in range(min(loc.count(), 5)):
#                 text = clean_value(loc.nth(i).inner_text(timeout=800))
#                 normalized = normalize_text(text)
#                 if not text or not normalized:
#                     continue
#                 if normalized in {"duoc tai tro", "sponsored", "google maps"}:
#                     continue
#                 return text
#         except Exception:
#             continue

#     # fallback từ /maps/place/<name>/
#     try:
#         url = unquote(clean_value(page.url))
#         m = re.search(r"/maps/place/([^/@?]+)", url, flags=re.IGNORECASE)
#         if m:
#             return clean_value(m.group(1).replace("+", " "))
#     except Exception:
#         pass

#     return ""


# def first_existing_value(row, columns):
#     for column in columns:
#         if column in row.index:
#             value = clean_value(row.get(column, ""))
#             if value:
#                 return value
#     return ""


# def should_repair_row(row):
#     status = first_existing_value(row, SOURCE_STATUS_COLUMNS).upper()
#     reason = first_existing_value(row, SOURCE_REASON_COLUMNS).upper()
#     check_status = clean_value(row.get(CHECK_STATUS_COLUMN, "")).upper()
#     check_reason = clean_value(row.get(CHECK_REASON_COLUMN, "")).upper()

#     if status == "MISSING" or status == "NOT_VERIFIED":
#         return True
#     if reason in REPAIR_DETAIL_REASONS:
#         return True
#     if check_status in {"NEED_REVIEW", "MISMATCH", "OPEN_FAILED"}:
#         return True
#     if check_reason in REPAIR_DETAIL_REASONS:
#         return True
#     return False


# # ============================================================
# # ADDRESS SIMILARITY
# # ============================================================


# def address_similarity(
#     expected_address,
#     actual_address,
# ):
#     """
#     So sánh Excel Address với Maps Address.

#     Trả score 0.0 -> 1.0.
#     """

#     expected = normalize_text(expected_address)

#     actual = normalize_text(actual_address)

#     if not expected or not actual:
#         return 0.0

#     # Exact
#     if expected == actual:
#         return 1.0

#     # Maps address chứa nguyên Excel address
#     if expected in actual:
#         return 0.95

#     # Excel address chứa nguyên Maps address
#     if actual in expected:
#         return 0.90

#     expected_tokens = set(meaningful_address_tokens(expected_address))

#     actual_tokens = set(meaningful_address_tokens(actual_address))

#     if not expected_tokens or not actual_tokens:
#         return 0.0

#     intersection = expected_tokens & actual_tokens

#     token_score = len(intersection) / max(
#         len(expected_tokens),
#         1,
#     )

#     # --------------------------------------------------------
#     # Location tail
#     #
#     # Lấy 3 token cuối của mỗi address.
#     # Ví dụ:
#     #
#     # gia lai
#     # quy nhon
#     # vietnam
#     # --------------------------------------------------------

#     expected_location = set(expected.split()[-3:])

#     actual_location = set(actual.split()[-3:])

#     if expected_location and actual_location:
#         location_intersection = expected_location & actual_location

#         location_score = len(location_intersection) / max(
#             len(expected_location),
#             1,
#         )
#     else:
#         location_score = 0.0

#     score = token_score * 0.75 + location_score * 0.25

#     return min(
#         max(score, 0.0),
#         1.0,
#     )


# # ============================================================
# # ADDRESS DECISION
# # ============================================================


# def decide_match(
#     expected_address,
#     actual_address,
# ):
#     """
#     Final decision.

#     ONLY:
#         Excel Address
#         vs
#         Maps Address

#     Title không tham gia.
#     Coordinates không tham gia.
#     """

#     expected_address = clean_value(expected_address)

#     actual_address = clean_value(actual_address)

#     if not expected_address:
#         return {
#             "status": "NEED_REVIEW",
#             "match": False,
#             "reason": "MISSING_EXCEL_ADDRESS",
#             "score": 0.0,
#         }

#     if not actual_address:
#         return {
#             "status": "NEED_REVIEW",
#             "match": False,
#             "reason": "MISSING_MAPS_ADDRESS",
#             "score": 0.0,
#         }

#     score = address_similarity(
#         expected_address,
#         actual_address,
#     )

#     if score >= ADDRESS_MATCH_THRESHOLD:
#         return {
#             "status": "MATCH",
#             "match": True,
#             "reason": "ADDRESS_MATCH",
#             "score": score,
#         }

#     return {
#         "status": "MISMATCH",
#         "match": False,
#         "reason": "ADDRESS_MISMATCH",
#         "score": score,
#     }


# # ============================================================
# # DOM ADDRESS EXTRACTION
# # ============================================================

# ADDRESS_SELECTORS = [
#     # Google Maps standard
#     '[data-item-id="address"]',
#     '[data-item-id*="address"]',
#     # aria
#     '[aria-label*="Address"]',
#     '[aria-label*="address"]',
#     '[aria-label*="Địa chỉ"]',
#     '[aria-label*="địa chỉ"]',
#     # tooltip
#     '[data-tooltip*="Address"]',
#     '[data-tooltip*="address"]',
#     '[data-tooltip*="Địa chỉ"]',
#     '[data-tooltip*="địa chỉ"]',
#     # Main area
#     '[role="main"] [data-item-id="address"]',
#     '[role="main"] [data-item-id*="address"]',
#     '[role="main"] [aria-label*="Address"]',
#     '[role="main"] [aria-label*="address"]',
#     '[role="main"] [aria-label*="Địa chỉ"]',
#     '[role="main"] [aria-label*="địa chỉ"]',
#     # Buttons
#     '[role="main"] button[aria-label*="Address"]',
#     '[role="main"] button[aria-label*="address"]',
#     '[role="main"] button[aria-label*="Địa chỉ"]',
#     '[role="main"] button[aria-label*="địa chỉ"]',
#     # Links
#     '[role="main"] a[aria-label*="Address"]',
#     '[role="main"] a[aria-label*="address"]',
#     '[role="main"] a[aria-label*="Địa chỉ"]',
#     '[role="main"] a[aria-label*="địa chỉ"]',
# ]


# def extract_dom_address_candidates(page):
#     """
#     Extract Google Maps Address bằng nhiều tầng.

#     Ưu tiên:
#         1. data-item-id
#         2. aria-label
#         3. data-tooltip
#         4. data-value
#         5. title
#         6. innerText
#         7. href
#     """

#     candidates = []

#     # ========================================================
#     # SELECTORS
#     # ========================================================

#     selectors = [
#         # ----------------------------------------------------
#         # Standard Google Maps
#         # ----------------------------------------------------
#         '[data-item-id="address"]',
#         '[data-item-id*="address"]',
#         # ----------------------------------------------------
#         # aria
#         # ----------------------------------------------------
#         '[aria-label*="Address"]',
#         '[aria-label*="address"]',
#         '[aria-label*="Địa chỉ"]',
#         '[aria-label*="địa chỉ"]',
#         '[aria-label*="Dia chi"]',
#         '[aria-label*="dia chi"]',
#         # ----------------------------------------------------
#         # tooltip
#         # ----------------------------------------------------
#         '[data-tooltip*="Address"]',
#         '[data-tooltip*="address"]',
#         '[data-tooltip*="Địa chỉ"]',
#         '[data-tooltip*="địa chỉ"]',
#         # ----------------------------------------------------
#         # Main
#         # ----------------------------------------------------
#         '[role="main"] [data-item-id="address"]',
#         '[role="main"] [data-item-id*="address"]',
#         '[role="main"] [aria-label*="Address"]',
#         '[role="main"] [aria-label*="address"]',
#         '[role="main"] [aria-label*="Địa chỉ"]',
#         '[role="main"] [aria-label*="địa chỉ"]',
#         # ----------------------------------------------------
#         # Button
#         # ----------------------------------------------------
#         'button[data-item-id="address"]',
#         'button[data-item-id*="address"]',
#         '[role="button"][data-item-id="address"]',
#         '[role="button"][data-item-id*="address"]',
#         # ----------------------------------------------------
#         # Link
#         # ----------------------------------------------------
#         'a[data-item-id="address"]',
#         'a[data-item-id*="address"]',
#         # ----------------------------------------------------
#         # Direction links
#         # ----------------------------------------------------
#         'a[href*="/maps/dir/"]',
#         'a[href*="google.com/maps/dir"]',
#     ]

#     # ========================================================
#     # PLAYWRIGHT
#     # ========================================================

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)
#             count = locator.count()

#             if count <= 0:
#                 continue

#             count = min(count, 30)

#             for index in range(count):
#                 node = locator.nth(index)

#                 # ------------------------------------------------
#                 # innerText
#                 # ------------------------------------------------
#                 try:
#                     value = node.inner_text(timeout=500)
#                     if value:
#                         candidates.append(value)
#                 except Exception:
#                     pass

#                 # ------------------------------------------------
#                 # textContent
#                 # ------------------------------------------------
#                 try:
#                     value = node.text_content(timeout=500)
#                     if value:
#                         candidates.append(value)
#                 except Exception:
#                     pass

#                 # ------------------------------------------------
#                 # attributes
#                 # ------------------------------------------------
#                 for attribute in (
#                     "aria-label",
#                     "data-tooltip",
#                     "data-value",
#                     "title",
#                 ):
#                     try:
#                         value = node.get_attribute(attribute)
#                         if value:
#                             candidates.append(value)
#                     except Exception:
#                         pass

#                 # ------------------------------------------------
#                 # href
#                 # ------------------------------------------------
#                 try:
#                     href = node.get_attribute("href")

#                     if href:
#                         decoded = unquote(href)

#                         # Query parameter q= hoặc query=
#                         match = re.search(
#                             r"[?&](?:q|query)=([^&]*)",
#                             decoded,
#                             flags=re.IGNORECASE,
#                         )

#                         if match:
#                             candidates.append(match.group(1))

#                 except Exception:
#                     pass

#         except Exception:
#             continue

#     # ========================================================
#     # JAVASCRIPT DEEP SCAN
#     # ========================================================

#     try:
#         values = page.evaluate(
#             """
#             () => {
#                 const result = [];

#                 const selectors = [
#                     '[data-item-id="address"]',
#                     '[data-item-id*="address"]',
#                     '[aria-label*="Address"]',
#                     '[aria-label*="address"]',
#                     '[aria-label*="Địa chỉ"]',
#                     '[aria-label*="địa chỉ"]',
#                     '[data-tooltip*="Address"]',
#                     '[data-tooltip*="address"]',
#                     '[data-tooltip*="Địa chỉ"]',
#                     '[data-tooltip*="địa chỉ"]'
#                 ];

#                 const nodes = document.querySelectorAll(
#                     selectors.join(',')
#                 );

#                 for (const node of nodes) {

#                     const attrs = [
#                         node.innerText,
#                         node.textContent,
#                         node.getAttribute('aria-label'),
#                         node.getAttribute('data-tooltip'),
#                         node.getAttribute('data-value'),
#                         node.getAttribute('title')
#                     ];

#                     for (const value of attrs) {

#                         if (!value) {
#                             continue;
#                         }

#                         const text = value
#                             .replace(/\\s+/g, ' ')
#                             .trim();

#                         if (text) {
#                             result.push(text);
#                         }
#                     }

#                     // Parent text
#                     if (node.parentElement) {

#                         const parentText =
#                             node.parentElement.innerText;

#                         if (parentText) {
#                             result.push(
#                                 parentText
#                                     .replace(/\\s+/g, ' ')
#                                     .trim()
#                             );
#                         }
#                     }
#                 }

#                 return result;
#             }
#             """
#         )

#         if isinstance(values, list):
#             candidates.extend(values)

#     except Exception as exc:
#         logger.debug(
#             "Deep JS address scan failed: %s",
#             exc,
#         )

#     return unique_address_candidates(candidates)


# # ============================================================
# # UNIQUE ADDRESS CANDIDATES
# # ============================================================


# def unique_address_candidates(candidates):
#     result = []

#     seen = set()

#     for candidate in candidates:
#         candidate = clean_address_candidate(candidate)

#         if not candidate:
#             continue

#         normalized = normalize_text(candidate)

#         if not normalized:
#             continue

#         if normalized in seen:
#             continue

#         seen.add(normalized)

#         result.append(candidate)

#         if len(result) >= MAX_ADDRESS_CANDIDATES:
#             break

#     return result


# # ============================================================
# # BODY / MAIN ADDRESS EXTRACTION
# # ============================================================


# def extract_address_from_main_text(
#     page,
#     expected_address,
# ):
#     """
#     Scan [role=main] text.

#     Google Maps đôi khi render address thành nhiều dòng:

#         Cầu Bãi Dại
#         Quy Nhơn Nam
#         Gia Lai
#         Vietnam

#     Vì vậy thử ghép:
#         1 dòng
#         2 dòng
#         3 dòng
#         4 dòng
#     """

#     lines = get_text_lines(page)

#     if not lines:
#         return ""

#     candidates = []

#     # --------------------------------------------------------
#     # Single lines
#     # --------------------------------------------------------

#     for line in lines:
#         if looks_like_address(line):
#             candidates.append(line)

#     # --------------------------------------------------------
#     # Consecutive lines
#     # --------------------------------------------------------

#     max_join = 4

#     for i in range(len(lines)):
#         for size in range(
#             2,
#             max_join + 1,
#         ):
#             end = i + size

#             if end > len(lines):
#                 break

#             chunk = lines[i:end]

#             joined = ", ".join(chunk)

#             if looks_like_address(joined):
#                 candidates.append(joined)

#     candidates = unique_address_candidates(candidates)

#     if not candidates:
#         return ""

#     # --------------------------------------------------------
#     # Rank against expected address
#     # --------------------------------------------------------

#     best_candidate = ""
#     best_score = -1.0

#     for candidate in candidates:
#         score = address_similarity(
#             expected_address,
#             candidate,
#         )

#         if score > best_score:
#             best_score = score
#             best_candidate = candidate

#     # Chỉ trả về candidate có ý nghĩa
#     if best_candidate:
#         return best_candidate

#     return ""


# # ============================================================
# # EXTRACT MAPS ADDRESS
# # ============================================================


# def extract_maps_address(
#     page,
#     expected_address,
# ):
#     """
#     Extract địa chỉ thực tế từ Google Maps.

#     QUAN TRỌNG:
#     - Không dùng Title.
#     - Không dùng coordinates.
#     - Không yêu cầu address phải giống Excel mới trả về.
#     - Nếu lấy được một candidate có vẻ là address -> trả về.
#     - Sau đó decide_match() mới quyết định MATCH/MISMATCH.
#     """

#     all_candidates = []

#     # ========================================================
#     # 1. DOM SELECTORS
#     # ========================================================

#     try:
#         dom_candidates = extract_dom_address_candidates(page)

#         if dom_candidates:
#             all_candidates.extend(dom_candidates)

#     except Exception as exc:
#         logger.debug(
#             "DOM address extraction failed: %s",
#             exc,
#         )

#     # ========================================================
#     # 2. MAIN TEXT
#     # ========================================================

#     try:
#         lines = get_text_lines(page)

#         if lines:
#             # ------------------------------------------------
#             # Single lines
#             # ------------------------------------------------

#             for line in lines:
#                 cleaned = clean_address_candidate(line)

#                 if not cleaned:
#                     continue

#                 if looks_like_address(cleaned):
#                     all_candidates.append(cleaned)

#             # ------------------------------------------------
#             # 2-4 consecutive lines
#             # ------------------------------------------------

#             for i in range(len(lines)):
#                 for size in range(2, 5):
#                     end = i + size

#                     if end > len(lines):
#                         break

#                     joined = ", ".join(lines[i:end])

#                     cleaned = clean_address_candidate(joined)

#                     if not cleaned:
#                         continue

#                     if looks_like_address(cleaned):
#                         all_candidates.append(cleaned)

#     except Exception as exc:
#         logger.debug(
#             "Main text address extraction failed: %s",
#             exc,
#         )

#     # ========================================================
#     # 3. EXTRA FALLBACK:
#     #    SEARCH ALL ELEMENTS WITH ADDRESS-LIKE ATTRIBUTES
#     # ========================================================

#     try:
#         fallback_values = page.evaluate(
#             """
#             () => {
#                 const result = [];

#                 const nodes = document.querySelectorAll(
#                     'button, a, div, span'
#                 );

#                 for (const node of nodes) {
#                     const attrs = [
#                         node.getAttribute('aria-label'),
#                         node.getAttribute('data-item-id'),
#                         node.getAttribute('data-tooltip'),
#                         node.getAttribute('data-value'),
#                         node.getAttribute('title')
#                     ];

#                     for (const value of attrs) {
#                         if (!value) {
#                             continue;
#                         }

#                         const text = value.trim();

#                         if (!text) {
#                             continue;
#                         }

#                         const lower = text.toLowerCase();

#                         if (
#                             lower.includes('address') ||
#                             lower.includes('địa chỉ') ||
#                             lower.includes('dia chi')
#                         ) {
#                             result.push(text);
#                         }
#                     }
#                 }

#                 return result;
#             }
#             """
#         )

#         if isinstance(fallback_values, list):
#             all_candidates.extend(fallback_values)

#     except Exception as exc:
#         logger.debug(
#             "Fallback DOM scan failed: %s",
#             exc,
#         )

#     # ========================================================
#     # 4. UNIQUE
#     # ========================================================

#     all_candidates = unique_address_candidates(all_candidates)

#     if not all_candidates:
#         return ""

#     # ========================================================
#     # 5. RANK
#     # ========================================================

#     best_address = ""
#     best_score = -1.0

#     for candidate in all_candidates:
#         score = address_similarity(
#             expected_address,
#             candidate,
#         )

#         logger.debug(
#             "Maps Address candidate | score=%.3f | %s",
#             score,
#             candidate,
#         )

#         if score > best_score:
#             best_score = score
#             best_address = candidate

#     # ========================================================
#     # 6. IMPORTANT
#     #
#     # ĐỪNG:
#     #
#     #     if best_score < 0.25:
#     #         return ""
#     #
#     # Vì nếu Maps Address khác Excel thì phải trả
#     # Address đó để decide_match() kết luận MISMATCH.
#     # ========================================================

#     if best_address:
#         logger.info(
#             "Maps Address candidate selected | score=%.3f | %s",
#             best_score,
#             best_address,
#         )

#         return best_address

#     return ""


# # ============================================================
# # POPUP HANDLING
# # ============================================================

# POPUP_BUTTON_TEXTS = [
#     "Accept all",
#     "I agree",
#     "Accept",
#     "Agree",
#     "Đồng ý",
#     "Chấp nhận tất cả",
#     "Chấp nhận",
#     "Tôi đồng ý",
# ]


# def close_google_popups(page):
#     """
#     Đóng cookie / consent popup nếu xuất hiện.
#     """

#     for text in POPUP_BUTTON_TEXTS:
#         selectors = [
#             f'button:has-text("{text}")',
#             f'[role="button"]:has-text("{text}")',
#         ]

#         for selector in selectors:
#             try:
#                 locator = page.locator(selector)

#                 count = locator.count()

#                 if count <= 0:
#                     continue

#                 for index in range(min(count, 3)):
#                     try:
#                         button = locator.nth(index)

#                         if button.is_visible(timeout=300):
#                             button.click(timeout=1000)

#                             time.sleep(0.3)

#                             logger.debug(
#                                 "Closed popup: %s",
#                                 text,
#                             )

#                             return True

#                     except Exception:
#                         continue

#             except Exception:
#                 continue

#     return False


# # ============================================================
# # AW SNAP DETECTION
# # ============================================================


# def is_aw_snap(page):
#     """
#     Detect Chrome:
#         Aw, Snap!
#         Aww, Snap!
#     """

#     try:
#         title = clean_value(page.title()).lower()

#         if "aw, snap" in title:
#             return True

#         if "aww, snap" in title:
#             return True

#     except Exception:
#         pass

#     try:
#         body_text = page.locator("body").inner_text(timeout=1000).lower()

#         patterns = [
#             "aw, snap",
#             "aww, snap",
#             "aw snap",
#             "aww snap",
#             "this page isn't working",
#             "page isn't working",
#         ]

#         for pattern in patterns:
#             if pattern in body_text:
#                 return True

#     except Exception:
#         pass

#     return False


# # ============================================================
# # PAGE RECOVERY
# # ============================================================


# def recover_page(page):
#     """
#     Recovery strategy:

#         1. reload current page
#         2. nếu vẫn Aw Snap:
#              create new page
#         3. close old page
#         4. return new page
#     """

#     context = page.context

#     # --------------------------------------------------------
#     # Try reload
#     # --------------------------------------------------------

#     try:
#         logger.warning("Aw, Snap detected. Reloading page...")

#         page.reload(
#             wait_until="domcontentloaded",
#             timeout=NAVIGATION_TIMEOUT,
#         )

#         time.sleep(ADDRESS_RELOAD_WAIT)

#         if not is_aw_snap(page):
#             logger.info("Aw, Snap recovered by reload.")

#             return page

#     except Exception as exc:
#         logger.warning(
#             "Reload recovery failed: %s",
#             exc,
#         )

#     # --------------------------------------------------------
#     # Create new page
#     # --------------------------------------------------------

#     logger.warning("Creating a new Playwright page...")

#     try:
#         new_page = context.new_page()

#         new_page.set_default_timeout(5000)

#         new_page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

#         try:
#             page.close(run_before_unload=False)
#         except Exception:
#             pass

#         return new_page

#     except Exception as exc:
#         logger.error(
#             "Could not create recovery page: %s",
#             exc,
#         )

#         return page


# # ============================================================
# # OPEN GOOGLE MAPS URL
# # ============================================================


# def open_maps_url(
#     page,
#     url,
# ):
#     """
#     Open Place URL robustly.

#     Return:
#         page,
#         success,
#         reason
#     """

#     url = clean_value(url)

#     if not is_google_maps_place_url(url):
#         return (
#             page,
#             False,
#             "INVALID_URL",
#         )

#     current_page = page

#     for attempt in range(
#         1,
#         RETRY_COUNT + 1,
#     ):
#         logger.info(
#             "Opening Maps URL (attempt %s/%s): %s",
#             attempt,
#             RETRY_COUNT,
#             url,
#         )

#         # ----------------------------------------------------
#         # Aw Snap trước khi goto
#         # ----------------------------------------------------

#         try:
#             if is_aw_snap(current_page):
#                 current_page = recover_page(current_page)

#         except Exception:
#             pass

#         # ----------------------------------------------------
#         # Goto
#         # ----------------------------------------------------

#         try:
#             current_page.goto(
#                 url,
#                 wait_until="domcontentloaded",
#                 timeout=NAVIGATION_TIMEOUT,
#             )

#         except PlaywrightTimeoutError:
#             logger.warning(
#                 "Maps navigation timeout (attempt %s/%s)",
#                 attempt,
#                 RETRY_COUNT,
#             )

#         except Exception as exc:
#             logger.warning(
#                 "Maps navigation error (attempt %s/%s): %s",
#                 attempt,
#                 RETRY_COUNT,
#                 exc,
#             )

#         # ----------------------------------------------------
#         # Wait
#         # ----------------------------------------------------

#         time.sleep(WAIT_AFTER_LOAD)

#         close_google_popups(current_page)

#         # ----------------------------------------------------
#         # Aw Snap after navigation
#         # ----------------------------------------------------

#         try:
#             if is_aw_snap(current_page):
#                 logger.warning("Aw, Snap after navigation.")

#                 current_page = recover_page(current_page)

#                 continue

#         except Exception:
#             pass

#         # ----------------------------------------------------
#         # Validate current URL
#         # ----------------------------------------------------

#         try:
#             current_url = clean_value(current_page.url)

#         except Exception:
#             current_url = ""

#         if is_google_maps_place_url(current_url):
#             logger.info("Maps Place URL opened successfully.")

#             return (
#                 current_page,
#                 True,
#                 "OPEN_SUCCESS",
#             )

#         logger.warning(
#             "Current URL is not a Place URL: %s",
#             current_url,
#         )

#         time.sleep(1.0)

#     return (
#         current_page,
#         False,
#         "URL_IS_PLACE_BUT_NAVIGATION_FAILED",
#     )


# # ============================================================
# # RESEARCH GOOGLE MAPS FROM ADDRESS
# # ============================================================


# def research_google_maps_from_address(
#     page,
#     title,
#     address,
#     context=None,
# ):
#     """
#     Luôn search lại bằng Excel Address.

#     search.py yêu cầu title không rỗng,
#     nên nếu title rỗng -> dùng address tạm.
#     """

#     address = clean_value(address)

#     title = clean_value(title)

#     if not address:
#         return (
#             page,
#             "",
#             {
#                 "success": False,
#                 "reason": "MISSING_EXCEL_ADDRESS",
#             },
#         )

#     search_title = title if title else address

#     logger.info(
#         "REAL GOOGLE MAPS RESEARCH | Title=%s | Address=%s",
#         search_title,
#         address,
#     )

#     try:
#         result = search_google_maps(
#             page,
#             search_title,
#             address,
#             context=context,
#             logger=logger,
#             timeout=PAGE_TIMEOUT,
#         )

#     except Exception as exc:
#         logger.exception(
#             "search_google_maps failed: %s",
#             exc,
#         )

#         return (
#             page,
#             "",
#             {
#                 "success": False,
#                 "reason": "SEARCH_EXCEPTION",
#                 "error": str(exc),
#             },
#         )

#     # --------------------------------------------------------
#     # search.py có thể trả page mới
#     # --------------------------------------------------------

#     result_page = None

#     if isinstance(
#         result,
#         dict,
#     ):
#         result_page = result.get("page")

#     if result_page is not None:
#         page = result_page

#         try:
#             page.set_default_timeout(5000)

#             page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

#         except Exception:
#             pass

#     # --------------------------------------------------------
#     # Extract URL
#     # --------------------------------------------------------

#     new_url = ""

#     if isinstance(
#         result,
#         dict,
#     ):
#         new_url = clean_value(
#             result.get("google_maps_url") or result.get("url") or result.get("maps_url")
#         )

#     # --------------------------------------------------------
#     # Nếu search.py không trả URL,
#     # thử current page URL
#     # --------------------------------------------------------

#     if not new_url:
#         try:
#             current_url = clean_value(page.url)

#             if is_google_maps_place_url(current_url):
#                 new_url = current_url

#         except Exception:
#             pass

#     # --------------------------------------------------------
#     # Validate
#     # --------------------------------------------------------

#     if not is_google_maps_place_url(new_url):
#         reason = "SEARCH_DID_NOT_RETURN_PLACE_URL"

#         if isinstance(
#             result,
#             dict,
#         ):
#             reason = clean_value(result.get("reason")) or reason

#         logger.warning(
#             "Research failed: %s",
#             reason,
#         )

#         return (
#             page,
#             "",
#             {
#                 **(
#                     result
#                     if isinstance(
#                         result,
#                         dict,
#                     )
#                     else {}
#                 ),
#                 "success": False,
#                 "reason": reason,
#             },
#         )

#     logger.info(
#         "Fresh Google Maps URL: %s",
#         new_url,
#     )

#     return (
#         page,
#         new_url,
#         {
#             **(
#                 result
#                 if isinstance(
#                     result,
#                     dict,
#                 )
#                 else {}
#             ),
#             "success": True,
#             "reason": "RESEARCH_SUCCESS",
#             "google_maps_url": new_url,
#         },
#     )


# def wait_for_address(
#     page: Page,
#     expected_address: str,
#     timeout: float = ADDRESS_WAIT_TIMEOUT,
#     poll_interval: float = ADDRESS_POLL_INTERVAL,
# ) -> str:
#     """
#     Chờ Google Maps render địa chỉ.

#     Không yêu cầu địa chỉ phải MATCH.
#     Chỉ cần tìm được một candidate hợp lệ thì trả về.
#     Việc MATCH/MISMATCH do decide_match() xử lý.
#     """

#     deadline = time.time() + timeout

#     best_candidate = ""
#     best_score = -1.0

#     while time.time() < deadline:
#         try:
#             candidate = extract_maps_address(
#                 page,
#                 expected_address,
#             )

#             if candidate:
#                 score = address_similarity(
#                     expected_address,
#                     candidate,
#                 )

#                 # Có candidate hợp lệ thì giữ lại.
#                 if score > best_score:
#                     best_candidate = candidate
#                     best_score = score

#                 # MATCH rõ ràng thì trả về ngay.
#                 if score >= ADDRESS_MATCH_THRESHOLD:
#                     return candidate

#         except Exception:
#             pass

#         time.sleep(poll_interval)

#     # Hết thời gian nhưng đã tìm thấy candidate:
#     # chỉ trả về nếu nó thực sự giống một address, không phải UI text.
#     if (
#         best_candidate
#         and not is_invalid_address_candidate(best_candidate)
#         and looks_like_address(best_candidate)
#     ):
#         return best_candidate

#     return ""


# # ============================================================
# # TITLE-FIRST RESEARCH FOR MISSING MAPS URL
# # ============================================================


# def research_google_maps_from_title(
#     page,
#     title,
#     address,
#     context=None,
# ):
#     """
#     Used ONLY when google_maps_url is empty/invalid.

#     Goal:
#         Search by TITLE first to locate the real business.
#         Then return its Place URL so check_one() can open it,
#         extract the REAL Maps title/address, and repair Excel.

#     IMPORTANT:
#         We intentionally do NOT feed the old Excel address into the
#         initial search decision, because the address itself may be wrong.
#     """

#     title = clean_value(title)
#     address = clean_value(address)

#     if not title:
#         return (
#             page,
#             "",
#             {
#                 "success": False,
#                 "reason": "MISSING_EXCEL_TITLE",
#             },
#         )

#     logger.info(
#         "REAL GOOGLE MAPS TITLE RESEARCH | Title=%s | Old Address=%s",
#         title,
#         address,
#     )

#     try:
#         # Empty address => search.py behaves as title-only search.
#         result = search_google_maps(
#             page,
#             title,
#             "",
#             context=context,
#             logger=logger,
#             timeout=PAGE_TIMEOUT,
#         )

#     except Exception as exc:
#         logger.exception(
#             "Title research search_google_maps failed: %s",
#             exc,
#         )

#         return (
#             page,
#             "",
#             {
#                 "success": False,
#                 "reason": "TITLE_SEARCH_EXCEPTION",
#                 "error": str(exc),
#             },
#         )

#     result_page = None

#     if isinstance(result, dict):
#         result_page = result.get("page")

#     if result_page is not None:
#         page = result_page

#         try:
#             page.set_default_timeout(5000)
#             page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)
#         except Exception:
#             pass

#     new_url = ""

#     if isinstance(result, dict):
#         new_url = clean_value(
#             result.get("google_maps_url") or result.get("url") or result.get("maps_url")
#         )

#     # If search.py did not expose the URL but browser is already
#     # sitting on a Place page, keep it ONLY when the visible Maps title
#     # is still plausibly related to the input title. This prevents the
#     # final random candidate from being promoted (e.g. LE PHUONG HOTEL
#     # -> Ben Hotel 839).
#     if not new_url:
#         try:
#             current_url = clean_value(page.url)

#             if is_google_maps_place_url(current_url):
#                 current_title = extract_maps_title(page)
#                 current_title_score = title_similarity(
#                     title,
#                     current_title,
#                 )

#                 if current_title_score >= 0.72:
#                     new_url = current_url

#                     logger.info(
#                         "TITLE RESEARCH CURRENT PLACE KEPT | "
#                         "title=%r | actual_title=%r | score=%.3f",
#                         title,
#                         current_title,
#                         current_title_score,
#                     )
#                 else:
#                     logger.warning(
#                         "TITLE RESEARCH CURRENT PLACE REJECTED | "
#                         "title=%r | actual_title=%r | score=%.3f | url=%r",
#                         title,
#                         current_title,
#                         current_title_score,
#                         current_url,
#                     )

#         except Exception:
#             pass

#     if not is_google_maps_place_url(new_url):
#         reason = "TITLE_SEARCH_DID_NOT_RETURN_PLACE_URL"

#         if isinstance(result, dict):
#             reason = (
#                 clean_value(result.get("detail_reason") or result.get("reason"))
#                 or reason
#             )

#         logger.warning(
#             "TITLE RESEARCH FAILED | title=%r | reason=%s",
#             title,
#             reason,
#         )

#         return (
#             page,
#             "",
#             {
#                 **(result if isinstance(result, dict) else {}),
#                 "success": False,
#                 "reason": reason,
#             },
#         )

#     logger.info(
#         "TITLE RESEARCH FOUND PLACE | %s",
#         new_url,
#     )

#     return (
#         page,
#         new_url,
#         {
#             **(result if isinstance(result, dict) else {}),
#             "success": True,
#             "reason": "TITLE_RESEARCH_SUCCESS",
#             "google_maps_url": new_url,
#         },
#     )


# # ============================================================
# # CHECK ONE RECORD
# # ============================================================


# def check_one(
#     page,
#     title,
#     address,
#     maps_url,
#     context=None,
#     force_research=False,
# ):
#     """
#     Check một record.

#     Return:
#         result,
#         current_page
#     """

#     title = clean_value(title)

#     address = clean_value(address)

#     maps_url = clean_value(maps_url)

#     current_page = page

#     # ========================================================
#     # 1. FORCE RESEARCH
#     # ========================================================

#     if force_research:
#         logger.info("Force research from Excel Address.")

#         (
#             current_page,
#             fresh_url,
#             research_result,
#         ) = research_google_maps_from_title(
#             current_page,
#             title,
#             address,
#             context=context,
#         )

#         if not fresh_url:
#             return (
#                 {
#                     "status": "NEED_REVIEW",
#                     "maps_check_address": "",
#                     "maps_check_url": "",
#                     "maps_address_match": False,
#                     "maps_check_reason": ("RESEARCH_FROM_ADDRESS_FAILED"),
#                     "maps_check_coordinates": "",
#                 },
#                 current_page,
#             )

#         maps_url = fresh_url

#     # ========================================================
#     # 2. NO MAPS URL
#     # ========================================================

#     if not maps_url:
#         logger.info("No Maps URL. Research from TITLE first.")

#         (
#             current_page,
#             fresh_url,
#             research_result,
#         ) = research_google_maps_from_title(
#             current_page,
#             title,
#             address,
#             context=context,
#         )

#         if not fresh_url:
#             return (
#                 {
#                     "status": "NEED_REVIEW",
#                     "maps_check_address": "",
#                     "maps_check_url": "",
#                     "maps_address_match": False,
#                     "maps_check_reason": ("NO_MAPS_URL_AND_RESEARCH_FAILED"),
#                     "maps_check_coordinates": "",
#                 },
#                 current_page,
#             )

#         maps_url = fresh_url

#     # ========================================================
#     # 3. INVALID URL
#     # ========================================================

#     elif not is_google_maps_place_url(maps_url):
#         logger.info("Invalid Maps URL. Research from TITLE first.")

#         (
#             current_page,
#             fresh_url,
#             research_result,
#         ) = research_google_maps_from_title(
#             current_page,
#             title,
#             address,
#             context=context,
#         )

#         if not fresh_url:
#             return (
#                 {
#                     "status": "NEED_REVIEW",
#                     "maps_check_address": "",
#                     "maps_check_url": maps_url,
#                     "maps_address_match": False,
#                     "maps_check_reason": ("INVALID_URL_AND_RESEARCH_FAILED"),
#                     "maps_check_coordinates": "",
#                 },
#                 current_page,
#             )

#         maps_url = fresh_url

#     # ========================================================
#     # 4. OPEN MAPS URL
#     # ========================================================

#     (
#         current_page,
#         opened,
#         open_reason,
#     ) = open_maps_url(
#         current_page,
#         maps_url,
#     )

#     if not opened:
#         return (
#             {
#                 "status": "OPEN_FAILED",
#                 "maps_check_address": "",
#                 "maps_check_url": maps_url,
#                 "maps_address_match": False,
#                 "maps_check_reason": open_reason,
#                 "maps_check_coordinates": (get_page_coordinates(current_page)),
#             },
#             current_page,
#         )

#     # ========================================================
#     # 5. CURRENT URL
#     # ========================================================

#     try:
#         current_url = clean_value(current_page.url)

#     except Exception:
#         current_url = maps_url

#     if not is_google_maps_place_url(current_url):
#         return (
#             {
#                 "status": "NEED_REVIEW",
#                 "maps_check_address": "",
#                 "maps_check_url": current_url,
#                 "maps_address_match": False,
#                 "maps_check_reason": ("MAPS_URL_NOT_PLACE_AFTER_OPEN"),
#                 "maps_check_coordinates": (get_page_coordinates(current_page)),
#             },
#             current_page,
#         )

#     # ========================================================
#     # 6. CLOSE POPUPS
#     # ========================================================

#     close_google_popups(current_page)

#     # ========================================================
#     # 7. GET COORDINATES
#     #
#     # DEBUG ONLY
#     # ========================================================

#     coordinates = get_page_coordinates(current_page)

#     # ========================================================
#     # 8. EXTRACT ADDRESS
#     # ========================================================

#     logger.info("Extracting Maps Address...")

#     actual_address = wait_for_address(
#         current_page,
#         address,
#         timeout=ADDRESS_WAIT_TIMEOUT,
#     )

#     # ========================================================
#     # 9. ADDRESS STILL EMPTY
#     #
#     # Reload once and try again.
#     # ========================================================

#     if not actual_address and ADDRESS_RELOAD_ONCE:
#         logger.warning("Maps Address is empty. Reloading page and trying again...")

#         try:
#             current_page.reload(
#                 wait_until="domcontentloaded",
#                 timeout=NAVIGATION_TIMEOUT,
#             )

#             time.sleep(ADDRESS_RELOAD_WAIT)

#             close_google_popups(current_page)

#             actual_address = wait_for_address(
#                 current_page,
#                 address,
#                 timeout=ADDRESS_WAIT_TIMEOUT,
#             )

#         except Exception as exc:
#             logger.warning(
#                 "Address reload failed: %s",
#                 exc,
#             )

#     # ========================================================
#     # 10. FINAL DECISION
#     # ========================================================

#     decision = decide_match(
#         address,
#         actual_address,
#     )

#     actual_title = extract_maps_title(current_page)
#     title_score = title_similarity(title, actual_title)

#     # ========================================================
#     # 11. IMPROVE REASON WHEN ADDRESS NOT FOUND
#     # ========================================================

#     reason = decision["reason"]

#     if decision["status"] == "NEED_REVIEW" and not actual_address:
#         reason = "MISSING_MAPS_ADDRESS_AFTER_RETRY"

#     logger.info(
#         "RESULT | status=%s | score=%.3f | Excel Address=%s | Maps Address=%s",
#         decision["status"],
#         decision["score"],
#         address,
#         actual_address,
#     )

#     return (
#         {
#             "status": decision["status"],
#             "maps_check_address": (actual_address or ""),
#             "maps_check_url": (current_url or maps_url),
#             "maps_address_match": bool(decision["match"]),
#             "maps_check_reason": reason,
#             "maps_check_coordinates": (coordinates or ""),
#             "address_score": decision["score"],
#             "maps_check_title": actual_title or "",
#             "title_score": title_score,
#         },
#         current_page,
#     )


# # ============================================================
# # OUTPUT COLUMNS
# # ============================================================

# OUTPUT_COLUMNS = [
#     CHECK_STATUS_COLUMN,
#     CHECK_ADDRESS_COLUMN,
#     CHECK_URL_COLUMN,
#     ADDRESS_MATCH_COLUMN,
#     CHECK_REASON_COLUMN,
#     CHECK_COORDINATES_COLUMN,
#     CHECK_TIME_COLUMN,
#     REPAIR_OLD_TITLE_COLUMN,
#     REPAIR_OLD_ADDRESS_COLUMN,
#     REPAIR_NEW_TITLE_COLUMN,
#     REPAIR_NEW_ADDRESS_COLUMN,
#     REPAIR_STATUS_COLUMN,
#     REPAIR_REASON_COLUMN,
# ]


# def ensure_output_columns(df):
#     """
#     Tạo output columns.

#     IMPORTANT:
#     Cast sang object để tránh:

#         LossySetitemError

#     khi pandas column đang float64 mà ghi "".
#     """

#     for column in OUTPUT_COLUMNS:
#         if column not in df.columns:
#             df[column] = ""

#         try:
#             df[column] = df[column].astype("object")
#         except Exception:
#             pass

#     # Address / title cũng nên giữ object
#     for column in (
#         TITLE_COLUMN,
#         ADDRESS_COLUMN,
#         URL_COLUMN,
#         GOOGLE_MAPS_COLUMN,
#     ):
#         if column in df.columns:
#             try:
#                 df[column] = df[column].astype("object")
#             except Exception:
#                 pass

#     return df


# # ============================================================
# # ATOMIC SAVE
# # ============================================================


# def atomic_save_excel(
#     df,
#     output_path,
# ):
#     """
#     Save Excel atomically.

#     Ghi ra .tmp.xlsx trước,
#     sau đó replace output.
#     """

#     output_path = Path(output_path)

#     output_path.parent.mkdir(
#         parents=True,
#         exist_ok=True,
#     )

#     temp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)

#     try:
#         df.to_excel(
#             temp_path,
#             index=False,
#             engine="openpyxl",
#         )

#         os.replace(
#             temp_path,
#             output_path,
#         )

#     except Exception:
#         try:
#             if temp_path.exists():
#                 temp_path.unlink()
#         except Exception:
#             pass

#         raise


# # ============================================================
# # CONTEXT
# # ============================================================


# def get_search_context(page):
#     try:
#         return page.context
#     except Exception:
#         return None


# # ============================================================
# # MULTI-WORKER CONFIG
# # ============================================================

# # Browser UI is forced visible for every worker.
# # Each worker gets its own Chromium window.

# DEFAULT_WORKERS = max(
#     1,
#     int(
#         os.getenv(
#             "CHECK_MAPS_WORKERS",
#             "4",
#         )
#     ),
# )

# ORIGINAL_INDEX_COLUMN = "__check_maps_original_index"


# # ============================================================
# # PROCESS ONE EXCEL FILE
# # ============================================================


# def process_excel_file(
#     input_path,
#     output_path,
#     worker_id=None,
# ):
#     """
#     Process one Excel file.

#     Multi-worker parent splits the original Excel into independent chunks.
#     Each worker owns:
#         - its own Playwright browser
#         - its own DataFrame
#         - its own checkpoint/output file

#     Therefore workers NEVER write to the same Excel file.
#     """

#     input_path = Path(input_path)
#     output_path = Path(output_path)

#     if worker_id is not None:
#         logger.name = f"check_maps.W{worker_id}"

#     logger.info("============================================================")
#     logger.info(
#         "GOOGLE MAPS CHECKER%s",
#         (f" | WORKER {worker_id}" if worker_id is not None else ""),
#     )
#     logger.info(
#         "Input : %s",
#         input_path,
#     )
#     logger.info(
#         "Output: %s",
#         output_path,
#     )
#     logger.info("============================================================")

#     # ========================================================
#     # READ EXCEL
#     # ========================================================

#     if not input_path.exists():
#         raise FileNotFoundError(f"Input Excel not found: {input_path}")

#     df = pd.read_excel(input_path)

#     logger.info(
#         "Loaded %s rows.",
#         len(df),
#     )

#     # ========================================================
#     # REQUIRED COLUMNS
#     # ========================================================

#     required_columns = [
#         TITLE_COLUMN,
#         ADDRESS_COLUMN,
#     ]

#     missing_columns = [
#         column for column in required_columns if column not in df.columns
#     ]

#     if missing_columns:
#         raise ValueError(
#             "Missing required Excel columns: " + ", ".join(missing_columns)
#         )

#     # ========================================================
#     # CREATE GOOGLE MAPS COLUMN
#     # ========================================================

#     if GOOGLE_MAPS_COLUMN not in df.columns:
#         df[GOOGLE_MAPS_COLUMN] = ""

#     df = ensure_output_columns(df)

#     # ========================================================
#     # PLAYWRIGHT
#     # ========================================================

#     from playwright.sync_api import (
#         sync_playwright,
#     )

#     counters = {
#         "MATCH": 0,
#         "MISMATCH": 0,
#         "NEED_REVIEW": 0,
#         "OPEN_FAILED": 0,
#         "SKIPPED": 0,
#         "REPAIRED": 0,
#         "NOT_REPAIRED": 0,
#     }

#     processed = 0

#     with sync_playwright() as p:
#         browser = p.chromium.launch(
#             headless=False,
#         )

#         context = browser.new_context(
#             viewport={
#                 "width": 1440,
#                 "height": 1000,
#             },
#             locale="vi-VN",
#         )

#         context.set_default_timeout(5000)
#         context.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

#         page = context.new_page()
#         page.set_default_timeout(5000)
#         page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

#         # ====================================================
#         # PROCESS ROWS
#         # ====================================================

#         for local_position, (index, row) in enumerate(
#             df.iterrows(),
#             1,
#         ):
#             title = clean_value(
#                 row.get(
#                     TITLE_COLUMN,
#                     "",
#                 )
#             )

#             address = clean_value(
#                 row.get(
#                     ADDRESS_COLUMN,
#                     "",
#                 )
#             )

#             old_maps_url = clean_value(
#                 row.get(
#                     GOOGLE_MAPS_COLUMN,
#                     "",
#                 )
#             )

#             previous_status = clean_value(
#                 row.get(
#                     CHECK_STATUS_COLUMN,
#                     "",
#                 )
#             ).upper()

#             original_position = clean_value(
#                 row.get(
#                     ORIGINAL_INDEX_COLUMN,
#                     "",
#                 )
#             )

#             logger.info("")
#             logger.info("============================================================")

#             logger.info(
#                 "[%s/%s%s] %s",
#                 local_position,
#                 len(df),
#                 (f" | original={original_position}" if original_position else ""),
#                 title,
#             )

#             logger.info(
#                 "Address: %s",
#                 address,
#             )

#             logger.info(
#                 "Old Maps URL: %s",
#                 old_maps_url,
#             )

#             logger.info(
#                 "Previous Status: %s",
#                 previous_status or "(empty)",
#             )

#             # =================================================
#             # SKIP FINAL STATUS
#             # =================================================

#             repair_requested = should_repair_row(row)

#             if previous_status in RESUME_SKIP_STATUSES and not repair_requested:
#                 counters["SKIPPED"] += 1

#                 logger.info(
#                     "SKIP FINAL | %s | %s",
#                     previous_status,
#                     title,
#                 )

#                 continue

#             # =================================================
#             # VALIDATE / CHECK
#             # =================================================

#             if not address:
#                 result = {
#                     "status": "NEED_REVIEW",
#                     "maps_check_address": "",
#                     "maps_check_url": old_maps_url,
#                     "maps_address_match": False,
#                     "maps_check_reason": ("MISSING_EXCEL_ADDRESS"),
#                     "maps_check_coordinates": "",
#                     "address_score": 0.0,
#                     "maps_check_title": "",
#                     "title_score": 0.0,
#                 }

#                 current_page = page

#             else:
#                 force_research = (
#                     previous_status in FORCE_RESEARCH_STATUSES or repair_requested
#                 )

#                 try:
#                     (
#                         result,
#                         current_page,
#                     ) = check_one(
#                         page,
#                         title,
#                         address,
#                         old_maps_url,
#                         context=context,
#                         force_research=force_research,
#                     )

#                 except Exception as exc:
#                     logger.exception(
#                         "Unhandled row error: %s",
#                         exc,
#                     )

#                     result = {
#                         "status": "NEED_REVIEW",
#                         "maps_check_address": "",
#                         "maps_check_url": old_maps_url,
#                         "maps_address_match": False,
#                         "maps_check_reason": ("UNHANDLED_EXCEPTION"),
#                         "maps_check_coordinates": "",
#                         "address_score": 0.0,
#                         "maps_check_title": "",
#                         "title_score": 0.0,
#                     }

#                     current_page = page

#             if current_page is not None:
#                 page = current_page

#             # =================================================
#             # CANDIDATE GOOGLE MAPS URL
#             #
#             # IMPORTANT:
#             # Do NOT write this URL to Excel yet.
#             # A valid /maps/place/ URL can still belong to the WRONG hotel.
#             # It is written only after MATCH / safe REPAIR below.
#             # =================================================

#             result_url = clean_value(
#                 result.get(
#                     "maps_check_url",
#                     "",
#                 )
#             )

#             # =================================================
#             # AUTO REPAIR TITLE + ADDRESS
#             # =================================================

#             actual_title = clean_value(
#                 result.get(
#                     "maps_check_title",
#                     "",
#                 )
#             )

#             actual_address = clean_value(
#                 result.get(
#                     "maps_check_address",
#                     "",
#                 )
#             )

#             title_score = float(
#                 result.get(
#                     "title_score",
#                     0.0,
#                 )
#                 or 0.0
#             )

#             result_status = clean_value(
#                 result.get(
#                     "status",
#                     "",
#                 )
#             ).upper()

#             if repair_requested:
#                 if not clean_value(
#                     df.at[
#                         index,
#                         REPAIR_OLD_TITLE_COLUMN,
#                     ]
#                 ):
#                     df.at[
#                         index,
#                         REPAIR_OLD_TITLE_COLUMN,
#                     ] = title

#                 if not clean_value(
#                     df.at[
#                         index,
#                         REPAIR_OLD_ADDRESS_COLUMN,
#                     ]
#                 ):
#                     df.at[
#                         index,
#                         REPAIR_OLD_ADDRESS_COLUMN,
#                     ] = address

#             repair_ok = False
#             repair_reason = ""

#             if repair_requested:
#                 # -------------------------------------------------
#                 # SAFE REPAIR RULE
#                 #
#                 # A repair is allowed only when:
#                 #   1. a real Maps Place URL exists
#                 #   2. actual title exists
#                 #   3. actual address exists
#                 #   4. identity evidence is strong enough
#                 # -------------------------------------------------

#                 identity_ok = (
#                     title_score >= REPAIR_TITLE_THRESHOLD or result_status == "MATCH"
#                 )

#                 if (
#                     is_google_maps_place_url(result_url)
#                     and actual_title
#                     and actual_address
#                     and identity_ok
#                 ):
#                     df.at[
#                         index,
#                         TITLE_COLUMN,
#                     ] = actual_title

#                     df.at[
#                         index,
#                         ADDRESS_COLUMN,
#                     ] = actual_address

#                     df.at[
#                         index,
#                         GOOGLE_MAPS_COLUMN,
#                     ] = result_url

#                     df.at[
#                         index,
#                         REPAIR_NEW_TITLE_COLUMN,
#                     ] = actual_title

#                     df.at[
#                         index,
#                         REPAIR_NEW_ADDRESS_COLUMN,
#                     ] = actual_address

#                     df.at[
#                         index,
#                         REPAIR_STATUS_COLUMN,
#                     ] = "REPAIRED"

#                     df.at[
#                         index,
#                         REPAIR_REASON_COLUMN,
#                     ] = "TITLE_ADDRESS_MAPS_REPAIRED"

#                     result["status"] = "MATCH"
#                     result["maps_address_match"] = True

#                     result["maps_check_reason"] = "REPAIRED_FROM_GOOGLE_MAPS"

#                     result["address_score"] = 1.0

#                     repair_ok = True
#                     repair_reason = "TITLE_ADDRESS_MAPS_REPAIRED"

#                     counters["REPAIRED"] += 1

#                     logger.info("AUTO REPAIR SUCCESS")

#                     logger.info(
#                         "Old Title   : %s",
#                         title,
#                     )

#                     logger.info(
#                         "New Title   : %s",
#                         actual_title,
#                     )

#                     logger.info(
#                         "Old Address : %s",
#                         address,
#                     )

#                     logger.info(
#                         "New Address : %s",
#                         actual_address,
#                     )

#                     logger.info(
#                         "Title Score : %.3f",
#                         title_score,
#                     )

#                     logger.info(
#                         "Maps URL    : %s",
#                         result_url,
#                     )

#                 else:
#                     if not is_google_maps_place_url(result_url):
#                         repair_reason = "NO_VERIFIED_PLACE_URL"

#                     elif not actual_title:
#                         repair_reason = "MAPS_TITLE_UNAVAILABLE"

#                     elif not actual_address:
#                         repair_reason = "MAPS_ADDRESS_UNAVAILABLE"

#                     elif not identity_ok:
#                         repair_reason = "PLACE_IDENTITY_NOT_STRONG_ENOUGH"

#                     else:
#                         repair_reason = "NOT_SAFE_TO_REPAIR"

#                     df.at[
#                         index,
#                         REPAIR_STATUS_COLUMN,
#                     ] = "NOT_REPAIRED"

#                     df.at[
#                         index,
#                         REPAIR_REASON_COLUMN,
#                     ] = repair_reason

#                     counters["NOT_REPAIRED"] += 1

#                     logger.warning(
#                         "AUTO REPAIR SKIPPED | reason=%s | title_score=%.3f",
#                         repair_reason,
#                         title_score,
#                     )

#             # =================================================
#             # COMMIT VERIFIED GOOGLE MAPS URL
#             # =================================================

#             final_status = clean_value(
#                 result.get(
#                     "status",
#                     "",
#                 )
#             ).upper()

#             if final_status == "MATCH" and is_google_maps_place_url(result_url):
#                 df.at[
#                     index,
#                     GOOGLE_MAPS_COLUMN,
#                 ] = result_url

#             # On MISMATCH / NEED_REVIEW / OPEN_FAILED,
#             # keep google_maps_url EMPTY for rows that started empty.
#             elif not old_maps_url:
#                 df.at[
#                     index,
#                     GOOGLE_MAPS_COLUMN,
#                 ] = ""

#             # =================================================
#             # WRITE CHECK RESULT
#             # =================================================

#             df.at[
#                 index,
#                 CHECK_STATUS_COLUMN,
#             ] = clean_value(
#                 result.get(
#                     "status",
#                     "",
#                 )
#             )

#             df.at[
#                 index,
#                 CHECK_ADDRESS_COLUMN,
#             ] = clean_value(
#                 result.get(
#                     "maps_check_address",
#                     "",
#                 )
#             )

#             df.at[
#                 index,
#                 CHECK_URL_COLUMN,
#             ] = clean_value(
#                 result.get(
#                     "maps_check_url",
#                     "",
#                 )
#             )

#             df.at[
#                 index,
#                 ADDRESS_MATCH_COLUMN,
#             ] = bool(
#                 result.get(
#                     "maps_address_match",
#                     False,
#                 )
#             )

#             df.at[
#                 index,
#                 CHECK_REASON_COLUMN,
#             ] = clean_value(
#                 result.get(
#                     "maps_check_reason",
#                     "",
#                 )
#             )

#             df.at[
#                 index,
#                 CHECK_COORDINATES_COLUMN,
#             ] = clean_value(
#                 result.get(
#                     "maps_check_coordinates",
#                     "",
#                 )
#             )

#             df.at[
#                 index,
#                 CHECK_TIME_COLUMN,
#             ] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#             status = clean_value(
#                 result.get(
#                     "status",
#                     "",
#                 )
#             ).upper()

#             if status in counters:
#                 counters[status] += 1

#             processed += 1

#             logger.info(
#                 "RESULT: %s",
#                 status,
#             )

#             if repair_requested:
#                 logger.info(
#                     "REPAIR: %s | reason=%s",
#                     ("REPAIRED" if repair_ok else "NOT_REPAIRED"),
#                     repair_reason,
#                 )

#             logger.info(
#                 "Maps Title: %s",
#                 actual_title or "(NULL/EMPTY)",
#             )

#             logger.info(
#                 "Maps Address: %s",
#                 clean_value(
#                     result.get(
#                         "maps_check_address",
#                         "",
#                     )
#                 )
#                 or "(NULL/EMPTY)",
#             )

#             logger.info(
#                 "Reason: %s",
#                 clean_value(
#                     result.get(
#                         "maps_check_reason",
#                         "",
#                     )
#                 ),
#             )

#             logger.info(
#                 "Coordinates: %s",
#                 clean_value(
#                     result.get(
#                         "maps_check_coordinates",
#                         "",
#                     )
#                 )
#                 or "(none)",
#             )

#             if "address_score" in result:
#                 logger.info(
#                     "Address Score: %.3f",
#                     float(
#                         result.get(
#                             "address_score",
#                             0.0,
#                         )
#                         or 0.0
#                     ),
#                 )

#             if "title_score" in result:
#                 logger.info(
#                     "Title Score: %.3f",
#                     float(
#                         result.get(
#                             "title_score",
#                             0.0,
#                         )
#                         or 0.0
#                     ),
#                 )

#             # =================================================
#             # CHECKPOINT
#             # =================================================

#             if processed > 0 and processed % CHECKPOINT_EVERY == 0:
#                 logger.info(
#                     "Checkpoint save after %s processed rows...",
#                     processed,
#                 )

#                 try:
#                     atomic_save_excel(
#                         df,
#                         output_path,
#                     )

#                     logger.info("Checkpoint saved.")

#                 except Exception as exc:
#                     logger.exception(
#                         "Checkpoint save failed: %s",
#                         exc,
#                     )

#         # ====================================================
#         # FINAL SAVE
#         # ====================================================

#         logger.info("Final saving...")

#         atomic_save_excel(
#             df,
#             output_path,
#         )

#         try:
#             context.close()
#         except Exception:
#             pass

#         try:
#             browser.close()
#         except Exception:
#             pass

#     logger.info("")
#     logger.info("============================================================")
#     logger.info(
#         "WORKER FINISHED%s",
#         (f" | W{worker_id}" if worker_id is not None else ""),
#     )
#     logger.info("============================================================")
#     logger.info(
#         "Rows         : %s",
#         len(df),
#     )
#     logger.info(
#         "Processed    : %s",
#         processed,
#     )
#     logger.info(
#         "Skipped      : %s",
#         counters["SKIPPED"],
#     )
#     logger.info(
#         "MATCH        : %s",
#         counters["MATCH"],
#     )
#     logger.info(
#         "MISMATCH     : %s",
#         counters["MISMATCH"],
#     )
#     logger.info(
#         "NEED_REVIEW  : %s",
#         counters["NEED_REVIEW"],
#     )
#     logger.info(
#         "OPEN_FAILED  : %s",
#         counters["OPEN_FAILED"],
#     )
#     logger.info(
#         "REPAIRED     : %s",
#         counters["REPAIRED"],
#     )
#     logger.info(
#         "NOT_REPAIRED : %s",
#         counters["NOT_REPAIRED"],
#     )
#     logger.info(
#         "Output       : %s",
#         output_path,
#     )
#     logger.info("============================================================")

#     return {
#         "rows": len(df),
#         "processed": processed,
#         "output": str(output_path),
#         **counters,
#     }


# # ============================================================
# # SPLIT DATA FOR WORKERS
# # ============================================================


# def split_dataframe_for_workers(
#     df,
#     workers,
# ):
#     """
#     Split rows into contiguous chunks.

#     Example:
#         1926 rows / 4 workers
#         W0 -> 482 rows
#         W1 -> 482 rows
#         W2 -> 481 rows
#         W3 -> 481 rows
#     """

#     workers = max(
#         1,
#         min(
#             int(workers),
#             len(df),
#         ),
#     )

#     total = len(df)

#     base = total // workers
#     remainder = total % workers

#     chunks = []

#     start = 0

#     for worker_id in range(workers):
#         size = base + (1 if worker_id < remainder else 0)

#         end = start + size

#         chunk = df.iloc[start:end].copy()

#         chunks.append(
#             (
#                 worker_id,
#                 chunk,
#             )
#         )

#         start = end

#     return chunks


# # ============================================================
# # MERGE WORKER OUTPUTS
# # ============================================================


# def merge_worker_outputs(
#     worker_outputs,
#     final_output,
# ):
#     frames = []

#     for worker_output in worker_outputs:
#         worker_output = Path(worker_output)

#         if not worker_output.exists():
#             raise FileNotFoundError(f"Worker output missing: {worker_output}")

#         frame = pd.read_excel(worker_output)

#         frames.append(frame)

#     if not frames:
#         raise RuntimeError("No worker outputs to merge.")

#     merged = pd.concat(
#         frames,
#         ignore_index=True,
#     )

#     if ORIGINAL_INDEX_COLUMN in merged.columns:
#         merged[ORIGINAL_INDEX_COLUMN] = pd.to_numeric(
#             merged[ORIGINAL_INDEX_COLUMN],
#             errors="coerce",
#         )

#         merged = merged.sort_values(
#             ORIGINAL_INDEX_COLUMN,
#             kind="stable",
#         )

#         merged = merged.drop(
#             columns=[ORIGINAL_INDEX_COLUMN],
#         )

#     merged = merged.reset_index(
#         drop=True,
#     )

#     atomic_save_excel(
#         merged,
#         final_output,
#     )

#     return merged


# # ============================================================
# # CTRL+C / PROCESS TREE TERMINATION
# # ============================================================


# def terminate_process_tree(
#     process,
#     worker_id=None,
# ):
#     """
#     Stop a worker AND its Chromium/Playwright child processes.

#     Windows:
#         taskkill /PID <pid> /T /F
#         /T = terminate child process tree
#         /F = force termination

#     This is important because each worker is launched in a separate
#     terminal with its own Chromium process tree.
#     """

#     if process is None:
#         return

#     try:
#         if process.poll() is not None:
#             return
#     except Exception:
#         pass

#     label = f"Worker {worker_id}" if worker_id is not None else "Worker"

#     if os.name == "nt":
#         try:
#             subprocess.run(
#                 [
#                     "taskkill",
#                     "/PID",
#                     str(process.pid),
#                     "/T",
#                     "/F",
#                 ],
#                 stdout=subprocess.DEVNULL,
#                 stderr=subprocess.DEVNULL,
#                 check=False,
#             )

#             print(f"🛑 {label} stopped (process tree terminated)")

#             return

#         except Exception:
#             pass

#     # Non-Windows / fallback
#     try:
#         process.terminate()
#     except Exception:
#         pass

#     try:
#         process.wait(timeout=3)
#         return
#     except Exception:
#         pass

#     try:
#         process.kill()
#     except Exception:
#         pass


# # ============================================================
# # MULTI-WORKER PARENT
# # ============================================================


# def run_multi_worker(
#     workers=DEFAULT_WORKERS,
# ):
#     """
#     Parent process - MISSING GOOGLE MAPS URL ONLY.

#     FLOW:
#         1. Read the full original Excel.
#         2. Select ONLY rows whose google_maps_url is empty/invalid.
#         3. Split ONLY those rows across workers.
#         4. On Windows, open EACH worker in its OWN terminal window.
#         5. Each worker writes its own chunk output.
#         6. Parent waits for all worker result files.
#         7. Merge repaired/checked rows back into the FULL original Excel.
#         8. Rows that already had google_maps_url are never opened/searched.

#     IMPORTANT:
#         Existing rows with valid Google Maps Place URL are left untouched.
#     """

#     input_path = Path(INPUT_FILE)

#     final_output = Path(OUTPUT_FILE)

#     if not input_path.exists():
#         raise FileNotFoundError(f"Input Excel not found: {input_path}")

#     # ========================================================
#     # READ FULL SOURCE
#     # ========================================================

#     source_df = pd.read_excel(input_path)

#     if source_df.empty:
#         raise ValueError("Input Excel is empty.")

#     if GOOGLE_MAPS_COLUMN not in source_df.columns:
#         source_df[GOOGLE_MAPS_COLUMN] = ""

#     # Preserve exact original ordering / merge key.
#     source_df = source_df.copy()

#     source_df[ORIGINAL_INDEX_COLUMN] = list(range(len(source_df)))

#     # ========================================================
#     # FILTER: ONLY ROWS WITHOUT VALID GOOGLE MAPS PLACE URL
#     # ========================================================

#     missing_mask = source_df[GOOGLE_MAPS_COLUMN].apply(
#         lambda value: not is_google_maps_place_url(clean_value(value))
#     )

#     work_df = source_df.loc[missing_mask].copy()

#     skipped_existing = len(source_df) - len(work_df)

#     # ========================================================
#     # NOTHING TO DO
#     # ========================================================

#     if work_df.empty:
#         final_df = source_df.drop(
#             columns=[ORIGINAL_INDEX_COLUMN],
#             errors="ignore",
#         ).reset_index(drop=True)

#         atomic_save_excel(
#             final_df,
#             final_output,
#         )

#         print("")
#         print("=" * 75)
#         print("CHECK_MAPS FINISHED")
#         print("=" * 75)
#         print(f"📊 Total rows       : {len(source_df)}")
#         print(f"⚡ Existing Maps    : {skipped_existing}")
#         print("🔎 Need workers     : 0")
#         print(f"📁 Result           : {final_output}")
#         print("=" * 75)

#         return {
#             "rows": len(source_df),
#             "workers": 0,
#             "processed_missing": 0,
#             "skipped_existing": skipped_existing,
#             "output": str(final_output),
#         }

#     workers = max(
#         1,
#         min(
#             int(workers),
#             len(work_df),
#         ),
#     )

#     # ========================================================
#     # SINGLE WORKER
#     # ========================================================

#     if workers == 1:
#         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

#         run_dir = final_output.parent / f".check_maps_run_{timestamp}"

#         chunks_dir = run_dir / "chunks"

#         chunks_dir.mkdir(
#             parents=True,
#             exist_ok=True,
#         )

#         chunk_input = chunks_dir / "chunk_00.xlsx"

#         chunk_output = chunks_dir / "chunk_00_checked.xlsx"

#         atomic_save_excel(
#             work_df,
#             chunk_input,
#         )

#         process_excel_file(
#             chunk_input,
#             chunk_output,
#             worker_id=0,
#         )

#         repaired_df = pd.read_excel(chunk_output)

#         full_df = _merge_repaired_rows_back(
#             source_df,
#             repaired_df,
#         )

#         atomic_save_excel(
#             full_df,
#             final_output,
#         )

#         return {
#             "rows": len(full_df),
#             "workers": 1,
#             "processed_missing": len(work_df),
#             "skipped_existing": skipped_existing,
#             "output": str(final_output),
#             "worker_dir": str(run_dir),
#         }

#     # ========================================================
#     # MULTI WORKER RUN DIR
#     # ========================================================

#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

#     run_dir = final_output.parent / f".check_maps_run_{timestamp}"

#     chunks_dir = run_dir / "chunks"

#     chunks_dir.mkdir(
#         parents=True,
#         exist_ok=True,
#     )

#     chunks = split_dataframe_for_workers(
#         work_df,
#         workers,
#     )

#     commands = []
#     worker_outputs = []

#     print("")
#     print("=" * 75)
#     print("CHECK_MAPS TITLE-REPAIR MISSING-ONLY MULTI-WORKER START")
#     print("=" * 75)
#     print(f"📊 Total rows       : {len(source_df)}")
#     print(f"⚡ Existing Maps    : {skipped_existing} (SKIPPED)")
#     print(f"🔎 Missing Maps     : {len(work_df)} (ONLY THESE WILL RUN)")
#     print(f"👷 Workers          : {workers}")
#     print(f"📂 Run dir          : {run_dir}")
#     print("=" * 75)

#     for (
#         worker_id,
#         chunk,
#     ) in chunks:
#         chunk_input = chunks_dir / f"chunk_{worker_id:02d}.xlsx"

#         chunk_output = chunks_dir / (f"chunk_{worker_id:02d}_checked.xlsx")

#         atomic_save_excel(
#             chunk,
#             chunk_input,
#         )

#         worker_outputs.append(chunk_output)

#         command = [
#             sys.executable,
#             "-m",
#             "app.check_maps",
#             "--worker-mode",
#             "--worker-id",
#             str(worker_id),
#             "--input",
#             str(chunk_input),
#             "--output",
#             str(chunk_output),
#         ]

#         commands.append(
#             (
#                 worker_id,
#                 command,
#                 chunk_output,
#             )
#         )

#         print(f"👷 W{worker_id}: {len(chunk)} missing-url rows")

#     print("=" * 75)

#     # ========================================================
#     # LAUNCH EACH WORKER IN ITS OWN TERMINAL
#     # ========================================================

#     processes = []

#     started_at = time.time()

#     for (
#         worker_id,
#         command,
#         chunk_output,
#     ) in commands:
#         if os.name == "nt":
#             # Windows:
#             # CREATE_NEW_CONSOLE opens a separate terminal window
#             # for EACH worker. Parent terminal only shows summary.
#             creationflags = (
#                 subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
#             )

#             process = subprocess.Popen(
#                 command,
#                 cwd=str(PROJECT_ROOT),
#                 creationflags=creationflags,
#             )

#         else:
#             # Non-Windows fallback:
#             # separate process, same terminal environment.
#             process = subprocess.Popen(
#                 command,
#                 cwd=str(PROJECT_ROOT),
#             )

#         processes.append(
#             (
#                 worker_id,
#                 process,
#                 chunk_output,
#             )
#         )

#         print(f"🚀 Worker {worker_id} started in separate terminal")

#     # ========================================================
#     # WAIT
#     # ========================================================

#     failed_workers = []

#     try:
#         for (
#             worker_id,
#             process,
#             chunk_output,
#         ) in processes:
#             return_code = process.wait()

#             if return_code != 0:
#                 failed_workers.append(
#                     (
#                         worker_id,
#                         return_code,
#                     )
#                 )

#                 print(f"❌ Worker {worker_id} failed (exit={return_code})")

#             elif not Path(chunk_output).exists():
#                 failed_workers.append(
#                     (
#                         worker_id,
#                         "NO_OUTPUT",
#                     )
#                 )

#                 print(f"❌ Worker {worker_id} finished but output file was not created")

#             else:
#                 print(f"✅ Worker {worker_id} finished")

#     except KeyboardInterrupt:
#         print("")
#         print("⛔ CTRL+C detected.")
#         print("🛑 Stopping ALL worker terminals and Chromium processes...")

#         for (
#             worker_id,
#             process,
#             _,
#         ) in processes:
#             terminate_process_tree(
#                 process,
#                 worker_id=worker_id,
#             )

#         print("✅ All workers stopped.")

#         # Exit cleanly instead of printing a long traceback.
#         return {
#             "rows": len(source_df),
#             "workers": workers,
#             "processed_missing": 0,
#             "skipped_existing": skipped_existing,
#             "cancelled": True,
#             "output": str(final_output),
#             "worker_dir": str(run_dir),
#         }

#     if failed_workers:
#         raise RuntimeError(
#             "Some check_maps workers failed: "
#             + ", ".join(
#                 (f"W{wid}={code}")
#                 for (
#                     wid,
#                     code,
#                 ) in failed_workers
#             )
#         )

#     parallel_elapsed = time.time() - started_at

#     # ========================================================
#     # MERGE WORKER OUTPUTS
#     # ========================================================

#     print("")
#     print("🔀 MERGING WORKER RESULTS...")

#     merge_started = time.time()

#     repaired_frames = []

#     for worker_output in worker_outputs:
#         repaired_frames.append(pd.read_excel(worker_output))

#     repaired_df = pd.concat(
#         repaired_frames,
#         ignore_index=True,
#     )

#     full_df = _merge_repaired_rows_back(
#         source_df,
#         repaired_df,
#     )

#     atomic_save_excel(
#         full_df,
#         final_output,
#     )

#     merge_elapsed = time.time() - merge_started

#     total_elapsed = time.time() - started_at

#     print("")
#     print("=" * 75)
#     print("CHECK_MAPS TITLE-REPAIR MISSING-ONLY MULTI-WORKER FINISHED")
#     print("=" * 75)
#     print(f"📊 Total rows       : {len(full_df)}")
#     print(f"⚡ Existing skipped : {skipped_existing}")
#     print(f"🔎 Missing processed: {len(work_df)}")
#     print(f"👷 Workers          : {workers}")
#     print(f"⏱️ Parallel         : {parallel_elapsed:.2f}s")
#     print(f"🔀 Merge            : {merge_elapsed:.2f}s")
#     print(f"⏱️ Total            : {total_elapsed:.2f}s")
#     print(f"📁 Result           : {final_output}")
#     print(f"📁 Worker dir       : {run_dir}")
#     print("=" * 75)

#     return {
#         "rows": len(full_df),
#         "workers": workers,
#         "processed_missing": len(work_df),
#         "skipped_existing": skipped_existing,
#         "parallel_seconds": (parallel_elapsed),
#         "merge_seconds": (merge_elapsed),
#         "total_seconds": (total_elapsed),
#         "output": str(final_output),
#         "worker_dir": str(run_dir),
#     }


# def _merge_repaired_rows_back(
#     original_df,
#     repaired_df,
# ):
#     # ------------------------------------------------------------
#     # Make destination columns safe for mixed worker values.
#     # ------------------------------------------------------------
#     for column in repaired_df.columns:
#         if column == "_original_index":
#             continue

#         if column not in original_df.columns:
#             original_df[column] = None

#         if original_df[column].dtype != "object":
#             original_df[column] = original_df[column].astype("object")

#     # ------------------------------------------------------------
#     # Merge repaired rows
#     # ------------------------------------------------------------
#     for _, repaired_row in repaired_df.iterrows():
#         original_index = repaired_row["_original_index"]

#         for column in repaired_df.columns:
#             if column == "_original_index":
#                 continue

#             value = repaired_row[column]

#             original_df.at[
#                 original_index,
#                 column,
#             ] = value

#     return original_df


# # ============================================================
# # CLI
# # ============================================================


# def parse_args():
#     import argparse

#     parser = argparse.ArgumentParser(
#         description=(
#             "Google Maps checker / repair - "
#             "missing google_maps_url only, "
#             "with one terminal per worker"
#         )
#     )

#     parser.add_argument(
#         "--workers",
#         type=int,
#         default=DEFAULT_WORKERS,
#         help=(f"Number of parallel workers. Default: {DEFAULT_WORKERS}"),
#     )

#     parser.add_argument(
#         "--worker-mode",
#         action="store_true",
#         help=argparse.SUPPRESS,
#     )

#     parser.add_argument(
#         "--worker-id",
#         type=int,
#         default=0,
#         help=argparse.SUPPRESS,
#     )

#     parser.add_argument(
#         "--input",
#         dest="input_path",
#         default="",
#         help=argparse.SUPPRESS,
#     )

#     parser.add_argument(
#         "--output",
#         dest="output_path",
#         default="",
#         help=argparse.SUPPRESS,
#     )

#     return parser.parse_args()


# # ============================================================
# # MAIN
# # ============================================================


# def main():
#     args = parse_args()

#     if args.worker_mode:
#         if not args.input_path:
#             raise ValueError("--input is required in worker mode.")

#         if not args.output_path:
#             raise ValueError("--output is required in worker mode.")

#         try:
#             return process_excel_file(
#                 args.input_path,
#                 args.output_path,
#                 worker_id=args.worker_id,
#             )

#         except KeyboardInterrupt:
#             print("")
#             print(f"⛔ Worker {args.worker_id}: CTRL+C detected.")
#             print(f"🛑 Worker {args.worker_id}: stopping now.")

#             # Clean exit. Playwright/browser child processes are also
#             # terminated automatically when this worker process exits.
#             raise SystemExit(130)

#     try:
#         return run_multi_worker(
#             workers=args.workers,
#         )

#     except KeyboardInterrupt:
#         print("")
#         print("⛔ CTRL+C detected. Exiting.")
#         raise SystemExit(130)


# # ============================================================
# # ENTRY POINT
# # ============================================================


# if __name__ == "__main__":
#     main()
import logging
import math
import os
import re
import sys
import subprocess
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# PROJECT ROOT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# IMPORT CONFIG
# ============================================================

from config import (
    HEADLESS,
    INPUT_FILE,
    OUTPUT_FILE,
    PAGE_TIMEOUT,
)


# ============================================================
# IMPORT SEARCH
# ============================================================

try:
    from .search import search_google_maps
except ImportError:
    from app.search import search_google_maps


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("check_maps")

if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
    )


# ============================================================
# CONFIG
# ============================================================

CHECK_MAPS_REPAIR_BUILD = "2026-09-07-final-promote-recovered-v6"

NAVIGATION_TIMEOUT = 30_000

# Chờ sau khi Maps load
WAIT_AFTER_LOAD = 2.0

# Retry mở URL
RETRY_COUNT = 3

# Sau bao nhiêu record thì save checkpoint
CHECKPOINT_EVERY = 10

# Ngưỡng Address MATCH
ADDRESS_MATCH_THRESHOLD = 0.55

# Status final
RESUME_SKIP_STATUSES = {
    "MATCH",
    "MISMATCH",
}

# Những status sẽ research lại
FORCE_RESEARCH_STATUSES = {
    "OPEN_FAILED",
    "NO_MAPS_URL",
    "INVALID_URL",
}

# ============================================================
# REPAIR MODE
# ============================================================
# Các lỗi từ pass search chính cần check_maps sửa lại Title/Address/Maps URL.
REPAIR_DETAIL_REASONS = {
    "NOT_VERIFIED",
    "ADDRESS_UNAVAILABLE",
    "ADDRESS_MISMATCH",
    "TITLE_LOCATION_MISMATCH",
    "TITLE_MISMATCH",
    "PROVINCE_MISMATCH",
    "TITLE_PREFILTER_REJECTED",
    "TITLE_NO_CANDIDATES",
}

# Chỉ tự sửa khi identity của place đủ mạnh.
REPAIR_TITLE_THRESHOLD = 0.82

# If TITLE search resolves to a very strong Maps title, that Place
# becomes the identity anchor. Its own Maps address + Place URL are
# used to repair stale/wrong Excel address data.
TITLE_PRIMARY_STRONG_THRESHOLD = 0.90

# A candidate with a weaker title may still be OPENED for repair
# if its real Maps address later proves it is the same place.
# This does NOT auto-accept the candidate; check_one() must still
# verify the Maps address before Title/Address/URL are committed.
RESEARCH_TITLE_CANDIDATE_MIN_SCORE = 0.55

# ADDRESS_UNAVAILABLE fallback: verify the same Place with a second
# independent address-guided search before accepting it.
ADDRESS_UNAVAILABLE_CONSENSUS_TITLE_MIN_SCORE = 0.85

# Các cột lỗi có thể được tạo bởi tool search chính.
SOURCE_STATUS_COLUMNS = ("status", "Status", "search_status")
SOURCE_REASON_COLUMNS = ("detail_reason", "reason", "missing_reason", "Missing Reason")

REPAIR_OLD_TITLE_COLUMN = "maps_old_title"
REPAIR_OLD_ADDRESS_COLUMN = "maps_old_address"
REPAIR_NEW_TITLE_COLUMN = "maps_repaired_title"
REPAIR_NEW_ADDRESS_COLUMN = "maps_repaired_address"
REPAIR_STATUS_COLUMN = "maps_repair_status"
REPAIR_REASON_COLUMN = "maps_repair_reason"

# Tối đa thời gian cố lấy address sau khi Maps mở
ADDRESS_WAIT_TIMEOUT = 8.0

# Khoảng delay giữa các lần extract
ADDRESS_POLL_INTERVAL = 0.5

# Reload tối đa một lần khi address không render
ADDRESS_RELOAD_ONCE = True

# Chờ sau reload
ADDRESS_RELOAD_WAIT = 2.0

# Tối đa số dòng body/main text dùng để scan
MAX_TEXT_LINES = 300

# Tối đa số candidate address
MAX_ADDRESS_CANDIDATES = 150


# ============================================================
# EXCEL COLUMNS
# ============================================================

TITLE_COLUMN = "Title"
ADDRESS_COLUMN = "Address"
URL_COLUMN = "URL"

GOOGLE_MAPS_COLUMN = "google_maps_url"

CHECK_STATUS_COLUMN = "maps_check_status"
CHECK_ADDRESS_COLUMN = "maps_check_address"
CHECK_URL_COLUMN = "maps_check_url"
ADDRESS_MATCH_COLUMN = "maps_address_match"
CHECK_REASON_COLUMN = "maps_check_reason"
CHECK_COORDINATES_COLUMN = "maps_check_coordinates"
CHECK_TIME_COLUMN = "maps_check_time"


# ============================================================
# GENERIC ADDRESS TOKENS
# ============================================================

GENERIC_ADDRESS_TOKENS = {
    "vietnam",
    "viet nam",
    "vn",
    "street",
    "road",
    "avenue",
    "boulevard",
    "highway",
    "ward",
    "district",
    "province",
    "city",
    "phuong",
    "quan",
    "huyen",
    "tinh",
    "xa",
    "thanh pho",
    "tp",
    "thi tran",
    "thi xa",
    "st",
    "rd",
    "ave",
    "blvd",
    "p",
    "q",
}


# ============================================================
# PLACEHOLDER / NON ADDRESS TEXT
# ============================================================

INVALID_ADDRESS_TEXTS = {
    "",
    "address",
    "địa chỉ",
    "dia chi",
    "directions",
    "chỉ đường",
    "chi duong",
    "website",
    "phone",
    "telephone",
    "số điện thoại",
    "so dien thoai",
    "copy address",
    "sao chép địa chỉ",
    "sao chep dia chi",
    "open now",
    "mở cửa",
    "mo cua",
    "thông tin về dữ liệu này",
    "thong tin ve du lieu nay",
    "about this data",
    "more information about this data",
    "khai thác tối đa google maps",
    "khai thac toi da google maps",
    "get the most out of google maps",
    "đăng nhập",
    "dang nhap",
    "sign in",
    "lưu",
    "luu",
    "save",
    "gần đây",
    "gan day",
    "recent",
}


# ============================================================
# BASIC VALUE HELPERS
# ============================================================


def clean_value(value):
    """
    Convert pandas NaN / None / invalid values -> "".
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = str(value).strip()

    if value.lower() in {
        "nan",
        "none",
        "null",
        "nat",
    }:
        return ""

    return value


# ============================================================
# TEXT NORMALIZATION
# ============================================================


def remove_accents(text):
    text = clean_value(text)

    text = unicodedata.normalize(
        "NFD",
        text,
    )

    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    return text


def normalize_text(text):
    """
    Normalize Vietnamese / address text.

    Example:

        "Cầu Bãi Dại, Quy Nhơn Nam, Gia Lai, Vietnam"

    ->
        "cau bai dai quy nhon nam gia lai vietnam"
    """

    text = clean_value(text)

    if not text:
        return ""

    text = text.replace("Đ", "D")
    text = text.replace("đ", "d")

    text = remove_accents(text)

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
    )

    return text.strip()


# ============================================================
# ADDRESS TOKENIZATION
# ============================================================


def address_tokens(address):
    normalized = normalize_text(address)

    if not normalized:
        return []

    tokens = normalized.split()

    result = []

    for token in tokens:
        if not token:
            continue

        result.append(token)

    return result


def meaningful_address_tokens(address):
    """
    Remove generic words such as:
        street
        road
        ward
        district
        province
        vietnam

    These words are not useful for identifying exact address.
    """

    normalized = normalize_text(address)

    if not normalized:
        return []

    tokens = normalized.split()

    result = []

    for token in tokens:
        if token in GENERIC_ADDRESS_TOKENS:
            continue

        result.append(token)

    return result


# ============================================================
# INVALID ADDRESS DETECTION
# ============================================================


def is_invalid_address_candidate(text):
    text = clean_value(text)
    if not text:
        return True

    normalized = normalize_text(text)
    if not normalized:
        return True

    # Exact and partial Google Maps chrome/UI text.  This is deliberately
    # a deny-list for UI chrome, while address extraction itself is a
    # whitelist (data-item-id=address / Address aria-label only).
    invalid_phrases = {normalize_text(x) for x in INVALID_ADDRESS_TEXTS if x}
    if normalized in invalid_phrases:
        return True

    ui_phrases = (
        "khai thac toi da google maps",
        "get the most out of google maps",
        "thong tin ve du lieu",
        "about this data",
        "duoc tai tro",
        "sponsored",
        "dang nhap",
        "sign in",
        "chi duong",
        "directions",
        "sao chep dia chi",
        "copy address",
    )
    if any(phrase in normalized for phrase in ui_phrases):
        return True

    if len(normalized) < 4:
        return True
    return False


# ============================================================
# CLEAN ADDRESS CANDIDATE
# ============================================================


def clean_address_candidate(text):
    """
    Làm sạch candidate lấy từ DOM.

    Có thể nhận:
        Address: 123 ABC...
        Địa chỉ: 123 ABC...
        123 ABC...
    """

    text = clean_value(text)

    if not text:
        return ""

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Remove private-use/icon glyphs frequently copied from Google Maps
    # (for example the address pin that appeared as "").
    text = "".join(ch for ch in text if not ("\ue000" <= ch <= "\uf8ff"))

    # Chuyển nhiều whitespace thành space
    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    # Xử lý prefix
    patterns = [
        r"^\s*địa\s*chỉ\s*:\s*",
        r"^\s*dia\s*chi\s*:\s*",
        r"^\s*address\s*:\s*",
        r"^\s*address\s*-\s*",
        r"^\s*địa\s*chỉ\s*-\s*",
        r"^\s*dia\s*chi\s*-\s*",
    ]

    for pattern in patterns:
        text = re.sub(
            pattern,
            "",
            text,
            flags=re.IGNORECASE,
        )

    # Nếu aria-label dạng:
    #
    # "Địa chỉ: Cầu Bãi Dại..."
    #
    # thì chỉ giữ phần address.
    text = text.strip()

    # Google Maps sometimes prefixes the visible address with a private-use
    # icon glyph (for example \ue0c8 / \uf3c5). Never persist that glyph.
    text = re.sub(r"^[\s\uE000-\uF8FF\u2000-\u206F]+", "", text).strip()

    if is_invalid_address_candidate(text):
        return ""

    # Loại các text UI thuần túy
    normalized = normalize_text(text)

    if normalized in INVALID_ADDRESS_TEXTS:
        return ""

    return text


# ============================================================
# GOOGLE MAPS URL
# ============================================================


def is_google_maps_url(url):
    url = clean_value(url)

    if not url:
        return False

    value = url.lower()

    return "google.com/maps" in value or "maps.google.com" in value


def is_google_maps_place_url(url):
    """
    Chỉ chấp nhận Google Maps Place URL.

    /maps/search/...
        -> KHÔNG phải Place URL

    /maps/place/...
        -> Place URL
    """

    url = clean_value(url)

    if not is_google_maps_url(url):
        return False

    value = url.lower()

    if "/maps/search" in value:
        return False

    if "/maps/place/" in value:
        return True

    if "/maps/place?" in value:
        return True

    return False


# ============================================================
# URL COORDINATES
# ============================================================


def _valid_coordinate(value, minimum, maximum):
    try:
        number = float(value)
    except Exception:
        return False

    if not math.isfinite(number):
        return False

    return minimum <= number <= maximum


def extract_coordinates_from_url(url):
    """
    Hỗ trợ:

        !3d13.7528117!4d109.2142909

    hoặc:

        @13.7528117,109.2142909
    """

    url = clean_value(url)

    if not url:
        return None, None

    decoded = unquote(url)

    # --------------------------------------------------------
    # !3dLAT!4dLNG
    # --------------------------------------------------------

    match = re.search(
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        decoded,
        flags=re.IGNORECASE,
    )

    if match:
        lat = float(match.group(1))
        lng = float(match.group(2))

        if _valid_coordinate(lat, -90, 90) and _valid_coordinate(lng, -180, 180):
            return lat, lng

    # --------------------------------------------------------
    # @LAT,LNG
    # --------------------------------------------------------

    match = re.search(
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        decoded,
        flags=re.IGNORECASE,
    )

    if match:
        lat = float(match.group(1))
        lng = float(match.group(2))

        if _valid_coordinate(lat, -90, 90) and _valid_coordinate(lng, -180, 180):
            return lat, lng

    return None, None


def coordinates_to_string(lat, lng):
    if lat is None or lng is None:
        return ""

    return f"{lat:.7f}, {lng:.7f}"


# ============================================================
# PAGE COORDINATES
# ============================================================


def get_page_coordinates(page):
    """
    Coordinates chỉ để DEBUG.

    KHÔNG dùng để quyết định MATCH/MISMATCH.
    """

    try:
        current_url = page.url

        lat, lng = extract_coordinates_from_url(current_url)

        if lat is not None and lng is not None:
            return coordinates_to_string(
                lat,
                lng,
            )

    except Exception:
        pass

    return ""


# ============================================================
# BODY TEXT
# ============================================================


def get_main_text(page):
    """
    Ưu tiên [role=main].
    Google Maps thường render place details bên trong main.
    """

    selectors = [
        '[role="main"]',
        "main",
        "body",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = locator.count()

            if count <= 0:
                continue

            text = locator.first.inner_text(timeout=1500)

            text = clean_value(text)

            if text:
                return text

        except Exception:
            continue

    return ""


def get_text_lines(page):
    text = get_main_text(page)

    if not text:
        return []

    lines = []

    for raw_line in text.splitlines():
        line = clean_value(raw_line)

        if not line:
            continue

        line = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if not line:
            continue

        if len(line) < 3:
            continue

        lines.append(line)

        if len(lines) >= MAX_TEXT_LINES:
            break

    return lines


# ============================================================
# ADDRESS-LIKE LINE
# ============================================================


def looks_like_address(text):
    """
    Detect address-like text.

    Không cần address phải giống Excel.
    """

    text = clean_address_candidate(text)

    if not text:
        return False

    normalized = normalize_text(text)

    if not normalized:
        return False

    if normalized in {
        "address",
        "dia chi",
        "copy address",
        "sao chep dia chi",
        "directions",
        "chi duong",
    }:
        return False

    tokens = normalized.split()

    if len(tokens) < 2:
        return False

    # Có số nhà / số đường
    if re.search(r"\d", text):
        if len(tokens) >= 2:
            return True

    # Có comma
    if "," in text and len(tokens) >= 3:
        return True

    # Location markers
    address_markers = {
        "street",
        "road",
        "avenue",
        "boulevard",
        "highway",
        "ward",
        "district",
        "province",
        "city",
        "phuong",
        "quan",
        "huyen",
        "tinh",
        "xa",
        "thanh",
        "pho",
        "tp",
        "thi",
        "tran",
        "vietnam",
        "viet",
        "nam",
    }

    marker_count = sum(1 for token in tokens if token in address_markers)

    if marker_count >= 1:
        return True

    # Address dài
    if len(tokens) >= 5:
        return True

    return False


# ============================================================
# ADMINISTRATIVE / TITLE IDENTITY REPAIR
# ============================================================

# Administrative equivalence after provincial mergers.  Keep both old and
# new province names valid so multi-province files do not reject a correct
# Place only because Google/Excel uses a different administrative vintage.
ADMINISTRATIVE_LOCATION_GROUPS = (
    frozenset({"tay ninh", "long an"}),
    frozenset({"gia lai", "binh dinh"}),
    frozenset({"ninh binh", "ha nam", "nam dinh"}),
)

ROOM_UNIT_SUFFIX_PATTERNS = (
    r"\bdeluxe\b.*$",
    r"\bsuperior\b.*$",
    r"\bpremier\b.*$",
    r"\bexecutive\b.*$",
    r"\bfamily room\b.*$",
    r"\bking room\b.*$",
    r"\bqueen room\b.*$",
    r"\btwin room\b.*$",
    r"\bdouble room\b.*$",
    r"\bsingle room\b.*$",
    r"\broom\b.*$",
    r"\bsuite\b.*$",
)


def canonicalize_administrative_text(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    for group in ADMINISTRATIVE_LOCATION_GROUPS:
        present = [name for name in group if name in normalized]
        if not present:
            continue
        canonical = sorted(group)[0]
        for name in sorted(group, key=len, reverse=True):
            normalized = re.sub(r"\b" + re.escape(name) + r"\b", canonical, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def strip_room_unit_suffix(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    # Booking/OTA titles frequently append the room product after a dash.
    normalized = re.split(r"\s+(?:-|–|—|\|)\s+", normalized, maxsplit=1)[0].strip()
    for pattern in ROOM_UNIT_SUFFIX_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", normalized).strip()


# ============================================================
# TITLE EXTRACTION / SIMILARITY
# ============================================================


def normalize_title(text):
    """
    Normalize a hotel/business title to its CORE identity.

    Examples:
        "Khách sạn Tân Mỹ Thiên" -> "tan my thien"
        "Tân Mỹ Thiên Hotel"     -> "tan my thien"

        "Vuon Cau Hotel"                  -> "vuon cau"
        "Khách sạn Vườn Cau Tây Ninh"    -> "vuon cau"

        "HANZ Mai Vy Hotel - Deluxe King Room"
            -> "hanz mai vy"

    Administrative suffixes are removed only for identity comparison;
    the original Maps title is still written back to Excel.
    """
    text = strip_room_unit_suffix(text)

    if not text:
        return ""

    generic = {
        "hotel",
        "hotels",
        "motel",
        "motels",
        "homestay",
        "homestays",
        "hostel",
        "hostels",
        "resort",
        "resorts",
        "villa",
        "villas",
        "apartment",
        "apartments",
        "lodge",
        "lodges",
        "guesthouse",
        "guesthouses",
        "guest",
        "house",
        "inn",
        "bnb",
        "khach",
        "san",
        "nha",
        "nghi",
        # Connector / possessive / marketing noise.
        # These must NOT create a fake strong title match.
        "at",
        "in",
        "on",
        "by",
        "of",
        "the",
        "and",
        "with",
        "near",
        "from",
        "to",
        "s",
        # Common room/listing marketing words that can survive suffix
        # stripping in marketplace titles.
        "entire",
        "place",
        "room",
        "rooms",
        "deluxe",
        "superior",
        "standard",
        "family",
        "double",
        "single",
        "twin",
        "king",
        "queen",
        "bedroom",
        "bedrooms",
    }

    # Province names are useful for LOCATION verification, but should not
    # make the business title look different.
    administrative_tokens = set()

    for group in ADMINISTRATIVE_LOCATION_GROUPS:
        for province_name in group:
            administrative_tokens.update(normalize_text(province_name).split())

    tokens = []

    for token in text.split():
        if token in generic:
            continue

        if token in administrative_tokens:
            continue

        tokens.append(token)

    return " ".join(tokens) or text


def title_similarity(expected_title, actual_title):
    """Identity score that favors the exact branded business name.

    Room/unit suffixes are ignored.  Word-order-only differences are exact
    identity (Nhung Trang <-> Trang Nhung).  A shorter generic/subset title
    stays below an exact branded match (Mai Vy < HANZ Mai Vy).
    """
    from difflib import SequenceMatcher

    expected = normalize_title(expected_title)
    actual = normalize_title(actual_title)
    if not expected or not actual:
        return 0.0
    if expected == actual:
        return 1.0

    a = set(expected.split())
    b = set(actual.split())
    if a and a == b:
        return 1.0

    # Subset is plausible, never equal to an exact branded candidate.
    if expected in actual or actual in expected or (a and b and (a <= b or b <= a)):
        return 0.92

    seq = SequenceMatcher(None, expected, actual).ratio()
    token = len(a & b) / max(len(a | b), 1)
    coverage = len(a & b) / max(min(len(a), len(b)), 1)
    return min(1.0, max(seq, token, coverage * 0.90))


def title_identity_guard(
    expected_title,
    actual_title,
):
    """
    Structural guard for TITLE ANCHOR.

    A high fuzzy score alone is NOT sufficient.

    Prevents false positives such as:
        LukaLucy House - Homestay at Tay Ninh
        An House - Homestay at Tay Ninh

    while still accepting:
        Thảo Nghi Hotel
        Khách sạn Thảo Nghi

        Nhung Trang hotel
        HOTEL TRANG NHUNG

        Ngoc Tram's Homestay
        Ngoc Tram's Homestay
    """
    expected = normalize_title(expected_title)
    actual = normalize_title(actual_title)

    if not expected or not actual:
        return False

    if expected == actual:
        return True

    expected_tokens = {token for token in expected.split() if token}

    actual_tokens = {token for token in actual.split() if token}

    if not expected_tokens or not actual_tokens:
        return False

    intersection = expected_tokens & actual_tokens

    if not intersection:
        return False

    # Exact token set, only word order differs.
    if expected_tokens == actual_tokens:
        return True

    smaller = min(
        len(expected_tokens),
        len(actual_tokens),
    )

    overlap_smaller = len(intersection) / max(smaller, 1)

    union = expected_tokens | actual_tokens

    jaccard = len(intersection) / max(len(union), 1)

    # A full subset is a plausible renamed/extended business title.
    if expected_tokens <= actual_tokens or actual_tokens <= expected_tokens:
        return overlap_smaller >= 0.80

    # Otherwise require substantial shared business identity.
    return overlap_smaller >= 0.67 and jaccard >= 0.50


def _coordinates_from_maps_url(url):
    """
    Return coordinates from the FINAL Google Maps Place URL.

    This is intentionally preferred over page-state coordinates because
    a second-search/reload can leave stale map viewport coordinates.
    """
    url = clean_value(url)

    if not url:
        return ""

    decoded = unquote(url)

    match = re.search(
        r"@(-?\\d+(?:\\.\\d+)?),(-?\\d+(?:\\.\\d+)?)",
        decoded,
        flags=re.IGNORECASE,
    )

    if not match:
        match = re.search(
            r"!3d(-?\\d+(?:\\.\\d+)?)!4d(-?\\d+(?:\\.\\d+)?)",
            decoded,
            flags=re.IGNORECASE,
        )

    if not match:
        return ""

    try:
        return coordinates_to_string(
            float(match.group(1)),
            float(match.group(2)),
        )
    except Exception:
        return ""


def _final_match_invariant(
    decision,
    title,
    actual_title,
    actual_address,
    current_url,
):
    """
    Last safety gate before a row can leave check_one() as MATCH.

    This gate does NOT redo the whole search. It catches impossible final
    states caused by stale page state or an over-permissive fuzzy match.
    """
    decision = dict(decision if isinstance(decision, dict) else {})

    if decision.get("status") != "MATCH":
        return decision

    if not is_google_maps_place_url(current_url):
        return {
            "status": "NEED_REVIEW",
            "match": False,
            "score": float(decision.get("score", 0.0) or 0.0),
            "reason": "FINAL_PLACE_URL_INVALID",
        }

    if not clean_value(actual_title):
        return {
            "status": "NEED_REVIEW",
            "match": False,
            "score": float(decision.get("score", 0.0) or 0.0),
            "reason": "FINAL_MAPS_TITLE_EMPTY",
        }

    reason = clean_value(decision.get("reason", "")).upper()

    # ADDRESS_ANCHOR and consensus paths have independent address evidence.
    independent_address_evidence = (
        "ADDRESS_ANCHOR" in reason
        or "TITLE_AND_ADDRESS_VERIFIED" in reason
        or "SAME_PLACE_CONFIRMED" in reason
    )

    if (
        reason.startswith("TITLE_")
        and not independent_address_evidence
        and not title_identity_guard(
            title,
            actual_title,
        )
    ):
        return {
            "status": "NEED_REVIEW",
            "match": False,
            "score": float(decision.get("score", 0.0) or 0.0),
            "reason": "FINAL_TITLE_IDENTITY_GUARD_REJECTED",
        }

    if actual_address:
        cleaned_address = clean_address_candidate(actual_address)

        if (
            not cleaned_address
            or is_invalid_address_candidate(cleaned_address)
            or not looks_like_address(cleaned_address)
        ):
            return {
                "status": "NEED_REVIEW",
                "match": False,
                "score": float(decision.get("score", 0.0) or 0.0),
                "reason": "FINAL_MAPS_ADDRESS_INVALID",
            }

    return decision


def extract_maps_title(page):
    """Lấy tên place đang mở, không lấy Sponsored/UI text."""
    selectors = [
        "h1.DUwDvf",
        '[role="main"] h1',
        "h1",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 5)):
                text = clean_value(loc.nth(i).inner_text(timeout=800))
                normalized = normalize_text(text)
                if not text or not normalized:
                    continue
                if normalized in {"duoc tai tro", "sponsored", "google maps"}:
                    continue
                return text
        except Exception:
            continue

    # fallback từ /maps/place/<name>/
    try:
        url = unquote(clean_value(page.url))
        m = re.search(r"/maps/place/([^/@?]+)", url, flags=re.IGNORECASE)
        if m:
            return clean_value(m.group(1).replace("+", " "))
    except Exception:
        pass

    return ""


def first_existing_value(row, columns):
    for column in columns:
        if column in row.index:
            value = clean_value(row.get(column, ""))
            if value:
                return value
    return ""


def should_repair_row(row):
    status = first_existing_value(row, SOURCE_STATUS_COLUMNS).upper()
    reason = first_existing_value(row, SOURCE_REASON_COLUMNS).upper()
    check_status = clean_value(row.get(CHECK_STATUS_COLUMN, "")).upper()
    check_reason = clean_value(row.get(CHECK_REASON_COLUMN, "")).upper()

    if status == "MISSING" or status == "NOT_VERIFIED":
        return True
    if reason in REPAIR_DETAIL_REASONS:
        return True
    if check_status in {"NEED_REVIEW", "MISMATCH", "OPEN_FAILED"}:
        return True
    if check_reason in REPAIR_DETAIL_REASONS:
        return True
    return False


# ============================================================
# ADDRESS SIMILARITY
# ============================================================


def address_similarity(
    expected_address,
    actual_address,
):
    """
    So sánh Excel Address với Maps Address.

    Trả score 0.0 -> 1.0.
    """

    expected = canonicalize_administrative_text(expected_address)

    actual = canonicalize_administrative_text(actual_address)

    if not expected or not actual:
        return 0.0

    # Exact
    if expected == actual:
        return 1.0

    # Maps address chứa nguyên Excel address
    if expected in actual:
        return 0.95

    # Excel address chứa nguyên Maps address
    if actual in expected:
        return 0.90

    expected_tokens = set(meaningful_address_tokens(expected))

    actual_tokens = set(meaningful_address_tokens(actual))

    if not expected_tokens or not actual_tokens:
        return 0.0

    intersection = expected_tokens & actual_tokens

    token_score = len(intersection) / max(
        len(expected_tokens),
        1,
    )

    # --------------------------------------------------------
    # Location tail
    #
    # Lấy 3 token cuối của mỗi address.
    # Ví dụ:
    #
    # gia lai
    # quy nhon
    # vietnam
    # --------------------------------------------------------

    expected_location = set(expected.split()[-3:])

    actual_location = set(actual.split()[-3:])

    if expected_location and actual_location:
        location_intersection = expected_location & actual_location

        location_score = len(location_intersection) / max(
            len(expected_location),
            1,
        )
    else:
        location_score = 0.0

    score = token_score * 0.75 + location_score * 0.25

    return min(
        max(score, 0.0),
        1.0,
    )


# ============================================================
# ADDRESS DECISION
# ============================================================


def decide_match(
    expected_address,
    actual_address,
):
    """
    Final decision.

    ONLY:
        Excel Address
        vs
        Maps Address

    Title không tham gia.
    Coordinates không tham gia.
    """

    expected_address = clean_value(expected_address)

    actual_address = clean_value(actual_address)

    if not expected_address:
        return {
            "status": "NEED_REVIEW",
            "match": False,
            "reason": "MISSING_EXCEL_ADDRESS",
            "score": 0.0,
        }

    if not actual_address:
        return {
            "status": "NEED_REVIEW",
            "match": False,
            "reason": "MISSING_MAPS_ADDRESS",
            "score": 0.0,
        }

    score = address_similarity(
        expected_address,
        actual_address,
    )

    if score >= ADDRESS_MATCH_THRESHOLD:
        return {
            "status": "MATCH",
            "match": True,
            "reason": "ADDRESS_MATCH",
            "score": score,
        }

    return {
        "status": "MISMATCH",
        "match": False,
        "reason": "ADDRESS_MISMATCH",
        "score": score,
    }


# ============================================================
# DOM ADDRESS EXTRACTION
# ============================================================

ADDRESS_SELECTORS = [
    # Google Maps standard
    '[data-item-id="address"]',
    '[data-item-id*="address"]',
    # aria
    '[aria-label*="Address"]',
    '[aria-label*="address"]',
    '[aria-label*="Địa chỉ"]',
    '[aria-label*="địa chỉ"]',
    # tooltip
    '[data-tooltip*="Address"]',
    '[data-tooltip*="address"]',
    '[data-tooltip*="Địa chỉ"]',
    '[data-tooltip*="địa chỉ"]',
    # Main area
    '[role="main"] [data-item-id="address"]',
    '[role="main"] [data-item-id*="address"]',
    '[role="main"] [aria-label*="Address"]',
    '[role="main"] [aria-label*="address"]',
    '[role="main"] [aria-label*="Địa chỉ"]',
    '[role="main"] [aria-label*="địa chỉ"]',
    # Buttons
    '[role="main"] button[aria-label*="Address"]',
    '[role="main"] button[aria-label*="address"]',
    '[role="main"] button[aria-label*="Địa chỉ"]',
    '[role="main"] button[aria-label*="địa chỉ"]',
    # Links
    '[role="main"] a[aria-label*="Address"]',
    '[role="main"] a[aria-label*="address"]',
    '[role="main"] a[aria-label*="Địa chỉ"]',
    '[role="main"] a[aria-label*="địa chỉ"]',
]


def extract_dom_address_candidates(page):
    """Extract only from DOM nodes that Google Maps explicitly marks as address.

    IMPORTANT: never scan generic div/span/body text here.  That old fallback
    is what allowed UI strings such as "Khai thác tối đa Google Maps" to be
    promoted to a fake address.
    """
    candidates = []
    selectors = [
        'button[data-item-id="address"]',
        '[data-item-id="address"]',
        '[role="main"] button[data-item-id="address"]',
        '[role="main"] [data-item-id="address"]',
        'button[aria-label^="Địa chỉ:"]',
        'button[aria-label^="Address:"]',
        '[role="main"] [aria-label^="Địa chỉ:"]',
        '[role="main"] [aria-label^="Address:"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 10)):
                node = locator.nth(index)
                for attribute in ("aria-label", "data-value", "title"):
                    try:
                        value = node.get_attribute(attribute)
                        if value:
                            candidates.append(value)
                    except Exception:
                        pass
                try:
                    value = node.inner_text(timeout=400)
                    if value:
                        candidates.append(value)
                except Exception:
                    pass
        except Exception:
            continue

    return unique_address_candidates(candidates)


# ============================================================
# UNIQUE ADDRESS CANDIDATES
# ============================================================


def unique_address_candidates(candidates):
    result = []

    seen = set()

    for candidate in candidates:
        candidate = clean_address_candidate(candidate)

        if not candidate:
            continue

        normalized = normalize_text(candidate)

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)

        result.append(candidate)

        if len(result) >= MAX_ADDRESS_CANDIDATES:
            break

    return result


# ============================================================
# BODY / MAIN ADDRESS EXTRACTION
# ============================================================


def extract_address_from_main_text(
    page,
    expected_address,
):
    """
    Scan [role=main] text.

    Google Maps đôi khi render address thành nhiều dòng:

        Cầu Bãi Dại
        Quy Nhơn Nam
        Gia Lai
        Vietnam

    Vì vậy thử ghép:
        1 dòng
        2 dòng
        3 dòng
        4 dòng
    """

    lines = get_text_lines(page)

    if not lines:
        return ""

    candidates = []

    # --------------------------------------------------------
    # Single lines
    # --------------------------------------------------------

    for line in lines:
        if looks_like_address(line):
            candidates.append(line)

    # --------------------------------------------------------
    # Consecutive lines
    # --------------------------------------------------------

    max_join = 4

    for i in range(len(lines)):
        for size in range(
            2,
            max_join + 1,
        ):
            end = i + size

            if end > len(lines):
                break

            chunk = lines[i:end]

            joined = ", ".join(chunk)

            if looks_like_address(joined):
                candidates.append(joined)

    candidates = unique_address_candidates(candidates)

    if not candidates:
        return ""

    # --------------------------------------------------------
    # Rank against expected address
    # --------------------------------------------------------

    best_candidate = ""
    best_score = -1.0

    for candidate in candidates:
        score = address_similarity(
            expected_address,
            candidate,
        )

        if score > best_score:
            best_score = score
            best_candidate = candidate

    # Chỉ trả về candidate có ý nghĩa
    if best_candidate:
        return best_candidate

    return ""


# ============================================================
# EXTRACT MAPS ADDRESS
# ============================================================


def extract_maps_address(
    page,
    expected_address,
):
    """Return the real Maps address from explicit address controls only.

    We intentionally do NOT scan role=main/body or join arbitrary text lines.
    If Maps has not rendered an explicit address control, return empty and let
    the caller use verified search evidence / consensus instead of fabricating
    an ADDRESS_MISMATCH from UI text.
    """
    try:
        candidates = extract_dom_address_candidates(page)
    except Exception as exc:
        logger.debug("DOM address extraction failed: %s", exc)
        return ""

    candidates = [
        c
        for c in candidates
        if c and not is_invalid_address_candidate(c) and looks_like_address(c)
    ]
    if not candidates:
        return ""

    best_address = ""
    best_score = -1.0
    for candidate in candidates:
        score = address_similarity(expected_address, candidate)
        if score > best_score:
            best_score = score
            best_address = candidate

    if best_address:
        logger.info(
            "Maps Address selected from explicit address DOM | score=%.3f | %s",
            best_score,
            best_address,
        )
    return best_address


# ============================================================
# POPUP HANDLING
# ============================================================

POPUP_BUTTON_TEXTS = [
    "Accept all",
    "I agree",
    "Accept",
    "Agree",
    "Đồng ý",
    "Chấp nhận tất cả",
    "Chấp nhận",
    "Tôi đồng ý",
]


def close_google_popups(page):
    """
    Đóng cookie / consent popup nếu xuất hiện.
    """

    for text in POPUP_BUTTON_TEXTS:
        selectors = [
            f'button:has-text("{text}")',
            f'[role="button"]:has-text("{text}")',
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector)

                count = locator.count()

                if count <= 0:
                    continue

                for index in range(min(count, 3)):
                    try:
                        button = locator.nth(index)

                        if button.is_visible(timeout=300):
                            button.click(timeout=1000)

                            time.sleep(0.3)

                            logger.debug(
                                "Closed popup: %s",
                                text,
                            )

                            return True

                    except Exception:
                        continue

            except Exception:
                continue

    return False


# ============================================================
# AW SNAP DETECTION
# ============================================================


def is_aw_snap(page):
    """
    Detect Chrome:
        Aw, Snap!
        Aww, Snap!
    """

    try:
        title = clean_value(page.title()).lower()

        if "aw, snap" in title:
            return True

        if "aww, snap" in title:
            return True

    except Exception:
        pass

    try:
        body_text = page.locator("body").inner_text(timeout=1000).lower()

        patterns = [
            "aw, snap",
            "aww, snap",
            "aw snap",
            "aww snap",
            "this page isn't working",
            "page isn't working",
        ]

        for pattern in patterns:
            if pattern in body_text:
                return True

    except Exception:
        pass

    return False


# ============================================================
# PAGE RECOVERY
# ============================================================


def recover_page(page):
    """
    Recovery strategy:

        1. reload current page
        2. nếu vẫn Aw Snap:
             create new page
        3. close old page
        4. return new page
    """

    context = page.context

    # --------------------------------------------------------
    # Try reload
    # --------------------------------------------------------

    try:
        logger.warning("Aw, Snap detected. Reloading page...")

        page.reload(
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT,
        )

        time.sleep(ADDRESS_RELOAD_WAIT)

        if not is_aw_snap(page):
            logger.info("Aw, Snap recovered by reload.")

            return page

    except Exception as exc:
        logger.warning(
            "Reload recovery failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Create new page
    # --------------------------------------------------------

    logger.warning("Creating a new Playwright page...")

    try:
        new_page = context.new_page()

        new_page.set_default_timeout(5000)

        new_page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

        try:
            page.close(run_before_unload=False)
        except Exception:
            pass

        return new_page

    except Exception as exc:
        logger.error(
            "Could not create recovery page: %s",
            exc,
        )

        return page


# ============================================================
# OPEN GOOGLE MAPS URL
# ============================================================


def open_maps_url(
    page,
    url,
):
    """
    Open Place URL robustly.

    Return:
        page,
        success,
        reason
    """

    url = clean_value(url)

    if not is_google_maps_place_url(url):
        return (
            page,
            False,
            "INVALID_URL",
        )

    current_page = page

    for attempt in range(
        1,
        RETRY_COUNT + 1,
    ):
        logger.info(
            "Opening Maps URL (attempt %s/%s): %s",
            attempt,
            RETRY_COUNT,
            url,
        )

        # ----------------------------------------------------
        # Aw Snap trước khi goto
        # ----------------------------------------------------

        try:
            if is_aw_snap(current_page):
                current_page = recover_page(current_page)

        except Exception:
            pass

        # ----------------------------------------------------
        # Goto
        # ----------------------------------------------------

        try:
            current_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

        except PlaywrightTimeoutError:
            logger.warning(
                "Maps navigation timeout (attempt %s/%s)",
                attempt,
                RETRY_COUNT,
            )

        except Exception as exc:
            logger.warning(
                "Maps navigation error (attempt %s/%s): %s",
                attempt,
                RETRY_COUNT,
                exc,
            )

        # ----------------------------------------------------
        # Wait
        # ----------------------------------------------------

        time.sleep(WAIT_AFTER_LOAD)

        close_google_popups(current_page)

        # ----------------------------------------------------
        # Aw Snap after navigation
        # ----------------------------------------------------

        try:
            if is_aw_snap(current_page):
                logger.warning("Aw, Snap after navigation.")

                current_page = recover_page(current_page)

                continue

        except Exception:
            pass

        # ----------------------------------------------------
        # Validate current URL
        # ----------------------------------------------------

        try:
            current_url = clean_value(current_page.url)

        except Exception:
            current_url = ""

        if is_google_maps_place_url(current_url):
            logger.info("Maps Place URL opened successfully.")

            return (
                current_page,
                True,
                "OPEN_SUCCESS",
            )

        logger.warning(
            "Current URL is not a Place URL: %s",
            current_url,
        )

        time.sleep(1.0)

    return (
        current_page,
        False,
        "URL_IS_PLACE_BUT_NAVIGATION_FAILED",
    )


# ============================================================
# RESEARCH GOOGLE MAPS FROM ADDRESS
# ============================================================


def research_google_maps_from_address(
    page,
    title,
    address,
    context=None,
):
    """
    Luôn search lại bằng Excel Address.

    search.py yêu cầu title không rỗng,
    nên nếu title rỗng -> dùng address tạm.
    """

    address = clean_value(address)

    title = clean_value(title)

    if not address:
        return (
            page,
            "",
            {
                "success": False,
                "reason": "MISSING_EXCEL_ADDRESS",
            },
        )

    search_title = title if title else address

    logger.info(
        "REAL GOOGLE MAPS RESEARCH | Title=%s | Address=%s",
        search_title,
        address,
    )

    try:
        result = search_google_maps(
            page,
            search_title,
            address,
            context=context,
            logger=logger,
            timeout=PAGE_TIMEOUT,
        )

    except Exception as exc:
        logger.exception(
            "search_google_maps failed: %s",
            exc,
        )

        return (
            page,
            "",
            {
                "success": False,
                "reason": "SEARCH_EXCEPTION",
                "error": str(exc),
            },
        )

    # --------------------------------------------------------
    # search.py có thể trả page mới
    # --------------------------------------------------------

    result_page = None

    if isinstance(
        result,
        dict,
    ):
        result_page = result.get("page")

    if result_page is not None:
        page = result_page

        try:
            page.set_default_timeout(5000)

            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

        except Exception:
            pass

    # --------------------------------------------------------
    # Extract URL
    # --------------------------------------------------------

    new_url = ""

    if isinstance(
        result,
        dict,
    ):
        new_url = clean_value(
            result.get("google_maps_url") or result.get("url") or result.get("maps_url")
        )

    # --------------------------------------------------------
    # Nếu search.py không trả URL,
    # thử current page URL
    # --------------------------------------------------------

    if not new_url:
        try:
            current_url = clean_value(page.url)

            if is_google_maps_place_url(current_url):
                new_url = current_url

        except Exception:
            pass

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if not is_google_maps_place_url(new_url):
        reason = "SEARCH_DID_NOT_RETURN_PLACE_URL"

        if isinstance(
            result,
            dict,
        ):
            reason = (
                clean_value(result.get("detail_reason") or result.get("reason"))
                or reason
            )

        logger.warning(
            "Research failed: %s",
            reason,
        )

        # search.py can reject a Place only because its title vocabulary
        # differs (Hotel <-> Khách sạn) even when its address is exact.
        # Rescue independently by ADDRESS before giving up.
        (
            page,
            rescued_url,
            rescued_result,
        ) = direct_address_place_rescue(
            page,
            title,
            address,
        )

        if is_google_maps_place_url(rescued_url):
            return (
                page,
                rescued_url,
                {
                    **(
                        result
                        if isinstance(
                            result,
                            dict,
                        )
                        else {}
                    ),
                    **(
                        rescued_result
                        if isinstance(
                            rescued_result,
                            dict,
                        )
                        else {}
                    ),
                    "success": True,
                    "reason": ("DIRECT_ADDRESS_RESCUE_SUCCESS"),
                    "google_maps_url": rescued_url,
                },
            )

        return (
            page,
            "",
            {
                **(
                    result
                    if isinstance(
                        result,
                        dict,
                    )
                    else {}
                ),
                **(
                    rescued_result
                    if isinstance(
                        rescued_result,
                        dict,
                    )
                    else {}
                ),
                "success": False,
                "reason": reason,
            },
        )

    logger.info(
        "Fresh Google Maps URL: %s",
        new_url,
    )

    return (
        page,
        new_url,
        {
            **(
                result
                if isinstance(
                    result,
                    dict,
                )
                else {}
            ),
            "success": True,
            "reason": "RESEARCH_SUCCESS",
            "google_maps_url": new_url,
        },
    )


def wait_for_address(
    page: Page,
    expected_address: str,
    timeout: float = ADDRESS_WAIT_TIMEOUT,
    poll_interval: float = ADDRESS_POLL_INTERVAL,
) -> str:
    """
    Chờ Google Maps render địa chỉ.

    Không yêu cầu địa chỉ phải MATCH.
    Chỉ cần tìm được một candidate hợp lệ thì trả về.
    Việc MATCH/MISMATCH do decide_match() xử lý.
    """

    deadline = time.time() + timeout

    best_candidate = ""
    best_score = -1.0

    while time.time() < deadline:
        try:
            candidate = extract_maps_address(
                page,
                expected_address,
            )

            if candidate:
                score = address_similarity(
                    expected_address,
                    candidate,
                )

                # Có candidate hợp lệ thì giữ lại.
                if score > best_score:
                    best_candidate = candidate
                    best_score = score

                # MATCH rõ ràng thì trả về ngay.
                if score >= ADDRESS_MATCH_THRESHOLD:
                    return candidate

        except Exception:
            pass

        time.sleep(poll_interval)

    # Hết thời gian nhưng đã tìm thấy candidate:
    # chỉ trả về nếu nó thực sự giống một address, không phải UI text.
    if (
        best_candidate
        and not is_invalid_address_candidate(best_candidate)
        and looks_like_address(best_candidate)
    ):
        return best_candidate

    return ""


# ============================================================
# DIRECT TITLE RESCUE
# ============================================================


def _extract_title_from_place_url(url):
    url = clean_value(url)

    if not url:
        return ""

    try:
        decoded = unquote(url)

        match = re.search(
            r"/maps/place/([^/@?]+)",
            decoded,
            flags=re.IGNORECASE,
        )

        if match:
            return clean_value(match.group(1).replace("+", " "))

    except Exception:
        pass

    return ""


def _collect_place_links_from_current_page(
    page,
    limit=100,
):
    """
    Collect unique /maps/place/ URLs visible on the current search page.
    """
    result = []
    seen = set()

    # Direct redirect is the strongest candidate.
    try:
        current_url = clean_value(page.url)

        if is_google_maps_place_url(current_url):
            key = _extract_google_maps_place_identity(current_url) or current_url

            if key not in seen:
                seen.add(key)
                result.append(current_url)
    except Exception:
        pass

    selectors = [
        'a[href*="/maps/place/"]',
        '[role="feed"] a[href*="/maps/place/"]',
        '[role="main"] a[href*="/maps/place/"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), limit)

            for index in range(count):
                try:
                    href = clean_value(locator.nth(index).get_attribute("href"))
                except Exception:
                    href = ""

                if not href:
                    continue

                # Google may expose a relative href.
                if href.startswith("/"):
                    href = "https://www.google.com" + href

                if not is_google_maps_place_url(href):
                    continue

                key = _extract_google_maps_place_identity(href) or href

                if key in seen:
                    continue

                seen.add(key)
                result.append(href)

        except Exception:
            continue

    return result


def _title_rescue_location_tail(address):
    """
    Small location hint for discovery only.
    """
    address = canonicalize_administrative_text(address)

    if not address:
        return ""

    parts = [clean_value(part) for part in address.split(",") if clean_value(part)]

    if not parts:
        return ""

    return ", ".join(parts[-2:])


def direct_title_place_rescue(
    page,
    title,
    address="",
):
    """
    Independent Google Maps TITLE rescue.

    WHY:
        search.py may correctly SEE a Maps Place but reject it because its
        internal title prefilter still scores:
            "Khách sạn Tân Mỹ Thiên"
            vs
            "Tân Mỹ Thiên Hotel"
        too low.

    This rescue uses check_maps.py's normalized business identity and does
    NOT auto-write anything. It only returns the best Place candidate;
    check_one() still opens/validates the URL and obtains the address.

    Candidate ranking:
        1. exact normalized business identity
        2. stronger branded title
        3. query with location hint
    """
    title = clean_value(title)
    address = clean_value(address)

    if not title:
        return page, "", ""

    core_title = normalize_title(title)
    location_tail = _title_rescue_location_tail(address)

    variants = []

    def add(query):
        query = clean_value(query)

        if not query:
            return

        key = normalize_text(query)

        if key and key not in {normalize_text(x) for x in variants}:
            variants.append(query)

    add(title)

    if core_title:
        add(core_title)

    if core_title and location_tail:
        add(f"{core_title}, {location_tail}")

    if title and location_tail:
        add(f"{title}, {location_tail}")

    best_url = ""
    best_title = ""
    best_score = 0.0

    for variant_index, query in enumerate(
        variants,
        1,
    ):
        logger.info(
            "DIRECT TITLE RESCUE %s/%s | %s",
            variant_index,
            len(variants),
            query,
        )

        search_url = "https://www.google.com/maps/search/?api=1&query=" + quote(
            query,
            safe="",
        )

        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

        except PlaywrightTimeoutError:
            logger.warning("DIRECT TITLE RESCUE navigation timeout.")

        except Exception as exc:
            logger.warning(
                "DIRECT TITLE RESCUE navigation error: %s",
                exc,
            )

        # Let Maps hydrate the result feed / redirect.
        for _ in range(6):
            time.sleep(0.45)

            try:
                if is_google_maps_place_url(clean_value(page.url)):
                    break
            except Exception:
                pass

        close_google_popups(page)

        candidate_urls = _collect_place_links_from_current_page(
            page,
            limit=120,
        )

        for candidate_url in candidate_urls:
            candidate_title = _extract_title_from_place_url(candidate_url)

            # When Maps directly resolved the page, visible H1 is better.
            try:
                if candidate_url == clean_value(page.url):
                    candidate_title = extract_maps_title(page) or candidate_title
            except Exception:
                pass

            score = title_similarity(
                title,
                candidate_title,
            )

            # Exact normalized business identity always wins immediately.
            exact_identity = bool(
                normalize_title(title)
                and normalize_title(title) == normalize_title(candidate_title)
            )

            if score > best_score or (
                score == best_score
                and len(normalize_title(candidate_title).split())
                > len(normalize_title(best_title).split())
            ):
                best_score = score
                best_url = candidate_url
                best_title = candidate_title

            if exact_identity:
                logger.info(
                    "DIRECT TITLE RESCUE EXACT IDENTITY | "
                    "input=%r | maps=%r | score=%.3f | url=%r",
                    title,
                    candidate_title,
                    score,
                    candidate_url,
                )

                return (
                    page,
                    candidate_url,
                    candidate_title,
                )

        if best_url and best_score >= TITLE_PRIMARY_STRONG_THRESHOLD:
            break

    if best_url and best_score >= RESEARCH_TITLE_CANDIDATE_MIN_SCORE:
        logger.info(
            "DIRECT TITLE RESCUE BEST | input=%r | maps=%r | score=%.3f | url=%r",
            title,
            best_title,
            best_score,
            best_url,
        )

        return (
            page,
            best_url,
            best_title,
        )

    logger.warning(
        "DIRECT TITLE RESCUE FAILED | title=%r | best=%r | score=%.3f",
        title,
        best_title,
        best_score,
    )

    return page, "", ""


def direct_address_place_rescue(
    page,
    title,
    address,
):
    """
    ADDRESS rescue for the opposite case:

        Excel ADDRESS is correct
        Excel TITLE is old/wrong

    Search Google Maps with address-guided variants and select a Place only
    when its real Maps address matches the Excel address.

    This specifically handles cases where search.py itself finds:
        address_score = 1.000
    but still returns NOT_VERIFIED because its title prefilter rejected the
    Maps title.
    """
    title = clean_value(title)
    address = clean_value(address)

    if not address:
        return (
            page,
            "",
            {
                "success": False,
                "reason": ("DIRECT_ADDRESS_RESCUE_MISSING_ADDRESS"),
            },
        )

    core_title = normalize_title(title)

    variants = []

    def add(query):
        query = clean_value(query)

        if not query:
            return

        key = normalize_text(query)

        if key and key not in {normalize_text(x) for x in variants}:
            variants.append(query)

    if title:
        add(f"{title}, {address}")

    if core_title:
        add(f"{core_title}, {address}")

    add(address)

    best = None

    for variant_index, query in enumerate(
        variants,
        1,
    ):
        logger.info(
            "DIRECT ADDRESS RESCUE %s/%s | %s",
            variant_index,
            len(variants),
            query,
        )

        search_url = "https://www.google.com/maps/search/?api=1&query=" + quote(
            query,
            safe="",
        )

        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "DIRECT ADDRESS RESCUE navigation error: %s",
                exc,
            )

        # Do NOT scan after a fixed 1.5 seconds. Maps often needs several
        # seconds to hydrate/redirect. The old rescue therefore logged all
        # DIRECT ADDRESS RESCUE variants but never opened a candidate.
        candidate_urls = []
        discovery_deadline = time.time() + 7.0

        while time.time() < discovery_deadline:
            time.sleep(0.45)
            close_google_popups(page)

            try:
                current_place_url = clean_value(page.url)
            except Exception:
                current_place_url = ""

            if (
                is_google_maps_place_url(current_place_url)
                and current_place_url not in candidate_urls
            ):
                candidate_urls.insert(0, current_place_url)

            for discovered_url in _collect_place_links_from_current_page(
                page,
                limit=60,
            ):
                if (
                    is_google_maps_place_url(discovered_url)
                    and discovered_url not in candidate_urls
                ):
                    candidate_urls.append(discovered_url)

            # A direct Place redirect is strong discovery evidence. We still
            # open it below and verify its REAL address before accepting.
            if candidate_urls:
                break

        logger.info(
            "DIRECT ADDRESS RESCUE DISCOVERY | query=%r | candidates=%s | page_url=%r",
            query,
            len(candidate_urls),
            clean_value(getattr(page, "url", "")),
        )

        # ADDRESS is authoritative in this rescue branch.
        # Never reject a candidate only because its title differs from Excel.
        # If its real Maps address strongly matches Excel, that Place wins and
        # its Maps title/address/URL become the repaired canonical values.
        for candidate_url in candidate_urls[:16]:
            candidate_title = _extract_title_from_place_url(candidate_url)

            title_score = title_similarity(
                title,
                candidate_title,
            )

            (
                page,
                opened,
                _,
            ) = open_maps_url(
                page,
                candidate_url,
            )

            if not opened:
                continue

            try:
                opened_url = clean_value(page.url)
            except Exception:
                opened_url = candidate_url

            if not _place_urls_consistent(
                candidate_url,
                opened_url,
            ):
                continue

            actual_title = extract_maps_title(page) or candidate_title

            actual_address = wait_for_address(
                page,
                address,
                timeout=min(
                    ADDRESS_WAIT_TIMEOUT,
                    5.0,
                ),
            )

            if not actual_address:
                continue

            address_score = address_similarity(
                address,
                actual_address,
            )

            logger.info(
                "DIRECT ADDRESS CANDIDATE | "
                "Excel Title=%r | Maps Title=%r | "
                "Excel Address=%r | Maps Address=%r | "
                "title_score=%.3f | address_score=%.3f | url=%r",
                title,
                actual_title,
                address,
                actual_address,
                title_similarity(title, actual_title),
                address_score,
                opened_url,
            )

            candidate_result = {
                "success": (address_score >= ADDRESS_MATCH_THRESHOLD),
                "google_maps_url": opened_url,
                "url": opened_url,
                "actual_title": actual_title,
                "maps_title": actual_title,
                "actual_address": actual_address,
                "maps_address": actual_address,
                "title_score": title_similarity(
                    title,
                    actual_title,
                ),
                "address_score": address_score,
                "reason": (
                    "DIRECT_ADDRESS_RESCUE_MATCH"
                    if address_score >= ADDRESS_MATCH_THRESHOLD
                    else "DIRECT_ADDRESS_RESCUE_MISMATCH"
                ),
                "page": page,
            }

            if best is None or address_score > best.get(
                "address_score",
                -1,
            ):
                best = candidate_result

            if candidate_result["success"]:
                logger.info(
                    "DIRECT ADDRESS RESCUE SUCCESS | ADDRESS ANCHOR WINS | "
                    "Excel Title=%r | Maps Title=%r | "
                    "Excel Address=%r | Maps Address=%r | "
                    "title_score=%.3f | address_score=%.3f | url=%r",
                    title,
                    actual_title,
                    address,
                    actual_address,
                    candidate_result["title_score"],
                    address_score,
                    opened_url,
                )

                return (
                    page,
                    opened_url,
                    candidate_result,
                )

    if best:
        return (
            page,
            "",
            {
                **best,
                "success": False,
                "reason": ("DIRECT_ADDRESS_RESCUE_NOT_STRONG_ENOUGH"),
            },
        )

    return (
        page,
        "",
        {
            "success": False,
            "reason": ("DIRECT_ADDRESS_RESCUE_NO_PLACE"),
        },
    )


# ============================================================
# TITLE-FIRST RESEARCH FOR MISSING MAPS URL
# ============================================================


def research_google_maps_from_title(
    page,
    title,
    address,
    context=None,
):
    """
    Used ONLY when google_maps_url is empty/invalid.

    Goal:
        Search by TITLE first to locate the real business.
        Then return its Place URL so check_one() can open it,
        extract the REAL Maps title/address, and repair Excel.

    IMPORTANT:
        We intentionally do NOT feed the old Excel address into the
        initial search decision, because the address itself may be wrong.
    """

    title = clean_value(title)
    address = clean_value(address)

    if not title:
        return (
            page,
            "",
            {
                "success": False,
                "reason": "MISSING_EXCEL_TITLE",
            },
        )

    logger.info(
        "REAL GOOGLE MAPS TITLE RESEARCH | Title=%s | Old Address=%s",
        title,
        address,
    )

    try:
        # Empty address => search.py behaves as title-only search.
        result = search_google_maps(
            page,
            title,
            "",
            context=context,
            logger=logger,
            timeout=PAGE_TIMEOUT,
        )

    except Exception as exc:
        logger.exception(
            "Title research search_google_maps failed: %s",
            exc,
        )

        return (
            page,
            "",
            {
                "success": False,
                "reason": "TITLE_SEARCH_EXCEPTION",
                "error": str(exc),
            },
        )

    result_page = None

    if isinstance(result, dict):
        result_page = result.get("page")

    if result_page is not None:
        page = result_page

        try:
            page.set_default_timeout(5000)
            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)
        except Exception:
            pass

    new_url = ""

    if isinstance(result, dict):
        new_url = clean_value(
            result.get("google_maps_url") or result.get("url") or result.get("maps_url")
        )

    # search.py may reject a valid candidate during its own title prefilter.
    # Rescue by querying Maps directly and ranking candidates with the SAME
    # core-title identity used by this repair module.  This returns only a
    # candidate; check_one() still performs exact Place/address verification.
    if not new_url:
        (
            page,
            rescued_url,
            rescued_title,
        ) = direct_title_place_rescue(
            page,
            title,
            address,
        )

        if is_google_maps_place_url(rescued_url):
            new_url = rescued_url
            if isinstance(result, dict):
                result.setdefault("maps_check_title", rescued_title)
                result.setdefault("title", rescued_title)
                result["google_maps_url"] = rescued_url
                result["direct_title_rescue"] = True

    # If search.py did not expose the URL but browser is already
    # sitting on a Place page, keep it ONLY when the visible Maps title
    # is still plausibly related to the input title. This prevents the
    # final random candidate from being promoted (e.g. LE PHUONG HOTEL
    # -> Ben Hotel 839).
    if not new_url:
        try:
            current_url = clean_value(page.url)

            if is_google_maps_place_url(current_url):
                current_title = extract_maps_title(page)
                current_title_score = title_similarity(
                    title,
                    current_title,
                )

                if current_title_score >= RESEARCH_TITLE_CANDIDATE_MIN_SCORE:
                    # IMPORTANT:
                    # This only keeps the Place as a REPAIR CANDIDATE.
                    # It is NOT accepted yet.
                    #
                    # check_one() will open the Place and compare the real
                    # Maps address against the Excel address. Only after
                    # that verification may the Excel title/address/url
                    # be repaired.
                    new_url = current_url

                    logger.info(
                        "TITLE RESEARCH CANDIDATE KEPT FOR ADDRESS VERIFY | "
                        "input_title=%r | maps_title=%r | "
                        "title_score=%.3f | url=%r",
                        title,
                        current_title,
                        current_title_score,
                        current_url,
                    )
                else:
                    logger.warning(
                        "TITLE RESEARCH CURRENT PLACE REJECTED | "
                        "title=%r | actual_title=%r | score=%.3f | url=%r",
                        title,
                        current_title,
                        current_title_score,
                        current_url,
                    )

        except Exception:
            pass

    if not is_google_maps_place_url(new_url):
        reason = "TITLE_SEARCH_DID_NOT_RETURN_PLACE_URL"

        if isinstance(result, dict):
            reason = (
                clean_value(result.get("detail_reason") or result.get("reason"))
                or reason
            )

        logger.warning(
            "TITLE RESEARCH FAILED | title=%r | reason=%s",
            title,
            reason,
        )

        return (
            page,
            "",
            {
                **(result if isinstance(result, dict) else {}),
                "success": False,
                "reason": reason,
            },
        )

    logger.info(
        "TITLE RESEARCH FOUND PLACE | %s",
        new_url,
    )

    return (
        page,
        new_url,
        {
            **(result if isinstance(result, dict) else {}),
            "success": True,
            "reason": "TITLE_RESEARCH_SUCCESS",
            "google_maps_url": new_url,
        },
    )


# ============================================================
# ADDRESS_UNAVAILABLE CONSENSUS HELPERS
# ============================================================


def _extract_google_maps_place_identity(url):
    """
    Extract a stable identity from a Google Maps Place URL.

    Priority:
        1. !1s<entity-id>
        2. 0x...:0x... entity id
        3. slug + exact coordinates

    We never compare only titles because same-name places may exist
    in different provinces.
    """
    url = clean_value(url)

    if not is_google_maps_place_url(url):
        return ""

    match = re.search(
        r"!1s([^!/?&#]+)",
        url,
        flags=re.IGNORECASE,
    )

    if match:
        return "data:" + clean_value(match.group(1)).casefold()

    match = re.search(
        r"(0x[0-9a-f]+:0x[0-9a-f]+)",
        url,
        flags=re.IGNORECASE,
    )

    if match:
        return "hex:" + match.group(1).casefold()

    slug_match = re.search(
        r"/maps/place/([^/?#]+)",
        url,
        flags=re.IGNORECASE,
    )

    coord_match = re.search(
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
        url,
        flags=re.IGNORECASE,
    )

    if slug_match and coord_match:
        return (
            "slugcoord:"
            + slug_match.group(1).casefold()
            + "@"
            + coord_match.group(1)
            + ","
            + coord_match.group(2)
        )

    return ""


def _same_google_maps_place(
    url_a,
    url_b,
):
    identity_a = _extract_google_maps_place_identity(url_a)

    identity_b = _extract_google_maps_place_identity(url_b)

    return bool(identity_a and identity_b and identity_a == identity_b)


def confirm_address_unavailable_by_second_search(
    page,
    title,
    address,
    original_place_url,
    original_maps_title,
    context=None,
):
    """
    Safe handler for ADDRESS_UNAVAILABLE.

    FLOW:
        1. Keep the business Place found from TITLE research.
        2. Maps address DOM is missing even after retry/reload.
        3. Search independently with Excel TITLE + Excel ADDRESS.
        4. Accept only if the second search resolves to the SAME
           Google Maps Place identity.
        5. Never invent a Maps address; keep the Excel address unchanged.

    This is safer than simply accepting a high title score.
    """
    title = clean_value(title)
    address = clean_value(address)
    original_place_url = clean_value(original_place_url)
    original_maps_title = clean_value(original_maps_title)

    if not title or not address or not is_google_maps_place_url(original_place_url):
        return (
            page,
            False,
            "",
            {
                "reason": ("ADDRESS_UNAVAILABLE_CONSENSUS_INPUT_INVALID"),
            },
        )

    original_title_score = title_similarity(
        title,
        original_maps_title,
    )

    if original_title_score < ADDRESS_UNAVAILABLE_CONSENSUS_TITLE_MIN_SCORE:
        logger.warning(
            "ADDRESS_UNAVAILABLE CONSENSUS SKIPPED | title_score=%.3f < %.3f",
            original_title_score,
            ADDRESS_UNAVAILABLE_CONSENSUS_TITLE_MIN_SCORE,
        )

        return (
            page,
            False,
            "",
            {
                "reason": ("ADDRESS_UNAVAILABLE_TITLE_TOO_WEAK"),
                "title_score": original_title_score,
            },
        )

    logger.info(
        "ADDRESS_UNAVAILABLE -> SECOND ADDRESS-GUIDED SEARCH | "
        "title=%r | address=%r | original_url=%r",
        title,
        address,
        original_place_url,
    )

    (
        page,
        second_url,
        second_result,
    ) = research_google_maps_from_address(
        page,
        title,
        address,
        context=context,
    )

    second_url = clean_value(second_url)

    if not is_google_maps_place_url(second_url):
        logger.warning(
            "ADDRESS_UNAVAILABLE CONSENSUS FAILED | "
            "second search did not return Place URL"
        )

        return (
            page,
            False,
            "",
            {
                **(
                    second_result
                    if isinstance(
                        second_result,
                        dict,
                    )
                    else {}
                ),
                "reason": ("ADDRESS_UNAVAILABLE_SECOND_SEARCH_FAILED"),
            },
        )

    if not _same_google_maps_place(
        original_place_url,
        second_url,
    ):
        logger.warning(
            "ADDRESS_UNAVAILABLE CONSENSUS REJECTED | original_url=%r | second_url=%r",
            original_place_url,
            second_url,
        )

        return (
            page,
            False,
            second_url,
            {
                **(
                    second_result
                    if isinstance(
                        second_result,
                        dict,
                    )
                    else {}
                ),
                "reason": ("ADDRESS_UNAVAILABLE_PLACE_CONFLICT"),
            },
        )

    logger.info(
        "ADDRESS_UNAVAILABLE CONSENSUS ACCEPTED | "
        "same Place returned by TITLE and ADDRESS searches | "
        "title_score=%.3f | url=%s",
        original_title_score,
        original_place_url,
    )

    # Promote the FULL verified result from the second search.
    #
    # IMPORTANT:
    # The old code only returned "confirmed=True" and then check_one()
    # kept:
    #   actual_address = ""
    #   coordinates    = stale coordinates from a previous page
    #
    # even though the second search had already verified:
    #   actual_address
    #   actual_title
    #   exact Place URL
    #
    # Keep the same Place identity guarantee, but return the freshest
    # canonical evidence so check_one() can promote it into the final row.
    second_result = (
        second_result
        if isinstance(
            second_result,
            dict,
        )
        else {}
    )

    second_evidence = _extract_research_evidence(
        second_result,
        page,
    )

    promoted_url = (
        clean_value(second_url)
        or clean_value(second_evidence.get("url"))
        or original_place_url
    )

    promoted_title = _best_nonempty_maps_title(
        second_evidence.get("title", ""),
        original_maps_title,
    )

    promoted_address = _best_nonempty_maps_address(
        second_evidence.get("address", ""),
    )

    promoted_coordinates = get_page_coordinates(page) or ""

    logger.info(
        "ADDRESS_UNAVAILABLE PROMOTED EVIDENCE | "
        "title=%r | address=%r | coordinates=%r | url=%r",
        promoted_title,
        promoted_address,
        promoted_coordinates,
        promoted_url,
    )

    return (
        page,
        True,
        promoted_url,
        {
            **second_result,
            "success": True,
            "reason": ("ADDRESS_UNAVAILABLE_CONFIRMED_BY_SECOND_SEARCH"),
            "title_score": original_title_score,
            "google_maps_url": promoted_url,
            "url": promoted_url,
            "actual_title": promoted_title,
            "maps_title": promoted_title,
            "actual_address": promoted_address,
            "maps_address": promoted_address,
            "maps_check_coordinates": promoted_coordinates,
            "coordinates": promoted_coordinates,
        },
    )


# ============================================================
# VERIFIED SEARCH EVIDENCE / PLACE URL CONSISTENCY
# ============================================================


def _extract_research_evidence(
    result,
    page=None,
):
    """
    Preserve the title/address/url that search.py already verified.

    This matters because opening/reloading the Place page again may fail
    to render its title/address DOM even though search.py already saw them.
    We never replace verified evidence with an empty value.
    """
    result = result if isinstance(result, dict) else {}

    evidence_title = clean_value(
        result.get("actual_title")
        or result.get("maps_title")
        or result.get("place_title")
        or result.get("title_actual")
    )

    evidence_address = clean_address_candidate(
        result.get("actual_address")
        or result.get("maps_address")
        or result.get("place_address")
        or result.get("address_actual")
        or ""
    )

    evidence_url = clean_value(
        result.get("google_maps_url") or result.get("url") or result.get("maps_url")
    )

    if page is not None:
        if not evidence_title:
            try:
                evidence_title = extract_maps_title(page)
            except Exception:
                pass

        if not evidence_url:
            try:
                candidate_url = clean_value(page.url)
                if is_google_maps_place_url(candidate_url):
                    evidence_url = candidate_url
            except Exception:
                pass

    if evidence_address and (
        is_invalid_address_candidate(evidence_address)
        or not looks_like_address(evidence_address)
    ):
        evidence_address = ""

    return {
        "title": evidence_title,
        "address": evidence_address,
        "url": evidence_url,
    }


def _place_urls_consistent(
    expected_url,
    opened_url,
):
    """
    Validate that the Place URL opened by the browser is still the same
    Google Maps entity returned by search.

    Prefer Google entity identity. If one URL has no extractable identity,
    exact normalized URL equality is accepted as a conservative fallback.
    """
    expected_url = clean_value(expected_url)
    opened_url = clean_value(opened_url)

    if not (
        is_google_maps_place_url(expected_url) and is_google_maps_place_url(opened_url)
    ):
        return False

    expected_identity = _extract_google_maps_place_identity(expected_url)
    opened_identity = _extract_google_maps_place_identity(opened_url)

    if expected_identity and opened_identity:
        return expected_identity == opened_identity

    return unquote(expected_url).rstrip("/") == unquote(opened_url).rstrip("/")


def _best_nonempty_maps_title(
    *values,
):
    """
    First usable Maps title. Avoid known UI placeholders.
    """
    for value in values:
        value = clean_value(value)
        normalized = normalize_text(value)

        if not value or not normalized:
            continue

        if normalized in {
            "google maps",
            "duoc tai tro",
            "sponsored",
            "khai thac toi da google maps",
            "get the most out of google maps",
        }:
            continue

        return value

    return ""


def _best_nonempty_maps_address(
    *values,
):
    """
    First real address candidate. Never return Google Maps UI text.
    """
    for value in values:
        value = clean_address_candidate(value)

        if not value:
            continue

        if is_invalid_address_candidate(value):
            continue

        if not looks_like_address(value):
            continue

        return value

    return ""


# ============================================================
# CHECK ONE RECORD
# ============================================================


def check_one(
    page,
    title,
    address,
    maps_url,
    context=None,
    force_research=False,
):
    """
    Robust repair policy.

    RULE A - TITLE ANCHOR
    ---------------------
    If TITLE search resolves to a strong/exact Maps business identity:
        Excel title -> Maps title
        Excel address -> address belonging to THAT SAME Maps Place
        google_maps_url -> URL belonging to THAT SAME Maps Place

    The old Excel address is NOT allowed to pull us away to another business
    once the title identity is strong enough.

    RULE B - ADDRESS ANCHOR
    -----------------------
    If title identity is weak/mismatched, but Excel address is reliable:
        search from Excel title + Excel address
        require Maps address to match Excel address
        then repair Excel title from that Place
        and keep the exact verified Place URL.

    RULE C - URL
    ------------
    A /maps/place/ URL is committed only if:
        - browser really opens a Place URL, and
        - the opened URL is the SAME Maps entity as the searched URL.

    RULE D - ADDRESS DOM FAILURE
    ----------------------------
    Never use arbitrary Maps UI text as address.
    Preserve verified search evidence.
    If address is still unavailable, use same-Place consensus instead of
    fabricating ADDRESS_MISMATCH.
    """

    title = clean_value(title)
    address = clean_value(address)
    maps_url = clean_value(maps_url)

    current_page = page

    title_research_result = {}
    title_evidence = {
        "title": "",
        "address": "",
        "url": "",
    }

    # ========================================================
    # 1. TITLE-FIRST RESEARCH
    # ========================================================

    must_research = (
        force_research or not maps_url or not is_google_maps_place_url(maps_url)
    )

    searched_by_title = False

    if must_research:
        searched_by_title = True

        logger.info(
            "REPAIR FLOW | TITLE FIRST -> strong title anchors Place; "
            "weak title -> ADDRESS recovery."
        )

        (
            current_page,
            fresh_url,
            title_research_result,
        ) = research_google_maps_from_title(
            current_page,
            title,
            address,
            context=context,
        )

        title_evidence = _extract_research_evidence(
            title_research_result,
            current_page,
        )

        if fresh_url:
            maps_url = fresh_url

        elif address:
            # No usable title candidate at all -> address fallback.
            logger.info(
                "TITLE research returned no usable Place. "
                "Switching to ADDRESS-guided recovery."
            )

            (
                current_page,
                address_url,
                address_result,
            ) = research_google_maps_from_address(
                current_page,
                title,
                address,
                context=context,
            )

            if not address_url:
                return (
                    {
                        "status": "NEED_REVIEW",
                        "maps_check_address": "",
                        "maps_check_url": "",
                        "maps_address_match": False,
                        "maps_check_reason": "TITLE_AND_ADDRESS_RESEARCH_FAILED",
                        "maps_check_coordinates": "",
                        "maps_check_title": "",
                        "title_score": 0.0,
                        "address_score": 0.0,
                    },
                    current_page,
                )

            maps_url = address_url
            title_research_result = address_result
            title_evidence = _extract_research_evidence(
                address_result,
                current_page,
            )
            searched_by_title = False

        else:
            return (
                {
                    "status": "NEED_REVIEW",
                    "maps_check_address": "",
                    "maps_check_url": "",
                    "maps_address_match": False,
                    "maps_check_reason": "TITLE_RESEARCH_FAILED",
                    "maps_check_coordinates": "",
                    "maps_check_title": "",
                    "title_score": 0.0,
                    "address_score": 0.0,
                },
                current_page,
            )

    expected_place_url = clean_value(title_evidence.get("url") or maps_url)

    # ========================================================
    # 2. OPEN + VERIFY THE EXACT PLACE URL
    # ========================================================

    (
        current_page,
        opened,
        open_reason,
    ) = open_maps_url(
        current_page,
        maps_url,
    )

    if not opened:
        return (
            {
                "status": "OPEN_FAILED",
                "maps_check_address": "",
                "maps_check_url": maps_url,
                "maps_address_match": False,
                "maps_check_reason": open_reason,
                "maps_check_coordinates": get_page_coordinates(current_page),
                "maps_check_title": title_evidence.get("title", ""),
                "title_score": title_similarity(
                    title,
                    title_evidence.get("title", ""),
                ),
                "address_score": 0.0,
            },
            current_page,
        )

    try:
        current_url = clean_value(current_page.url)
    except Exception:
        current_url = maps_url

    if not is_google_maps_place_url(current_url):
        return (
            {
                "status": "NEED_REVIEW",
                "maps_check_address": "",
                "maps_check_url": "",
                "maps_address_match": False,
                "maps_check_reason": "MAPS_URL_NOT_PLACE_AFTER_OPEN",
                "maps_check_coordinates": get_page_coordinates(current_page),
                "maps_check_title": title_evidence.get("title", ""),
                "title_score": title_similarity(
                    title,
                    title_evidence.get("title", ""),
                ),
                "address_score": 0.0,
            },
            current_page,
        )

    if (
        expected_place_url
        and is_google_maps_place_url(expected_place_url)
        and not _place_urls_consistent(
            expected_place_url,
            current_url,
        )
    ):
        logger.warning(
            "PLACE URL REJECTED | searched Place != opened Place | "
            "searched=%r | opened=%r",
            expected_place_url,
            current_url,
        )

        return (
            {
                "status": "NEED_REVIEW",
                "maps_check_address": "",
                "maps_check_url": "",
                "maps_address_match": False,
                "maps_check_reason": "PLACE_URL_IDENTITY_CHANGED",
                "maps_check_coordinates": get_page_coordinates(current_page),
                "maps_check_title": title_evidence.get("title", ""),
                "title_score": title_similarity(
                    title,
                    title_evidence.get("title", ""),
                ),
                "address_score": 0.0,
            },
            current_page,
        )

    close_google_popups(current_page)

    coordinates = get_page_coordinates(current_page)

    actual_title = _best_nonempty_maps_title(
        extract_maps_title(current_page),
        title_evidence.get("title", ""),
    )

    title_score = title_similarity(
        title,
        actual_title,
    )

    # ========================================================
    # 3. EXTRACT ADDRESS FROM THIS SAME PLACE
    # ========================================================

    logger.info(
        "VERIFIED PLACE | Excel Title=%r | Maps Title=%r | title_score=%.3f | url=%r",
        title,
        actual_title,
        title_score,
        current_url,
    )

    actual_address = _best_nonempty_maps_address(
        wait_for_address(
            current_page,
            address,
            timeout=ADDRESS_WAIT_TIMEOUT,
        ),
        title_evidence.get("address", ""),
    )

    # One reload only; never lose already verified evidence.
    if not actual_address and ADDRESS_RELOAD_ONCE:
        preserved_title = actual_title
        preserved_url = current_url

        logger.info("Maps address unavailable. Reload SAME Place once.")

        try:
            current_page.reload(
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            time.sleep(ADDRESS_RELOAD_WAIT)
            close_google_popups(current_page)

            try:
                reload_url = clean_value(current_page.url)
            except Exception:
                reload_url = preserved_url

            if is_google_maps_place_url(reload_url) and _place_urls_consistent(
                preserved_url,
                reload_url,
            ):
                current_url = reload_url

                actual_title = _best_nonempty_maps_title(
                    extract_maps_title(current_page),
                    preserved_title,
                    title_evidence.get("title", ""),
                )

                actual_address = _best_nonempty_maps_address(
                    wait_for_address(
                        current_page,
                        address,
                        timeout=ADDRESS_WAIT_TIMEOUT,
                    ),
                    title_evidence.get("address", ""),
                )
            else:
                current_url = preserved_url
                actual_title = preserved_title

        except Exception as exc:
            logger.warning(
                "Same-Place reload failed: %s",
                exc,
            )
            current_url = preserved_url
            actual_title = preserved_title

        title_score = title_similarity(
            title,
            actual_title,
        )

    # ========================================================
    # 4. TITLE ANCHOR
    #
    # Excel title is right / Maps title is a strong identity match.
    # Trust THIS Place and repair Excel address from THIS Place.
    # ========================================================

    strong_title_anchor = (
        bool(actual_title)
        and title_score >= TITLE_PRIMARY_STRONG_THRESHOLD
        and title_identity_guard(
            title,
            actual_title,
        )
        and is_google_maps_place_url(current_url)
    )

    address_unavailable_confirmed = False
    address_unavailable_reason = ""

    if strong_title_anchor:
        logger.info(
            "TITLE ANCHOR ACCEPTED | score=%.3f >= %.3f | "
            "address will come from this exact Place.",
            title_score,
            TITLE_PRIMARY_STRONG_THRESHOLD,
        )

        if actual_address:
            # The title identifies the business. The address from this same
            # Place is therefore the repair value even if old Excel address
            # was stale/wrong.
            old_address_score = (
                address_similarity(
                    address,
                    actual_address,
                )
                if address
                else 0.0
            )

            reason = (
                "TITLE_VERIFIED_ADDRESS_FROM_SAME_PLACE"
                if old_address_score >= ADDRESS_MATCH_THRESHOLD
                else "TITLE_VERIFIED_EXCEL_ADDRESS_REPAIRED"
            )

            decision = {
                "status": "MATCH",
                "match": old_address_score >= ADDRESS_MATCH_THRESHOLD,
                "score": (old_address_score if address else 1.0),
                "reason": reason,
            }

        elif address:
            # Address DOM missing. Confirm this same title Place by an
            # independent title+address search before accepting.
            original_place_url = current_url

            (
                current_page,
                address_unavailable_confirmed,
                confirmed_url,
                consensus_result,
            ) = confirm_address_unavailable_by_second_search(
                current_page,
                title,
                address,
                original_place_url,
                actual_title,
                context=context,
            )

            if isinstance(consensus_result, dict):
                address_unavailable_reason = clean_value(consensus_result.get("reason"))

            if address_unavailable_confirmed:
                consensus_result = (
                    consensus_result
                    if isinstance(
                        consensus_result,
                        dict,
                    )
                    else {}
                )

                # PROMOTE THE SECOND SEARCH AS THE FINAL CANONICAL PLACE.
                #
                # The second search already proved:
                #   same Google Maps Place identity
                #   title/address match
                #
                # so we must not keep empty address / stale coordinates
                # from the first rendering attempt.
                current_url = (
                    clean_value(
                        consensus_result.get("google_maps_url")
                        or consensus_result.get("url")
                    )
                    or clean_value(confirmed_url)
                    or original_place_url
                )

                promoted_title = _best_nonempty_maps_title(
                    consensus_result.get("actual_title", ""),
                    consensus_result.get("maps_title", ""),
                    actual_title,
                )

                promoted_address = _best_nonempty_maps_address(
                    consensus_result.get("actual_address", ""),
                    consensus_result.get("maps_address", ""),
                )

                if promoted_title:
                    actual_title = promoted_title

                if promoted_address:
                    actual_address = promoted_address

                # page is now the successful second-search Place page.
                recovered_coordinates = (
                    consensus_result.get("maps_check_coordinates")
                    or consensus_result.get("coordinates")
                    or get_page_coordinates(current_page)
                    or ""
                )

                if recovered_coordinates:
                    coordinates = recovered_coordinates

                title_score = title_similarity(
                    title,
                    actual_title,
                )

                final_address_score = (
                    address_similarity(
                        address,
                        actual_address,
                    )
                    if (address and actual_address)
                    else 1.0
                )

                decision = {
                    "status": "MATCH",
                    "match": True,
                    "score": final_address_score,
                    "reason": (
                        "TITLE_AND_ADDRESS_VERIFIED"
                        if actual_address
                        else "TITLE_VERIFIED_ADDRESS_UNAVAILABLE_SAME_PLACE_CONFIRMED"
                    ),
                }

                logger.info(
                    "CONSENSUS FINAL PROMOTION | "
                    "Maps Title=%r | Maps Address=%r | "
                    "Coordinates=%r | URL=%r",
                    actual_title,
                    actual_address,
                    coordinates,
                    current_url,
                )
            else:
                decision = {
                    "status": "NEED_REVIEW",
                    "match": False,
                    "score": 0.0,
                    "reason": (address_unavailable_reason or "ADDRESS_UNAVAILABLE"),
                }

        else:
            # No Excel address and no Maps address: title + exact Place URL
            # can identify the business, but there is no address to repair.
            decision = {
                "status": "MATCH",
                "match": False,
                "score": 0.0,
                "reason": "TITLE_VERIFIED_NO_ADDRESS_AVAILABLE",
            }

    # ========================================================
    # 5. ADDRESS ANCHOR
    #
    # TITLE is weak/wrong. Use Excel address to find the correct Place,
    # then repair Excel title using the Maps title of THAT address.
    # ========================================================

    else:
        logger.info(
            "TITLE ANCHOR NOT ACCEPTED | score=%.3f | threshold=%.3f | "
            "identity_guard=%s | trying ADDRESS anchor.",
            title_score,
            TITLE_PRIMARY_STRONG_THRESHOLD,
            title_identity_guard(
                title,
                actual_title,
            ),
        )

        if not address:
            decision = {
                "status": "NEED_REVIEW",
                "match": False,
                "score": 0.0,
                "reason": "TITLE_WEAK_AND_EXCEL_ADDRESS_MISSING",
            }

        else:
            (
                address_page,
                address_url,
                address_result,
            ) = research_google_maps_from_address(
                current_page,
                title,
                address,
                context=context,
            )

            address_evidence = _extract_research_evidence(
                address_result,
                address_page,
            )

            recovered = False

            if is_google_maps_place_url(address_url):
                (
                    address_page,
                    address_opened,
                    _,
                ) = open_maps_url(
                    address_page,
                    address_url,
                )

                if address_opened:
                    try:
                        address_current_url = clean_value(address_page.url)
                    except Exception:
                        address_current_url = address_url

                    if _place_urls_consistent(
                        address_url,
                        address_current_url,
                    ):
                        close_google_popups(address_page)

                        recovered_title = _best_nonempty_maps_title(
                            extract_maps_title(address_page),
                            address_evidence.get("title", ""),
                        )

                        recovered_address = _best_nonempty_maps_address(
                            wait_for_address(
                                address_page,
                                address,
                                timeout=ADDRESS_WAIT_TIMEOUT,
                            ),
                            address_evidence.get("address", ""),
                        )

                        recovered_decision = decide_match(
                            address,
                            recovered_address,
                        )

                        if recovered_decision["status"] == "MATCH":
                            logger.info(
                                "ADDRESS ANCHOR ACCEPTED | "
                                "Excel Address=%r | Maps Address=%r | "
                                "Excel Title=%r -> Maps Title=%r | URL=%r",
                                address,
                                recovered_address,
                                title,
                                recovered_title,
                                address_current_url,
                            )

                            current_page = address_page
                            current_url = address_current_url
                            actual_title = recovered_title
                            actual_address = recovered_address
                            coordinates = (
                                get_page_coordinates(current_page) or coordinates
                            )
                            title_score = title_similarity(
                                title,
                                actual_title,
                            )

                            decision = {
                                **recovered_decision,
                                "reason": ("ADDRESS_ANCHOR_CONFIRMED_TITLE_REPAIRED"),
                            }

                            recovered = True

            if not recovered:
                # Do not keep the weak/random title candidate.
                decision = {
                    "status": "NEED_REVIEW",
                    "match": False,
                    "score": 0.0,
                    "reason": "ADDRESS_ANCHOR_NOT_VERIFIED",
                }

    # ========================================================
    # 6. FINAL SAFETY / CONSISTENCY
    # ========================================================

    decision = _final_match_invariant(
        decision,
        title,
        actual_title,
        actual_address,
        current_url,
    )

    # FINAL URL is the source of truth for coordinates.
    # This prevents stale viewport coordinates after second-search/reload.
    final_url_coordinates = _coordinates_from_maps_url(current_url)

    if final_url_coordinates:
        coordinates = final_url_coordinates

    # Never expose UI garbage as a final Maps address.
    if actual_address:
        cleaned_final_address = clean_address_candidate(actual_address)

        if (
            cleaned_final_address
            and not is_invalid_address_candidate(cleaned_final_address)
            and looks_like_address(cleaned_final_address)
        ):
            actual_address = cleaned_final_address
        else:
            actual_address = ""

    # ========================================================
    # 7. FINAL RESULT
    # ========================================================

    logger.info(
        "FINAL VERIFIED RESULT | status=%s | reason=%s | "
        "Excel Title=%r | Maps Title=%r | "
        "Excel Address=%r | Maps Address=%r | "
        "Coordinates=%r | URL=%r",
        decision["status"],
        decision["reason"],
        title,
        actual_title,
        address,
        actual_address,
        coordinates,
        current_url,
    )

    return (
        {
            "status": decision["status"],
            "maps_check_address": actual_address or "",
            "maps_check_url": current_url or "",
            "maps_address_match": bool(decision["match"]),
            "maps_check_reason": decision["reason"],
            "maps_check_coordinates": coordinates or "",
            "address_score": float(decision.get("score", 0.0) or 0.0),
            "maps_check_title": actual_title or "",
            "title_score": float(title_score or 0.0),
            "address_unavailable_confirmed": bool(address_unavailable_confirmed),
            "title_anchor_verified": bool(strong_title_anchor),
        },
        current_page,
    )


# ============================================================
# OUTPUT COLUMNS
# ============================================================

OUTPUT_COLUMNS = [
    CHECK_STATUS_COLUMN,
    CHECK_ADDRESS_COLUMN,
    CHECK_URL_COLUMN,
    ADDRESS_MATCH_COLUMN,
    CHECK_REASON_COLUMN,
    CHECK_COORDINATES_COLUMN,
    CHECK_TIME_COLUMN,
    REPAIR_OLD_TITLE_COLUMN,
    REPAIR_OLD_ADDRESS_COLUMN,
    REPAIR_NEW_TITLE_COLUMN,
    REPAIR_NEW_ADDRESS_COLUMN,
    REPAIR_STATUS_COLUMN,
    REPAIR_REASON_COLUMN,
]


def ensure_output_columns(df):
    """
    Tạo output columns.

    IMPORTANT:
    Cast sang object để tránh:

        LossySetitemError

    khi pandas column đang float64 mà ghi "".
    """

    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""

        try:
            df[column] = df[column].astype("object")
        except Exception:
            pass

    # Address / title cũng nên giữ object
    for column in (
        TITLE_COLUMN,
        ADDRESS_COLUMN,
        URL_COLUMN,
        GOOGLE_MAPS_COLUMN,
    ):
        if column in df.columns:
            try:
                df[column] = df[column].astype("object")
            except Exception:
                pass

    return df


# ============================================================
# ATOMIC SAVE
# ============================================================


def atomic_save_excel(
    df,
    output_path,
):
    """
    Save Excel atomically.

    Ghi ra .tmp.xlsx trước,
    sau đó replace output.
    """

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)

    try:
        df.to_excel(
            temp_path,
            index=False,
            engine="openpyxl",
        )

        os.replace(
            temp_path,
            output_path,
        )

    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

        raise


# ============================================================
# CONTEXT
# ============================================================


def get_search_context(page):
    try:
        return page.context
    except Exception:
        return None


# ============================================================
# MULTI-WORKER CONFIG
# ============================================================

# Browser UI is forced visible for every worker.
# Each worker gets its own Chromium window.

DEFAULT_WORKERS = max(
    1,
    int(
        os.getenv(
            "CHECK_MAPS_WORKERS",
            "4",
        )
    ),
)

ORIGINAL_INDEX_COLUMN = "__check_maps_original_index"


# ============================================================
# PROCESS ONE EXCEL FILE
# ============================================================


def process_excel_file(
    input_path,
    output_path,
    worker_id=None,
):
    """
    Process one Excel file.

    Multi-worker parent splits the original Excel into independent chunks.
    Each worker owns:
        - its own Playwright browser
        - its own DataFrame
        - its own checkpoint/output file

    Therefore workers NEVER write to the same Excel file.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    if worker_id is not None:
        logger.name = f"check_maps.W{worker_id}"

    logger.info("============================================================")
    logger.info(
        "GOOGLE MAPS CHECKER%s",
        (f" | WORKER {worker_id}" if worker_id is not None else ""),
    )
    logger.info(
        "Input : %s",
        input_path,
    )
    logger.info(
        "Output: %s",
        output_path,
    )
    logger.info("============================================================")

    # ========================================================
    # READ EXCEL
    # ========================================================

    if not input_path.exists():
        raise FileNotFoundError(f"Input Excel not found: {input_path}")

    df = pd.read_excel(input_path)

    logger.info(
        "Loaded %s rows.",
        len(df),
    )

    # ========================================================
    # REQUIRED COLUMNS
    # ========================================================

    required_columns = [
        TITLE_COLUMN,
        ADDRESS_COLUMN,
    ]

    missing_columns = [
        column for column in required_columns if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Missing required Excel columns: " + ", ".join(missing_columns)
        )

    # ========================================================
    # CREATE GOOGLE MAPS COLUMN
    # ========================================================

    if GOOGLE_MAPS_COLUMN not in df.columns:
        df[GOOGLE_MAPS_COLUMN] = ""

    df = ensure_output_columns(df)

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    from playwright.sync_api import (
        sync_playwright,
    )

    counters = {
        "MATCH": 0,
        "MISMATCH": 0,
        "NEED_REVIEW": 0,
        "OPEN_FAILED": 0,
        "SKIPPED": 0,
        "REPAIRED": 0,
        "NOT_REPAIRED": 0,
    }

    processed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            locale="vi-VN",
        )

        context.set_default_timeout(5000)
        context.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

        page = context.new_page()
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT)

        # ====================================================
        # PROCESS ROWS
        # ====================================================

        for local_position, (index, row) in enumerate(
            df.iterrows(),
            1,
        ):
            title = clean_value(
                row.get(
                    TITLE_COLUMN,
                    "",
                )
            )

            address = clean_value(
                row.get(
                    ADDRESS_COLUMN,
                    "",
                )
            )

            old_maps_url = clean_value(
                row.get(
                    GOOGLE_MAPS_COLUMN,
                    "",
                )
            )

            previous_status = clean_value(
                row.get(
                    CHECK_STATUS_COLUMN,
                    "",
                )
            ).upper()

            original_position = clean_value(
                row.get(
                    ORIGINAL_INDEX_COLUMN,
                    "",
                )
            )

            logger.info("")
            logger.info("============================================================")

            logger.info(
                "[%s/%s%s] %s",
                local_position,
                len(df),
                (f" | original={original_position}" if original_position else ""),
                title,
            )

            logger.info(
                "Address: %s",
                address,
            )

            logger.info(
                "Old Maps URL: %s",
                old_maps_url,
            )

            logger.info(
                "Previous Status: %s",
                previous_status or "(empty)",
            )

            # =================================================
            # SKIP FINAL STATUS
            # =================================================

            repair_requested = should_repair_row(row)

            if previous_status in RESUME_SKIP_STATUSES and not repair_requested:
                counters["SKIPPED"] += 1

                logger.info(
                    "SKIP FINAL | %s | %s",
                    previous_status,
                    title,
                )

                continue

            # =================================================
            # VALIDATE / CHECK
            # =================================================

            if not address:
                result = {
                    "status": "NEED_REVIEW",
                    "maps_check_address": "",
                    "maps_check_url": old_maps_url,
                    "maps_address_match": False,
                    "maps_check_reason": ("MISSING_EXCEL_ADDRESS"),
                    "maps_check_coordinates": "",
                    "address_score": 0.0,
                    "maps_check_title": "",
                    "title_score": 0.0,
                }

                current_page = page

            else:
                force_research = (
                    previous_status in FORCE_RESEARCH_STATUSES or repair_requested
                )

                try:
                    (
                        result,
                        current_page,
                    ) = check_one(
                        page,
                        title,
                        address,
                        old_maps_url,
                        context=context,
                        force_research=force_research,
                    )

                except Exception as exc:
                    logger.exception(
                        "Unhandled row error: %s",
                        exc,
                    )

                    result = {
                        "status": "NEED_REVIEW",
                        "maps_check_address": "",
                        "maps_check_url": old_maps_url,
                        "maps_address_match": False,
                        "maps_check_reason": ("UNHANDLED_EXCEPTION"),
                        "maps_check_coordinates": "",
                        "address_score": 0.0,
                        "maps_check_title": "",
                        "title_score": 0.0,
                    }

                    current_page = page

            if current_page is not None:
                page = current_page

            # =================================================
            # CANDIDATE GOOGLE MAPS URL
            #
            # IMPORTANT:
            # Do NOT write this URL to Excel yet.
            # A valid /maps/place/ URL can still belong to the WRONG hotel.
            # It is written only after MATCH / safe REPAIR below.
            # =================================================

            result_url = clean_value(
                result.get(
                    "maps_check_url",
                    "",
                )
            )

            # =================================================
            # AUTO REPAIR TITLE + ADDRESS
            # =================================================

            actual_title = clean_value(
                result.get(
                    "maps_check_title",
                    "",
                )
            )

            actual_address = clean_value(
                result.get(
                    "maps_check_address",
                    "",
                )
            )

            title_score = float(
                result.get(
                    "title_score",
                    0.0,
                )
                or 0.0
            )

            result_status = clean_value(
                result.get(
                    "status",
                    "",
                )
            ).upper()

            if repair_requested:
                if not clean_value(
                    df.at[
                        index,
                        REPAIR_OLD_TITLE_COLUMN,
                    ]
                ):
                    df.at[
                        index,
                        REPAIR_OLD_TITLE_COLUMN,
                    ] = title

                if not clean_value(
                    df.at[
                        index,
                        REPAIR_OLD_ADDRESS_COLUMN,
                    ]
                ):
                    df.at[
                        index,
                        REPAIR_OLD_ADDRESS_COLUMN,
                    ] = address

            repair_ok = False
            repair_reason = ""

            if repair_requested:
                # -------------------------------------------------
                # SAFE REPAIR RULE
                #
                # A repair is allowed only when:
                #   1. a real Maps Place URL exists
                #   2. actual title exists
                #   3. actual address exists
                #   4. identity evidence is strong enough
                # -------------------------------------------------

                # Identity policy for repairing an OLD/incorrect title:
                #
                # 1. If Maps ADDRESS matches the Excel address, that is
                #    strong identity evidence. The Maps title may replace
                #    an old/alias Excel title even when title_score is lower.
                #
                # 2. Otherwise, require a strong title score.
                #
                # This is what allows cases such as:
                #   Excel: "Mai Vy Hotel"
                #   Maps : "HANZ Mai Vy Hotel" / "Mai Vy Hotel Tây Ninh"
                # when the Maps address proves they are the same property.
                address_verified = result_status == "MATCH" or bool(
                    result.get(
                        "maps_address_match",
                        False,
                    )
                )

                identity_ok = address_verified or title_score >= REPAIR_TITLE_THRESHOLD

                address_unavailable_confirmed = bool(
                    result.get(
                        "address_unavailable_confirmed",
                        False,
                    )
                )

                title_anchor_verified = bool(
                    result.get(
                        "title_anchor_verified",
                        False,
                    )
                )

                if (
                    is_google_maps_place_url(result_url)
                    and actual_title
                    and (actual_address or address_unavailable_confirmed)
                    and identity_ok
                ):
                    df.at[
                        index,
                        TITLE_COLUMN,
                    ] = actual_title

                    if actual_address:
                        df.at[
                            index,
                            ADDRESS_COLUMN,
                        ] = actual_address
                    else:
                        # Confirmed ADDRESS_UNAVAILABLE:
                        # keep the original Excel address.
                        df.at[
                            index,
                            ADDRESS_COLUMN,
                        ] = address

                    df.at[
                        index,
                        GOOGLE_MAPS_COLUMN,
                    ] = result_url

                    df.at[
                        index,
                        REPAIR_NEW_TITLE_COLUMN,
                    ] = actual_title

                    df.at[
                        index,
                        REPAIR_NEW_ADDRESS_COLUMN,
                    ] = actual_address or address

                    df.at[
                        index,
                        REPAIR_STATUS_COLUMN,
                    ] = "REPAIRED"

                    if address_unavailable_confirmed and actual_address:
                        final_repair_reason = "TITLE_ADDRESS_URL_VERIFIED_AND_REPAIRED"

                    elif address_unavailable_confirmed:
                        final_repair_reason = "TITLE_MAPS_REPAIRED_ADDRESS_PRESERVED"

                    elif title_anchor_verified and actual_address:
                        final_repair_reason = "TITLE_VERIFIED_ADDRESS_AND_URL_REPAIRED"

                    elif address_verified and title_score < REPAIR_TITLE_THRESHOLD:
                        final_repair_reason = "ADDRESS_VERIFIED_TITLE_AND_URL_REPAIRED"

                    else:
                        final_repair_reason = "TITLE_ADDRESS_MAPS_REPAIRED"

                    df.at[
                        index,
                        REPAIR_REASON_COLUMN,
                    ] = final_repair_reason

                    result["status"] = "MATCH"
                    result["maps_address_match"] = True

                    result["maps_check_reason"] = "REPAIRED_FROM_GOOGLE_MAPS"

                    result["address_score"] = 1.0

                    repair_ok = True
                    repair_reason = final_repair_reason

                    counters["REPAIRED"] += 1

                    logger.info("AUTO REPAIR SUCCESS")

                    logger.info(
                        "Old Title   : %s",
                        title,
                    )

                    logger.info(
                        "New Title   : %s",
                        actual_title,
                    )

                    logger.info(
                        "Old Address : %s",
                        address,
                    )

                    logger.info(
                        "New Address : %s",
                        actual_address
                        or (address + " [PRESERVED: MAPS ADDRESS UNAVAILABLE]"),
                    )

                    logger.info(
                        "Title Score : %.3f",
                        title_score,
                    )

                    logger.info(
                        "Maps URL    : %s",
                        result_url,
                    )

                else:
                    if not is_google_maps_place_url(result_url):
                        repair_reason = "NO_VERIFIED_PLACE_URL"

                    elif not actual_title:
                        repair_reason = "MAPS_TITLE_UNAVAILABLE"

                    elif not actual_address and not address_unavailable_confirmed:
                        repair_reason = "MAPS_ADDRESS_UNAVAILABLE"

                    elif not identity_ok:
                        repair_reason = "PLACE_IDENTITY_NOT_STRONG_ENOUGH"

                    else:
                        repair_reason = "NOT_SAFE_TO_REPAIR"

                    df.at[
                        index,
                        REPAIR_STATUS_COLUMN,
                    ] = "NOT_REPAIRED"

                    df.at[
                        index,
                        REPAIR_REASON_COLUMN,
                    ] = repair_reason

                    counters["NOT_REPAIRED"] += 1

                    logger.warning(
                        "AUTO REPAIR SKIPPED | reason=%s | title_score=%.3f",
                        repair_reason,
                        title_score,
                    )

            # =================================================
            # COMMIT VERIFIED GOOGLE MAPS URL
            # =================================================

            final_status = clean_value(
                result.get(
                    "status",
                    "",
                )
            ).upper()

            if final_status == "MATCH" and is_google_maps_place_url(result_url):
                df.at[
                    index,
                    GOOGLE_MAPS_COLUMN,
                ] = result_url

            # On MISMATCH / NEED_REVIEW / OPEN_FAILED,
            # keep google_maps_url EMPTY for rows that started empty.
            elif not old_maps_url:
                df.at[
                    index,
                    GOOGLE_MAPS_COLUMN,
                ] = ""

            # =================================================
            # WRITE CHECK RESULT
            # =================================================

            df.at[
                index,
                CHECK_STATUS_COLUMN,
            ] = clean_value(
                result.get(
                    "status",
                    "",
                )
            )

            df.at[
                index,
                CHECK_ADDRESS_COLUMN,
            ] = clean_value(
                result.get(
                    "maps_check_address",
                    "",
                )
            )

            df.at[
                index,
                CHECK_URL_COLUMN,
            ] = clean_value(
                result.get(
                    "maps_check_url",
                    "",
                )
            )

            df.at[
                index,
                ADDRESS_MATCH_COLUMN,
            ] = bool(
                result.get(
                    "maps_address_match",
                    False,
                )
            )

            df.at[
                index,
                CHECK_REASON_COLUMN,
            ] = clean_value(
                result.get(
                    "maps_check_reason",
                    "",
                )
            )

            df.at[
                index,
                CHECK_COORDINATES_COLUMN,
            ] = clean_value(
                result.get(
                    "maps_check_coordinates",
                    "",
                )
            )

            df.at[
                index,
                CHECK_TIME_COLUMN,
            ] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            status = clean_value(
                result.get(
                    "status",
                    "",
                )
            ).upper()

            if status in counters:
                counters[status] += 1

            processed += 1

            logger.info(
                "RESULT: %s",
                status,
            )

            if repair_requested:
                logger.info(
                    "REPAIR: %s | reason=%s",
                    ("REPAIRED" if repair_ok else "NOT_REPAIRED"),
                    repair_reason,
                )

            logger.info(
                "Maps Title: %s",
                actual_title or "(NULL/EMPTY)",
            )

            logger.info(
                "Maps Address: %s",
                clean_value(
                    result.get(
                        "maps_check_address",
                        "",
                    )
                )
                or "(NULL/EMPTY)",
            )

            logger.info(
                "Reason: %s",
                clean_value(
                    result.get(
                        "maps_check_reason",
                        "",
                    )
                ),
            )

            logger.info(
                "Coordinates: %s",
                clean_value(
                    result.get(
                        "maps_check_coordinates",
                        "",
                    )
                )
                or "(none)",
            )

            if "address_score" in result:
                logger.info(
                    "Address Score: %.3f",
                    float(
                        result.get(
                            "address_score",
                            0.0,
                        )
                        or 0.0
                    ),
                )

            if "title_score" in result:
                logger.info(
                    "Title Score: %.3f",
                    float(
                        result.get(
                            "title_score",
                            0.0,
                        )
                        or 0.0
                    ),
                )

            # =================================================
            # CHECKPOINT
            # =================================================

            if processed > 0 and processed % CHECKPOINT_EVERY == 0:
                logger.info(
                    "Checkpoint save after %s processed rows...",
                    processed,
                )

                try:
                    atomic_save_excel(
                        df,
                        output_path,
                    )

                    logger.info("Checkpoint saved.")

                except Exception as exc:
                    logger.exception(
                        "Checkpoint save failed: %s",
                        exc,
                    )

        # ====================================================
        # FINAL SAVE
        # ====================================================

        logger.info("Final saving...")

        atomic_save_excel(
            df,
            output_path,
        )

        try:
            context.close()
        except Exception:
            pass

        try:
            browser.close()
        except Exception:
            pass

    logger.info("")
    logger.info("============================================================")
    logger.info(
        "WORKER FINISHED%s",
        (f" | W{worker_id}" if worker_id is not None else ""),
    )
    logger.info("============================================================")
    logger.info(
        "Rows         : %s",
        len(df),
    )
    logger.info(
        "Processed    : %s",
        processed,
    )
    logger.info(
        "Skipped      : %s",
        counters["SKIPPED"],
    )
    logger.info(
        "MATCH        : %s",
        counters["MATCH"],
    )
    logger.info(
        "MISMATCH     : %s",
        counters["MISMATCH"],
    )
    logger.info(
        "NEED_REVIEW  : %s",
        counters["NEED_REVIEW"],
    )
    logger.info(
        "OPEN_FAILED  : %s",
        counters["OPEN_FAILED"],
    )
    logger.info(
        "REPAIRED     : %s",
        counters["REPAIRED"],
    )
    logger.info(
        "NOT_REPAIRED : %s",
        counters["NOT_REPAIRED"],
    )
    logger.info(
        "Output       : %s",
        output_path,
    )
    logger.info("============================================================")

    return {
        "rows": len(df),
        "processed": processed,
        "output": str(output_path),
        **counters,
    }


# ============================================================
# SPLIT DATA FOR WORKERS
# ============================================================


def split_dataframe_for_workers(
    df,
    workers,
):
    """
    Split rows into contiguous chunks.

    Example:
        1926 rows / 4 workers
        W0 -> 482 rows
        W1 -> 482 rows
        W2 -> 481 rows
        W3 -> 481 rows
    """

    workers = max(
        1,
        min(
            int(workers),
            len(df),
        ),
    )

    total = len(df)

    base = total // workers
    remainder = total % workers

    chunks = []

    start = 0

    for worker_id in range(workers):
        size = base + (1 if worker_id < remainder else 0)

        end = start + size

        chunk = df.iloc[start:end].copy()

        chunks.append(
            (
                worker_id,
                chunk,
            )
        )

        start = end

    return chunks


# ============================================================
# MERGE WORKER OUTPUTS
# ============================================================


def merge_worker_outputs(
    worker_outputs,
    final_output,
):
    frames = []

    for worker_output in worker_outputs:
        worker_output = Path(worker_output)

        if not worker_output.exists():
            raise FileNotFoundError(f"Worker output missing: {worker_output}")

        frame = pd.read_excel(worker_output)

        frames.append(frame)

    if not frames:
        raise RuntimeError("No worker outputs to merge.")

    merged = pd.concat(
        frames,
        ignore_index=True,
    )

    if ORIGINAL_INDEX_COLUMN in merged.columns:
        merged[ORIGINAL_INDEX_COLUMN] = pd.to_numeric(
            merged[ORIGINAL_INDEX_COLUMN],
            errors="coerce",
        )

        merged = merged.sort_values(
            ORIGINAL_INDEX_COLUMN,
            kind="stable",
        )

        merged = merged.drop(
            columns=[ORIGINAL_INDEX_COLUMN],
        )

    merged = merged.reset_index(
        drop=True,
    )

    atomic_save_excel(
        merged,
        final_output,
    )

    return merged


# ============================================================
# CTRL+C / PROCESS TREE TERMINATION
# ============================================================


def terminate_process_tree(
    process,
    worker_id=None,
):
    """
    Stop a worker AND its Chromium/Playwright child processes.

    Windows:
        taskkill /PID <pid> /T /F
        /T = terminate child process tree
        /F = force termination

    This is important because each worker is launched in a separate
    terminal with its own Chromium process tree.
    """

    if process is None:
        return

    try:
        if process.poll() is not None:
            return
    except Exception:
        pass

    label = f"Worker {worker_id}" if worker_id is not None else "Worker"

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            print(f"🛑 {label} stopped (process tree terminated)")

            return

        except Exception:
            pass

    # Non-Windows / fallback
    try:
        process.terminate()
    except Exception:
        pass

    try:
        process.wait(timeout=3)
        return
    except Exception:
        pass

    try:
        process.kill()
    except Exception:
        pass


# ============================================================
# MULTI-WORKER PARENT
# ============================================================


def run_multi_worker(
    workers=DEFAULT_WORKERS,
):
    """
    Parent process - MISSING GOOGLE MAPS URL ONLY.

    FLOW:
        1. Read the full original Excel.
        2. Select ONLY rows whose google_maps_url is empty/invalid.
        3. Split ONLY those rows across workers.
        4. On Windows, open EACH worker in its OWN terminal window.
        5. Each worker writes its own chunk output.
        6. Parent waits for all worker result files.
        7. Merge repaired/checked rows back into the FULL original Excel.
        8. Rows that already had google_maps_url are never opened/searched.

    IMPORTANT:
        Existing rows with valid Google Maps Place URL are left untouched.
    """

    input_path = Path(INPUT_FILE)

    final_output = Path(OUTPUT_FILE)

    if not input_path.exists():
        raise FileNotFoundError(f"Input Excel not found: {input_path}")

    # ========================================================
    # READ FULL SOURCE
    # ========================================================

    source_df = pd.read_excel(input_path)

    if source_df.empty:
        raise ValueError("Input Excel is empty.")

    if GOOGLE_MAPS_COLUMN not in source_df.columns:
        source_df[GOOGLE_MAPS_COLUMN] = ""

    # Preserve exact original ordering / merge key.
    source_df = source_df.copy()

    source_df[ORIGINAL_INDEX_COLUMN] = list(range(len(source_df)))

    # ========================================================
    # FILTER: ONLY ROWS WITHOUT VALID GOOGLE MAPS PLACE URL
    # ========================================================

    missing_mask = source_df[GOOGLE_MAPS_COLUMN].apply(
        lambda value: not is_google_maps_place_url(clean_value(value))
    )

    work_df = source_df.loc[missing_mask].copy()

    skipped_existing = len(source_df) - len(work_df)

    # ========================================================
    # NOTHING TO DO
    # ========================================================

    if work_df.empty:
        final_df = source_df.drop(
            columns=[ORIGINAL_INDEX_COLUMN],
            errors="ignore",
        ).reset_index(drop=True)

        atomic_save_excel(
            final_df,
            final_output,
        )

        print("")
        print("=" * 75)
        print("CHECK_MAPS FINISHED")
        print("=" * 75)
        print(f"📊 Total rows       : {len(source_df)}")
        print(f"⚡ Existing Maps    : {skipped_existing}")
        print("🔎 Need workers     : 0")
        print(f"📁 Result           : {final_output}")
        print("=" * 75)

        return {
            "rows": len(source_df),
            "workers": 0,
            "processed_missing": 0,
            "skipped_existing": skipped_existing,
            "output": str(final_output),
        }

    workers = max(
        1,
        min(
            int(workers),
            len(work_df),
        ),
    )

    # ========================================================
    # SINGLE WORKER
    # ========================================================

    if workers == 1:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        run_dir = final_output.parent / f".check_maps_run_{timestamp}"

        chunks_dir = run_dir / "chunks"

        chunks_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        chunk_input = chunks_dir / "chunk_00.xlsx"

        chunk_output = chunks_dir / "chunk_00_checked.xlsx"

        atomic_save_excel(
            work_df,
            chunk_input,
        )

        process_excel_file(
            chunk_input,
            chunk_output,
            worker_id=0,
        )

        repaired_df = pd.read_excel(chunk_output)

        full_df = _merge_repaired_rows_back(
            source_df,
            repaired_df,
        )

        atomic_save_excel(
            full_df,
            final_output,
        )

        return {
            "rows": len(full_df),
            "workers": 1,
            "processed_missing": len(work_df),
            "skipped_existing": skipped_existing,
            "output": str(final_output),
            "worker_dir": str(run_dir),
        }

    # ========================================================
    # MULTI WORKER RUN DIR
    # ========================================================

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_dir = final_output.parent / f".check_maps_run_{timestamp}"

    chunks_dir = run_dir / "chunks"

    chunks_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    chunks = split_dataframe_for_workers(
        work_df,
        workers,
    )

    commands = []
    worker_outputs = []

    print("")
    print("=" * 75)
    print("CHECK_MAPS TITLE-REPAIR MISSING-ONLY MULTI-WORKER START")
    print("=" * 75)
    print(f"📊 Total rows       : {len(source_df)}")
    print(f"⚡ Existing Maps    : {skipped_existing} (SKIPPED)")
    print(f"🔎 Missing Maps     : {len(work_df)} (ONLY THESE WILL RUN)")
    print(f"👷 Workers          : {workers}")
    print(f"📂 Run dir          : {run_dir}")
    print("=" * 75)

    for (
        worker_id,
        chunk,
    ) in chunks:
        chunk_input = chunks_dir / f"chunk_{worker_id:02d}.xlsx"

        chunk_output = chunks_dir / (f"chunk_{worker_id:02d}_checked.xlsx")

        atomic_save_excel(
            chunk,
            chunk_input,
        )

        worker_outputs.append(chunk_output)

        command = [
            sys.executable,
            "-m",
            "app.check_maps",
            "--worker-mode",
            "--worker-id",
            str(worker_id),
            "--input",
            str(chunk_input),
            "--output",
            str(chunk_output),
        ]

        commands.append(
            (
                worker_id,
                command,
                chunk_output,
            )
        )

        print(f"👷 W{worker_id}: {len(chunk)} missing-url rows")

    print("=" * 75)

    # ========================================================
    # LAUNCH EACH WORKER IN ITS OWN TERMINAL
    # ========================================================

    processes = []

    started_at = time.time()

    for (
        worker_id,
        command,
        chunk_output,
    ) in commands:
        if os.name == "nt":
            # Windows:
            # CREATE_NEW_CONSOLE opens a separate terminal window
            # for EACH worker. Parent terminal only shows summary.
            creationflags = (
                subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                creationflags=creationflags,
            )

        else:
            # Non-Windows fallback:
            # separate process, same terminal environment.
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
            )

        processes.append(
            (
                worker_id,
                process,
                chunk_output,
            )
        )

        print(f"🚀 Worker {worker_id} started in separate terminal")

    # ========================================================
    # WAIT
    # ========================================================

    failed_workers = []

    try:
        # Do NOT block on process.wait() one worker at a time.
        # Poll all workers so the MAIN terminal remains responsive to Ctrl+C.
        pending = {
            worker_id: (
                process,
                chunk_output,
            )
            for (
                worker_id,
                process,
                chunk_output,
            ) in processes
        }

        while pending:
            finished_ids = []

            for (
                worker_id,
                (
                    process,
                    chunk_output,
                ),
            ) in list(pending.items()):
                return_code = process.poll()

                if return_code is None:
                    continue

                finished_ids.append(worker_id)

                if return_code != 0:
                    failed_workers.append(
                        (
                            worker_id,
                            return_code,
                        )
                    )

                    print(f"❌ Worker {worker_id} failed (exit={return_code})")

                elif not Path(chunk_output).exists():
                    failed_workers.append(
                        (
                            worker_id,
                            "NO_OUTPUT",
                        )
                    )

                    print(
                        f"❌ Worker {worker_id} finished "
                        "but output file was not created"
                    )

                else:
                    print(f"✅ Worker {worker_id} finished")

            for worker_id in finished_ids:
                pending.pop(
                    worker_id,
                    None,
                )

            if pending:
                time.sleep(0.20)

    except KeyboardInterrupt:
        print("")
        print("⛔ CTRL+C detected.")
        print("🛑 Stopping ALL worker terminals and Chromium processes...")

        for (
            worker_id,
            process,
            _,
        ) in processes:
            terminate_process_tree(
                process,
                worker_id=worker_id,
            )

        print("✅ All workers stopped.")

        # Exit cleanly instead of printing a long traceback.
        return {
            "rows": len(source_df),
            "workers": workers,
            "processed_missing": 0,
            "skipped_existing": skipped_existing,
            "cancelled": True,
            "output": str(final_output),
            "worker_dir": str(run_dir),
        }

    if failed_workers:
        raise RuntimeError(
            "Some check_maps workers failed: "
            + ", ".join(
                (f"W{wid}={code}")
                for (
                    wid,
                    code,
                ) in failed_workers
            )
        )

    parallel_elapsed = time.time() - started_at

    # ========================================================
    # MERGE WORKER OUTPUTS
    # ========================================================

    print("")
    print("🔀 MERGING WORKER RESULTS...")

    merge_started = time.time()

    repaired_frames = []

    for worker_output in worker_outputs:
        repaired_frames.append(pd.read_excel(worker_output))

    repaired_df = pd.concat(
        repaired_frames,
        ignore_index=True,
    )

    full_df = _merge_repaired_rows_back(
        source_df,
        repaired_df,
    )

    atomic_save_excel(
        full_df,
        final_output,
    )

    merge_elapsed = time.time() - merge_started

    total_elapsed = time.time() - started_at

    print("")
    print("=" * 75)
    print("CHECK_MAPS TITLE-REPAIR MISSING-ONLY MULTI-WORKER FINISHED")
    print("=" * 75)
    print(f"📊 Total rows       : {len(full_df)}")
    print(f"⚡ Existing skipped : {skipped_existing}")
    print(f"🔎 Missing processed: {len(work_df)}")
    print(f"👷 Workers          : {workers}")
    print(f"⏱️ Parallel         : {parallel_elapsed:.2f}s")
    print(f"🔀 Merge            : {merge_elapsed:.2f}s")
    print(f"⏱️ Total            : {total_elapsed:.2f}s")
    print(f"📁 Result           : {final_output}")
    print(f"📁 Worker dir       : {run_dir}")
    print("=" * 75)

    return {
        "rows": len(full_df),
        "workers": workers,
        "processed_missing": len(work_df),
        "skipped_existing": skipped_existing,
        "parallel_seconds": (parallel_elapsed),
        "merge_seconds": (merge_elapsed),
        "total_seconds": (total_elapsed),
        "output": str(final_output),
        "worker_dir": str(run_dir),
    }


def _merge_repaired_rows_back(
    source_df,
    repaired_df,
):
    """
    Merge ONLY worker-processed rows back into the full source DataFrame.

    Rows that already had a valid google_maps_url were never sent to workers
    and remain exactly as they were in the source file.
    """

    full_df = source_df.copy()

    # ========================================================
    # PANDAS STRICT-DTYPE MERGE FIX
    #
    # Worker outputs can contain:
    #   str / bool / int / float / None / NaN
    #
    # Recent pandas StringDtype refuses assignments such as:
    #   False -> string column
    #
    # Worker merge is intentionally a mixed-value operation,
    # therefore every destination column touched by repaired_df
    # is converted to object before cell-by-cell merge.
    # ========================================================

    for column in repaired_df.columns:
        if column == ORIGINAL_INDEX_COLUMN:
            continue

        if column not in full_df.columns:
            full_df[column] = pd.Series(
                [None] * len(full_df),
                dtype="object",
            )
        else:
            try:
                full_df[column] = full_df[column].astype("object")
            except Exception:
                pass

    if ORIGINAL_INDEX_COLUMN not in repaired_df.columns:
        raise ValueError(f"Worker output is missing {ORIGINAL_INDEX_COLUMN}")

    for _, repaired_row in repaired_df.iterrows():
        original_index_raw = repaired_row.get(ORIGINAL_INDEX_COLUMN)

        try:
            original_index = int(float(original_index_raw))
        except Exception:
            continue

        if original_index < 0 or original_index >= len(full_df):
            continue

        for column in repaired_df.columns:
            if column == ORIGINAL_INDEX_COLUMN:
                continue

            if column not in full_df.columns:
                full_df[column] = ""

            value = repaired_row.get(column)

            # Destination is object dtype, so strings/booleans/numbers
            # can safely coexist without pandas StringDtype errors.
            full_df.at[
                original_index,
                column,
            ] = value

    full_df = full_df.drop(
        columns=[ORIGINAL_INDEX_COLUMN],
        errors="ignore",
    )

    return full_df.reset_index(drop=True)


# ============================================================
# CLI
# ============================================================


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Google Maps checker / repair - "
            "missing google_maps_url only, "
            "with one terminal per worker"
        )
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(f"Number of parallel workers. Default: {DEFAULT_WORKERS}"),
    )

    parser.add_argument(
        "--worker-mode",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--worker-id",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--input",
        dest="input_path",
        default="",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--output",
        dest="output_path",
        default="",
        help=argparse.SUPPRESS,
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================


def main():
    args = parse_args()

    if args.worker_mode:
        if not args.input_path:
            raise ValueError("--input is required in worker mode.")

        if not args.output_path:
            raise ValueError("--output is required in worker mode.")

        try:
            return process_excel_file(
                args.input_path,
                args.output_path,
                worker_id=args.worker_id,
            )

        except KeyboardInterrupt:
            print("")
            print(f"⛔ Worker {args.worker_id}: CTRL+C detected.")
            print(f"🛑 Worker {args.worker_id}: stopping now.")

            # Clean exit. Playwright/browser child processes are also
            # terminated automatically when this worker process exits.
            raise SystemExit(130)

    try:
        return run_multi_worker(
            workers=args.workers,
        )

    except KeyboardInterrupt:
        print("")
        print("⛔ CTRL+C detected. Exiting.")
        raise SystemExit(130)


# ============================================================
# ENTRY POINT
# ============================================================


if __name__ == "__main__":
    main()
