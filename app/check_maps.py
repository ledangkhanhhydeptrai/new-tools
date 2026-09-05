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
# ============================================================
# app/check_maps.py
# ============================================================

import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

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
# CONFIG
# ============================================================

from config import (
    HEADLESS,
    INPUT_FILE,
    OUTPUT_FILE,
    PAGE_TIMEOUT,
)


# ============================================================
# LOCAL CONFIG
# ============================================================

NAVIGATION_TIMEOUT = 30_000
WAIT_AFTER_LOAD = 2.0

RETRY_COUNT = 3
CHECKPOINT_EVERY = 10

CHECK_ONLY_WITH_MAPS_URL = True

ADDRESS_MATCH_THRESHOLD = 0.55


# ============================================================
# EXCEL COLUMNS
# ============================================================

TITLE_COLUMN = "Title"
ADDRESS_COLUMN = "Address"
URL_COLUMN = "URL"
GOOGLE_MAPS_COLUMN = "google_maps_url"


# ============================================================
# OUTPUT COLUMNS
# ============================================================

CHECK_STATUS_COLUMN = "maps_check_status"
CHECK_ADDRESS_COLUMN = "maps_check_address"
CHECK_URL_COLUMN = "maps_check_url"
ADDRESS_MATCH_COLUMN = "maps_address_match"
CHECK_REASON_COLUMN = "maps_check_reason"
CHECK_COORDINATES_COLUMN = "maps_check_coordinates"
CHECK_TIME_COLUMN = "maps_check_time"


# ============================================================
# LOGGING
# ============================================================

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

LOG_FILE = LOG_DIR / "check_maps.log"

logging.basicConfig(
    level=logging.INFO,
    format=("%(asctime)s | %(levelname)s | %(message)s"),
    handlers=[
        logging.FileHandler(
            LOG_FILE,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================


def clean_value(value):
    """
    Convert Excel / pandas values into safe strings.
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


# ============================================================
# TEXT NORMALIZATION
# ============================================================


def normalize_text(value):
    """
    Normalize text for address comparison.

    Handles:
    - URL encoded text
    - accents
    - lowercase
    - invisible unicode characters
    - punctuation
    - duplicated whitespace
    """

    text = clean_value(value)

    if not text:
        return ""

    try:
        text = unquote(text)
    except Exception:
        pass

    # Remove private-use / control / format chars.
    cleaned = []

    for char in text:
        category = unicodedata.category(char)

        if category in {
            "Co",
            "Cc",
            "Cf",
        }:
            continue

        cleaned.append(char)

    text = "".join(cleaned)

    # Remove accents.
    text = unicodedata.normalize(
        "NFKD",
        text,
    )

    text = "".join(char for char in text if not unicodedata.combining(char))

    text = text.lower()

    # Keep only ascii alphanumeric + whitespace.
    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text,
    )

    # Collapse whitespace.
    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


# ============================================================
# INVALID EXTRACTED TEXT
# ============================================================


def is_invalid_extracted_text(value):
    """
    Detect empty / garbage / icon-like extracted strings.
    """

    text = clean_value(value)

    if not text:
        return True

    # Private-use unicode.
    for char in text:
        if unicodedata.category(char) == "Co":
            return True

    normalized = normalize_text(text)

    if not normalized:
        return True

    if len(normalized) < 3:
        return True

    garbage_values = {
        "address",
        "dia chi",
        "copy address",
        "sao chep dia chi",
        "copy",
        "chep",
    }

    if normalized in garbage_values:
        return True

    return False


# ============================================================
# GOOGLE MAPS URL
# ============================================================


def is_google_maps_url(url):
    """
    Check whether URL belongs to Google Maps.
    """

    value = clean_value(url)

    if not value:
        return False

    lowered = value.lower()

    return "google.com/maps" in lowered or "maps.google.com" in lowered


def is_google_maps_place_url(url):
    """
    Only a Google Maps Place URL counts as a valid result.

    Generic /maps/search URLs are NOT accepted.
    """

    value = clean_value(url)

    if not is_google_maps_url(value):
        return False

    lowered = value.lower()

    return "/maps/place/" in lowered


# ============================================================
# COORDINATES
# ============================================================


def extract_coordinates(url):
    """
    Extract coordinates from Google Maps URL.

    Supports:
        !3dLAT!4dLNG
        @LAT,LNG
    """

    value = clean_value(url)

    if not value:
        return None

    # --------------------------------------------------------
    # !3dLAT!4dLNG
    # --------------------------------------------------------

    match = re.search(
        r"!3d(-?\d+(?:\.\d+)?)"
        r"!4d(-?\d+(?:\.\d+)?)",
        value,
    )

    if match:
        try:
            lat = float(match.group(1))
            lng = float(match.group(2))

            return {
                "lat": lat,
                "lng": lng,
            }
        except Exception:
            pass

    # --------------------------------------------------------
    # @LAT,LNG
    # --------------------------------------------------------

    match = re.search(
        r"@(-?\d+(?:\.\d+)?),"
        r"(-?\d+(?:\.\d+)?)",
        value,
    )

    if match:
        try:
            lat = float(match.group(1))
            lng = float(match.group(2))

            return {
                "lat": lat,
                "lng": lng,
            }
        except Exception:
            pass

    return None


# ============================================================
# BODY TEXT
# ============================================================


def get_body_text(page):
    """
    Safely get Google Maps body text.
    """

    try:
        return page.locator("body").inner_text(
            timeout=5_000,
        )
    except Exception:
        return ""


# ============================================================
# ADDRESS SELECTORS
# ============================================================

ADDRESS_SELECTORS = [
    "button[data-item-id='address']",
    "button[data-item-id^='address']",
    "div[data-item-id='address']",
    "div[data-item-id^='address']",
    "a[data-item-id='address']",
    "a[data-item-id^='address']",
    "[role='button'][data-item-id='address']",
    "[role='button'][data-item-id^='address']",
    "button[aria-label*='Address']",
    "button[aria-label*='Địa chỉ']",
    "[aria-label*='Address']",
    "[aria-label*='Địa chỉ']",
    "[data-tooltip*='Address']",
    "[data-tooltip*='Địa chỉ']",
    ".Io6YTe",
    "[jsaction*='address']",
]


# ============================================================
# ADDRESS CANDIDATES
# ============================================================


def collect_address_candidates(page):
    """
    Collect possible Google Maps address strings.
    """

    candidates = []

    for selector in ADDRESS_SELECTORS:
        try:
            locator = page.locator(selector)

            count = locator.count()

        except Exception:
            continue

        # Prevent excessive DOM scanning.
        count = min(
            count,
            10,
        )

        for index in range(count):
            element = locator.nth(index)

            values = []

            # inner text
            try:
                values.append(
                    element.inner_text(
                        timeout=1_500,
                    )
                )
            except Exception:
                pass

            # text content
            try:
                values.append(
                    element.text_content(
                        timeout=1_500,
                    )
                )
            except Exception:
                pass

            # aria-label
            try:
                values.append(element.get_attribute("aria-label"))
            except Exception:
                pass

            # data-tooltip
            try:
                values.append(element.get_attribute("data-tooltip"))
            except Exception:
                pass

            for value in values:
                value = clean_value(value)

                if is_invalid_extracted_text(value):
                    continue

                normalized = normalize_text(value)

                # Ignore labels.
                if normalized in {
                    "address",
                    "dia chi",
                    "copy address",
                    "sao chep dia chi",
                }:
                    continue

                if value not in candidates:
                    candidates.append(value)

    return candidates


# ============================================================
# ADDRESS TOKEN SCORE
# ============================================================


def address_similarity(
    input_address,
    actual_address,
):
    """
    Address similarity based on normalized text.

    Priority:
        exact
        containment
        token F1
    """

    expected = normalize_text(input_address)

    actual = normalize_text(actual_address)

    if not expected or not actual:
        return 0.0

    # Exact.
    if expected == actual:
        return 1.0

    # Containment.
    if expected in actual or actual in expected:
        return 0.90

    expected_tokens = set(expected.split())

    actual_tokens = set(actual.split())

    if len(expected_tokens) < 2 or len(actual_tokens) < 2:
        return 0.0

    intersection = expected_tokens & actual_tokens

    if not intersection:
        return 0.0

    precision = len(intersection) / len(actual_tokens)

    recall = len(intersection) / len(expected_tokens)

    if precision + recall == 0:
        return 0.0

    f1 = 2 * precision * recall / (precision + recall)

    return round(
        f1,
        4,
    )


# ============================================================
# EXTRACT ADDRESS
# ============================================================


def extract_address(
    page,
    expected_address="",
):
    """
    Extract the most likely address from Google Maps.
    """

    candidates = collect_address_candidates(page)

    if not candidates:
        return ""

    # --------------------------------------------------------
    # If we know expected address,
    # choose candidate with highest similarity.
    # --------------------------------------------------------

    expected = clean_value(expected_address)

    if expected:
        scored = []

        for candidate in candidates:
            score = address_similarity(
                expected,
                candidate,
            )

            scored.append(
                (
                    score,
                    candidate,
                )
            )

        scored.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        if scored:
            best_score, best_candidate = scored[0]

            if best_score > 0:
                return best_candidate

    # --------------------------------------------------------
    # Fallback:
    # prefer candidates containing numbers.
    # --------------------------------------------------------

    for candidate in candidates:
        if re.search(
            r"\d",
            candidate,
        ):
            return candidate

    return candidates[0]


# ============================================================
# WAIT FOR ADDRESS
# ============================================================


def wait_for_address(
    page,
    expected_address,
    timeout_seconds=5.0,
):
    """
    Wait until Google Maps exposes an address.
    """

    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        actual_address = extract_address(
            page,
            expected_address,
        )

        if not is_invalid_extracted_text(actual_address):
            return actual_address

        time.sleep(0.5)

    return ""


# ============================================================
# BODY ADDRESS FALLBACK
# ============================================================


def extract_address_from_body(
    page,
    input_address,
):
    """
    Fallback address extraction from body text.

    Only accepts lines having meaningful
    token overlap with the Excel address.
    """

    body = get_body_text(page)

    if not body:
        return ""

    expected = normalize_text(input_address)

    if not expected:
        return ""

    expected_tokens = set(expected.split())

    if len(expected_tokens) < 2:
        return ""

    best_candidate = ""
    best_score = 0.0

    for line in body.splitlines():
        line = clean_value(line)

        if is_invalid_extracted_text(line):
            continue

        normalized = normalize_text(line)

        if not normalized:
            continue

        actual_tokens = set(normalized.split())

        if len(actual_tokens) < 2:
            continue

        overlap = expected_tokens & actual_tokens

        if not overlap:
            continue

        score = len(overlap) / max(
            len(expected_tokens),
            1,
        )

        if score > best_score:
            best_score = score
            best_candidate = line

    if best_score >= 0.40:
        return best_candidate

    return ""


# ============================================================
# DECIDE ADDRESS MATCH
# ============================================================


def decide_match(
    expected_address,
    actual_address,
):
    """
    ADDRESS-ONLY decision.

    Title is completely ignored.
    Coordinates are completely ignored.
    """

    expected = normalize_text(expected_address)

    actual = normalize_text(actual_address)

    # --------------------------------------------------------
    # Missing Excel address.
    # --------------------------------------------------------

    if not expected:
        return {
            "status": "NEED_REVIEW",
            "address_match": False,
            "reason": "MISSING_EXCEL_ADDRESS",
            "address_score": 0.0,
        }

    # --------------------------------------------------------
    # Missing Google Maps address.
    # --------------------------------------------------------

    if not actual or is_invalid_extracted_text(actual_address):
        return {
            "status": "NEED_REVIEW",
            "address_match": False,
            "reason": "MISSING_MAPS_ADDRESS",
            "address_score": 0.0,
        }

    # --------------------------------------------------------
    # Compare.
    # --------------------------------------------------------

    score = address_similarity(
        expected_address,
        actual_address,
    )

    if score >= ADDRESS_MATCH_THRESHOLD:
        return {
            "status": "MATCH",
            "address_match": True,
            "reason": "ADDRESS_MATCH",
            "address_score": score,
        }

    return {
        "status": "MISMATCH",
        "address_match": False,
        "reason": "ADDRESS_MISMATCH",
        "address_score": score,
    }


# ============================================================
# GOOGLE POPUPS
# ============================================================


def close_google_popups(page):
    """
    Close common Google consent / popup buttons.
    """

    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Chấp nhận tất cả')",
        "button:has-text('I agree')",
        "button:has-text('Tôi đồng ý')",
        "button:has-text('Close')",
        "button:has-text('Đóng')",
        "[aria-label='Close']",
        "[aria-label='Đóng']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = locator.count()

            if count <= 0:
                continue

            for index in range(min(count, 3)):
                button = locator.nth(index)

                try:
                    if button.is_visible(timeout=500):
                        button.click(timeout=1_500)
                        time.sleep(0.5)

                except Exception:
                    pass

        except Exception:
            pass


# ============================================================
# AW SNAP
# ============================================================


def is_aw_snap(page):
    """
    Detect Chrome 'Aw, Snap!' page.
    """

    try:
        title = clean_value(page.title())

        if "Aw, Snap" in title:
            return True

    except Exception:
        pass

    try:
        body = get_body_text(page)

        if "Aw, Snap" in body:
            return True

        if "Aw, Snap!" in body:
            return True

        if "Aww Snap" in body or "Aww, Snap" in body:
            return True

    except Exception:
        pass

    return False


# ============================================================
# RECOVER PAGE
# ============================================================


def recover_page(page):
    """
    Recover Chrome / Playwright page after Aw Snap.

    First tries reload.
    If that fails, opens a fresh page.
    """

    logger.warning("Aw, Snap detected. Attempting recovery...")

    # --------------------------------------------------------
    # Try reload.
    # --------------------------------------------------------

    try:
        page.reload(
            wait_until="domcontentloaded",
            timeout=15_000,
        )

        time.sleep(1.5)

        if not is_aw_snap(page):
            logger.info("Aw, Snap recovered by reload.")
            return page

    except Exception as exc:
        logger.warning(
            "Reload recovery failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Create new page.
    # --------------------------------------------------------

    try:
        context = page.context

        new_page = context.new_page()

        try:
            page.close()
        except Exception:
            pass

        logger.info("Created a fresh page after Aw, Snap.")

        return new_page

    except Exception as exc:
        logger.error(
            "Failed to create recovery page: %s",
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
    Open a Google Maps Place URL.

    Returns:
        page,
        success,
        reason
    """

    target_url = clean_value(url)

    if not target_url:
        return (
            page,
            False,
            "EMPTY_GOOGLE_MAPS_URL",
        )

    if not is_google_maps_url(target_url):
        return (
            page,
            False,
            "INVALID_GOOGLE_MAPS_URL",
        )

    if not is_google_maps_place_url(target_url):
        return (
            page,
            False,
            "NOT_GOOGLE_MAPS_PLACE_URL",
        )

    current_page = page

    for attempt in range(
        1,
        RETRY_COUNT + 1,
    ):
        logger.info(
            "Opening Maps URL | attempt=%s/%s",
            attempt,
            RETRY_COUNT,
        )

        # ----------------------------------------------------
        # Aw Snap before navigation.
        # ----------------------------------------------------

        if is_aw_snap(current_page):
            current_page = recover_page(current_page)

        try:
            current_page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            time.sleep(WAIT_AFTER_LOAD)

            close_google_popups(current_page)

            # ------------------------------------------------
            # Aw Snap after navigation.
            # ------------------------------------------------

            if is_aw_snap(current_page):
                logger.warning("Aw, Snap after navigation.")

                current_page = recover_page(current_page)

                continue

            actual_url = clean_value(current_page.url)

            logger.info(
                "Current Maps URL: %s",
                actual_url,
            )

            # ------------------------------------------------
            # IMPORTANT:
            # only /maps/place/ counts.
            # ------------------------------------------------

            if is_google_maps_place_url(actual_url):
                logger.info("Valid Google Maps Place URL opened.")

                return (
                    current_page,
                    True,
                    "OPEN_SUCCESS",
                )

            logger.warning("Opened URL is not a Maps Place URL.")

        except PlaywrightTimeoutError as exc:
            logger.warning(
                "Maps navigation timeout | attempt=%s/%s | %s",
                attempt,
                RETRY_COUNT,
                exc,
            )

        except Exception as exc:
            logger.warning(
                "Maps navigation error | attempt=%s/%s | %s",
                attempt,
                RETRY_COUNT,
                exc,
            )

            if is_aw_snap(current_page):
                current_page = recover_page(current_page)

        # ----------------------------------------------------
        # Retry with reload.
        # ----------------------------------------------------

        if attempt < RETRY_COUNT:
            try:
                current_page.reload(
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )

                time.sleep(1.0)

            except Exception:
                pass

    return (
        current_page,
        False,
        "URL_IS_PLACE_BUT_NAVIGATION_FAILED",
    )


# ============================================================
# CHECK ONE
# ============================================================


def check_one(
    page,
    address,
    maps_url,
):
    """
    Check one Google Maps URL.

    IMPORTANT:
        Title is NOT checked.

    Only:
        Excel Address
        vs
        Google Maps Address
    """

    result = {
        "status": "",
        "title": "",
        "address": "",
        "url": "",
        "title_match": None,
        "address_match": None,
        "reason": "",
        "coordinates": None,
    }

    address = clean_value(address)

    maps_url = clean_value(maps_url)

    # ========================================================
    # Validate URL
    # ========================================================

    if not maps_url:
        result["status"] = "NO_MAPS_URL"

        result["reason"] = "EMPTY_GOOGLE_MAPS_URL"

        return result

    if not is_google_maps_url(maps_url):
        result["status"] = "INVALID_URL"

        result["reason"] = "INVALID_GOOGLE_MAPS_URL"

        result["url"] = maps_url

        return result

    if not is_google_maps_place_url(maps_url):
        result["status"] = "INVALID_URL"

        result["reason"] = "NOT_GOOGLE_MAPS_PLACE_URL"

        result["url"] = maps_url

        return result

    # ========================================================
    # Open URL
    # ========================================================

    page, opened, open_reason = open_maps_url(
        page,
        maps_url,
    )

    if not opened:
        result["status"] = "OPEN_FAILED"

        result["reason"] = open_reason

        result["url"] = clean_value(page.url)

        return result

    # ========================================================
    # Actual URL
    # ========================================================

    actual_url = clean_value(page.url)

    result["url"] = actual_url or maps_url

    # ========================================================
    # Coordinates
    #
    # Stored for investigation only.
    # NOT used for MATCH/MISMATCH.
    # ========================================================

    result["coordinates"] = extract_coordinates(actual_url) or extract_coordinates(
        maps_url
    )

    # ========================================================
    # Ignore title completely.
    # ========================================================

    result["title"] = ""

    result["title_match"] = None

    # ========================================================
    # Extract address
    # ========================================================

    actual_address = wait_for_address(
        page,
        address,
        timeout_seconds=5.0,
    )

    # ========================================================
    # Body fallback
    # ========================================================

    if is_invalid_extracted_text(actual_address):
        actual_address = extract_address_from_body(
            page,
            address,
        )

    result["address"] = clean_value(actual_address)

    # ========================================================
    # ADDRESS ONLY DECISION
    # ========================================================

    decision = decide_match(
        address,
        actual_address,
    )

    result["status"] = decision["status"]

    result["address_match"] = decision["address_match"]

    result["reason"] = decision["reason"]

    logger.info(
        "Address check | score=%.4f | status=%s | reason=%s",
        decision["address_score"],
        decision["status"],
        decision["reason"],
    )

    return result


# ============================================================
# ATOMIC EXCEL SAVE
# ============================================================


def save_excel_atomic(
    df,
    output_file,
):
    """
    Save Excel safely using temporary file
    and os.replace().
    """

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = output_path.with_suffix(".tmp.xlsx")

    try:
        df.to_excel(
            temp_path,
            index=False,
        )

        os.replace(
            temp_path,
            output_path,
        )

        logger.info(
            "Excel saved: %s",
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
# INITIALIZE OUTPUT COLUMNS
# ============================================================


def initialize_output_columns(
    df,
):
    """
    Create output columns and force them to object dtype.

    This fixes:
        LossySetitemError
        Invalid value '' for dtype float64
    """

    output_columns = [
        CHECK_STATUS_COLUMN,
        CHECK_ADDRESS_COLUMN,
        CHECK_URL_COLUMN,
        ADDRESS_MATCH_COLUMN,
        CHECK_REASON_COLUMN,
        CHECK_COORDINATES_COLUMN,
        CHECK_TIME_COLUMN,
    ]

    for column in output_columns:
        if column not in df.columns:
            df[column] = pd.Series(
                index=df.index,
                dtype="object",
            )

        else:
            # ------------------------------------------------
            # Critical fix:
            # Excel may load an all-NaN column as float64.
            # ------------------------------------------------

            df[column] = df[column].astype("object")

    return df


# ============================================================
# MAIN
# ============================================================


def main():

    logger.info("=" * 70)

    logger.info("GOOGLE MAPS ADDRESS CHECKER")

    logger.info("=" * 70)

    logger.info(
        "INPUT_FILE  = %s",
        INPUT_FILE,
    )

    logger.info(
        "OUTPUT_FILE = %s",
        OUTPUT_FILE,
    )

    # ========================================================
    # Validate input
    # ========================================================

    if not Path(INPUT_FILE).exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    # ========================================================
    # READ EXCEL / RESUME
    # ========================================================

    if Path(OUTPUT_FILE).exists():
        logger.info("Existing output found.")

        logger.info(
            "RESUME from: %s",
            OUTPUT_FILE,
        )

        df = pd.read_excel(OUTPUT_FILE)

    else:
        logger.info("No existing output.")

        logger.info(
            "Loading input: %s",
            INPUT_FILE,
        )

        df = pd.read_excel(INPUT_FILE)

    logger.info(
        "Total rows: %s",
        len(df),
    )

    # ========================================================
    # REQUIRED COLUMNS
    # ========================================================

    required_columns = [
        ADDRESS_COLUMN,
        GOOGLE_MAPS_COLUMN,
    ]

    missing_columns = [
        column for column in required_columns if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")

    # ========================================================
    # INITIALIZE OUTPUT COLUMNS
    # ========================================================

    df = initialize_output_columns(df)

    # ========================================================
    # COUNTERS
    # ========================================================

    total = len(df)

    processed = 0
    skipped = 0

    matched = 0
    mismatched = 0
    need_review = 0

    open_failed = 0
    invalid_url = 0
    no_maps_url = 0

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    from playwright.sync_api import (
        sync_playwright,
    )

    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        try:
            # ------------------------------------------------
            # Launch browser
            # ------------------------------------------------

            browser = p.chromium.launch(headless=HEADLESS)

            context = browser.new_context(
                viewport={
                    "width": 1280,
                    "height": 900,
                },
                locale="vi-VN",
                timezone_id=("Asia/Ho_Chi_Minh"),
            )

            page = context.new_page()

            page.set_default_timeout(PAGE_TIMEOUT)

            # ------------------------------------------------
            # Main loop
            # ------------------------------------------------

            for position, index in enumerate(
                df.index,
                start=1,
            ):
                title = ""

                if TITLE_COLUMN in df.columns:
                    title = clean_value(
                        df.at[
                            index,
                            TITLE_COLUMN,
                        ]
                    )

                address = clean_value(
                    df.at[
                        index,
                        ADDRESS_COLUMN,
                    ]
                )

                maps_url = clean_value(
                    df.at[
                        index,
                        GOOGLE_MAPS_COLUMN,
                    ]
                )

                print()
                print("=" * 70)

                print(f"[{position}/{total}] {title}")

                print(f"📍 {address}")

                print(f"🗺️ {maps_url}")

                # ====================================================
                # RESUME SKIP
                # ====================================================

                existing_status = clean_value(
                    df.at[
                        index,
                        CHECK_STATUS_COLUMN,
                    ]
                )

                if existing_status in {
                    "MATCH",
                    "MISMATCH",
                    "NEED_REVIEW",
                    "OPEN_FAILED",
                    "INVALID_URL",
                    "NO_MAPS_URL",
                }:
                    skipped += 1

                    logger.info(
                        "SKIP already checked | row=%s | status=%s",
                        position,
                        existing_status,
                    )

                    print(f"⏭️ SKIP | already checked: {existing_status}")

                    continue

                # ====================================================
                # NO MAPS URL
                # ====================================================

                if CHECK_ONLY_WITH_MAPS_URL and not maps_url:
                    now = datetime.now().isoformat(timespec="seconds")

                    df.at[
                        index,
                        CHECK_STATUS_COLUMN,
                    ] = "NO_MAPS_URL"

                    df.at[
                        index,
                        CHECK_ADDRESS_COLUMN,
                    ] = ""

                    df.at[
                        index,
                        CHECK_URL_COLUMN,
                    ] = ""

                    df.at[
                        index,
                        ADDRESS_MATCH_COLUMN,
                    ] = False

                    df.at[
                        index,
                        CHECK_REASON_COLUMN,
                    ] = "EMPTY_GOOGLE_MAPS_URL"

                    df.at[
                        index,
                        CHECK_COORDINATES_COLUMN,
                    ] = None

                    df.at[
                        index,
                        CHECK_TIME_COLUMN,
                    ] = now

                    no_maps_url += 1
                    processed += 1

                    print("⚠️ NO GOOGLE MAPS URL")

                    # ------------------------------------------------
                    # Checkpoint
                    # ------------------------------------------------

                    if processed % CHECKPOINT_EVERY == 0:
                        save_excel_atomic(
                            df,
                            OUTPUT_FILE,
                        )

                    continue

                # ====================================================
                # CHECK
                # ====================================================

                try:
                    result = check_one(
                        page,
                        address,
                        maps_url,
                    )

                    # ------------------------------------------------
                    # Store results
                    # ------------------------------------------------

                    df.at[
                        index,
                        CHECK_STATUS_COLUMN,
                    ] = result.get(
                        "status",
                        "",
                    )

                    df.at[
                        index,
                        CHECK_ADDRESS_COLUMN,
                    ] = result.get(
                        "address",
                        "",
                    )

                    df.at[
                        index,
                        CHECK_URL_COLUMN,
                    ] = result.get(
                        "url",
                        "",
                    )

                    df.at[
                        index,
                        ADDRESS_MATCH_COLUMN,
                    ] = result.get(
                        "address_match",
                        None,
                    )

                    df.at[
                        index,
                        CHECK_REASON_COLUMN,
                    ] = result.get(
                        "reason",
                        "",
                    )

                    coordinates = result.get("coordinates")

                    if coordinates:
                        df.at[
                            index,
                            CHECK_COORDINATES_COLUMN,
                        ] = f"{coordinates['lat']},{coordinates['lng']}"

                    else:
                        df.at[
                            index,
                            CHECK_COORDINATES_COLUMN,
                        ] = ""

                    df.at[
                        index,
                        CHECK_TIME_COLUMN,
                    ] = datetime.now().isoformat(timespec="seconds")

                    processed += 1

                    # ------------------------------------------------
                    # Counters
                    # ------------------------------------------------

                    status = result.get(
                        "status",
                        "",
                    )

                    if status == "MATCH":
                        matched += 1

                    elif status == "MISMATCH":
                        mismatched += 1

                    elif status == "NEED_REVIEW":
                        need_review += 1

                    elif status == "OPEN_FAILED":
                        open_failed += 1

                    elif status == "INVALID_URL":
                        invalid_url += 1

                    elif status == "NO_MAPS_URL":
                        no_maps_url += 1

                    # ------------------------------------------------
                    # Console output
                    # ------------------------------------------------

                    print(f"📍 Maps address: {result.get('address', '')}")

                    print(f"📌 Status: {result.get('status', '')}")

                    print(f"💡 Reason: {result.get('reason', '')}")

                    if result.get("coordinates"):
                        print(f"🌐 Coordinates: {result['coordinates']}")

                    # ------------------------------------------------
                    # Checkpoint
                    # ------------------------------------------------

                    if processed % CHECKPOINT_EVERY == 0:
                        logger.info(
                            "CHECKPOINT | "
                            "processed=%s | "
                            "matched=%s | "
                            "mismatched=%s | "
                            "review=%s",
                            processed,
                            matched,
                            mismatched,
                            need_review,
                        )

                        save_excel_atomic(
                            df,
                            OUTPUT_FILE,
                        )

                except Exception:
                    logger.exception(
                        "Unexpected error at row %s",
                        position,
                    )

                    # ------------------------------------------------
                    # IMPORTANT:
                    # Save current progress even if this row crashes.
                    # ------------------------------------------------

                    try:
                        save_excel_atomic(
                            df,
                            OUTPUT_FILE,
                        )

                    except Exception:
                        logger.exception("Failed to save checkpoint after error.")

                    print("💥 ERROR: Unexpected error. Progress has been saved.")

                    # ------------------------------------------------
                    # Continue next row instead of killing
                    # entire 1900+ row job.
                    # ------------------------------------------------

                    continue

            # ========================================================
            # FINAL SAVE
            # ========================================================

            save_excel_atomic(
                df,
                OUTPUT_FILE,
            )

        finally:
            # ========================================================
            # CLOSE PLAYWRIGHT
            # ========================================================

            try:
                if page is not None:
                    page.close()

            except Exception:
                pass

            try:
                if context is not None:
                    context.close()

            except Exception:
                pass

            try:
                if browser is not None:
                    browser.close()

            except Exception:
                pass

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 70)

    print("✅ GOOGLE MAPS CHECK FINISHED")

    print("=" * 70)

    print(f"Total rows      : {total}")

    print(f"Processed       : {processed}")

    print(f"Skipped         : {skipped}")

    print(f"MATCH           : {matched}")

    print(f"MISMATCH        : {mismatched}")

    print(f"NEED_REVIEW     : {need_review}")

    print(f"OPEN_FAILED     : {open_failed}")

    print(f"INVALID_URL     : {invalid_url}")

    print(f"NO_MAPS_URL     : {no_maps_url}")

    print()
    print(f"📁 Output: {OUTPUT_FILE}")

    print(f"📄 Log:    {LOG_FILE}")

    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print("🛑 Interrupted by user.")

        logger.warning("Process interrupted by user.")

    except Exception as exc:
        logger.exception("Fatal error.")

        print()
        print(f"💥 ERROR: {exc}")

        raise
