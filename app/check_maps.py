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

# ============================================================
# check_maps.py
#
# GOOGLE MAPS ADDRESS CHECKER
#
# Flow:
#   1. Read Excel
#   2. MATCH / MISMATCH -> skip khi resume
#   3. NEED_REVIEW -> search lại Google Maps từ Excel Address
#   4. OPEN_FAILED -> retry
#   5. NO_MAPS_URL / INVALID_URL -> search lại nếu có Address
#   6. Open fresh Google Maps Place URL
#   7. Compare ONLY:
#        Excel Address <-> Google Maps Address
#   8. Coordinate chỉ dùng để debug / lưu kết quả
#
# search.py KHÔNG CẦN SỬA
# ============================================================

# ============================================================
# app/check_maps.py
#
# GOOGLE MAPS ADDRESS CHECKER
#
# Logic:
#   Excel Address
#       ↓
#   Google Maps URL
#       ↓
#   Open Google Maps Place
#       ↓
#   Extract actual Maps Address
#       ↓
#   Compare:
#       Excel Address <-> Maps Address
#
# IMPORTANT:
#   - Title KHÔNG quyết định MATCH/MISMATCH.
#   - Coordinates KHÔNG quyết định MATCH/MISMATCH.
#   - Coordinates chỉ dùng để debug.
#   - MATCH / MISMATCH là FINAL.
#   - NEED_REVIEW / OPEN_FAILED / NO_MAPS_URL /
#     INVALID_URL sẽ được research lại.
# ============================================================

import logging
import math
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

# Tối đa thời gian cố lấy address sau khi Maps mở
ADDRESS_WAIT_TIMEOUT = 18.0

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

    if normalized in INVALID_ADDRESS_TEXTS:
        return True

    if normalized.startswith("address "):
        # "Address: ..." được xử lý ở clean_address_candidate.
        return False

    if normalized.startswith("dia chi "):
        return False

    # Các text quá ngắn thường không phải address
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

    expected = normalize_text(expected_address)

    actual = normalize_text(actual_address)

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

    expected_tokens = set(meaningful_address_tokens(expected_address))

    actual_tokens = set(meaningful_address_tokens(actual_address))

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
    """
    Extract Google Maps Address bằng nhiều tầng.

    Ưu tiên:
        1. data-item-id
        2. aria-label
        3. data-tooltip
        4. data-value
        5. title
        6. innerText
        7. href
    """

    candidates = []

    # ========================================================
    # SELECTORS
    # ========================================================

    selectors = [
        # ----------------------------------------------------
        # Standard Google Maps
        # ----------------------------------------------------
        '[data-item-id="address"]',
        '[data-item-id*="address"]',
        # ----------------------------------------------------
        # aria
        # ----------------------------------------------------
        '[aria-label*="Address"]',
        '[aria-label*="address"]',
        '[aria-label*="Địa chỉ"]',
        '[aria-label*="địa chỉ"]',
        '[aria-label*="Dia chi"]',
        '[aria-label*="dia chi"]',
        # ----------------------------------------------------
        # tooltip
        # ----------------------------------------------------
        '[data-tooltip*="Address"]',
        '[data-tooltip*="address"]',
        '[data-tooltip*="Địa chỉ"]',
        '[data-tooltip*="địa chỉ"]',
        # ----------------------------------------------------
        # Main
        # ----------------------------------------------------
        '[role="main"] [data-item-id="address"]',
        '[role="main"] [data-item-id*="address"]',
        '[role="main"] [aria-label*="Address"]',
        '[role="main"] [aria-label*="address"]',
        '[role="main"] [aria-label*="Địa chỉ"]',
        '[role="main"] [aria-label*="địa chỉ"]',
        # ----------------------------------------------------
        # Button
        # ----------------------------------------------------
        'button[data-item-id="address"]',
        'button[data-item-id*="address"]',
        '[role="button"][data-item-id="address"]',
        '[role="button"][data-item-id*="address"]',
        # ----------------------------------------------------
        # Link
        # ----------------------------------------------------
        'a[data-item-id="address"]',
        'a[data-item-id*="address"]',
        # ----------------------------------------------------
        # Direction links
        # ----------------------------------------------------
        'a[href*="/maps/dir/"]',
        'a[href*="google.com/maps/dir"]',
    ]

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()

            if count <= 0:
                continue

            count = min(count, 30)

            for index in range(count):
                node = locator.nth(index)

                # ------------------------------------------------
                # innerText
                # ------------------------------------------------
                try:
                    value = node.inner_text(timeout=500)
                    if value:
                        candidates.append(value)
                except Exception:
                    pass

                # ------------------------------------------------
                # textContent
                # ------------------------------------------------
                try:
                    value = node.text_content(timeout=500)
                    if value:
                        candidates.append(value)
                except Exception:
                    pass

                # ------------------------------------------------
                # attributes
                # ------------------------------------------------
                for attribute in (
                    "aria-label",
                    "data-tooltip",
                    "data-value",
                    "title",
                ):
                    try:
                        value = node.get_attribute(attribute)
                        if value:
                            candidates.append(value)
                    except Exception:
                        pass

                # ------------------------------------------------
                # href
                # ------------------------------------------------
                try:
                    href = node.get_attribute("href")

                    if href:
                        decoded = unquote(href)

                        # Query parameter q= hoặc query=
                        match = re.search(
                            r"[?&](?:q|query)=([^&]*)",
                            decoded,
                            flags=re.IGNORECASE,
                        )

                        if match:
                            candidates.append(match.group(1))

                except Exception:
                    pass

        except Exception:
            continue

    # ========================================================
    # JAVASCRIPT DEEP SCAN
    # ========================================================

    try:
        values = page.evaluate(
            """
            () => {
                const result = [];

                const selectors = [
                    '[data-item-id="address"]',
                    '[data-item-id*="address"]',
                    '[aria-label*="Address"]',
                    '[aria-label*="address"]',
                    '[aria-label*="Địa chỉ"]',
                    '[aria-label*="địa chỉ"]',
                    '[data-tooltip*="Address"]',
                    '[data-tooltip*="address"]',
                    '[data-tooltip*="Địa chỉ"]',
                    '[data-tooltip*="địa chỉ"]'
                ];

                const nodes = document.querySelectorAll(
                    selectors.join(',')
                );

                for (const node of nodes) {

                    const attrs = [
                        node.innerText,
                        node.textContent,
                        node.getAttribute('aria-label'),
                        node.getAttribute('data-tooltip'),
                        node.getAttribute('data-value'),
                        node.getAttribute('title')
                    ];

                    for (const value of attrs) {

                        if (!value) {
                            continue;
                        }

                        const text = value
                            .replace(/\\s+/g, ' ')
                            .trim();

                        if (text) {
                            result.push(text);
                        }
                    }

                    // Parent text
                    if (node.parentElement) {

                        const parentText =
                            node.parentElement.innerText;

                        if (parentText) {
                            result.push(
                                parentText
                                    .replace(/\\s+/g, ' ')
                                    .trim()
                            );
                        }
                    }
                }

                return result;
            }
            """
        )

        if isinstance(values, list):
            candidates.extend(values)

    except Exception as exc:
        logger.debug(
            "Deep JS address scan failed: %s",
            exc,
        )

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
    """
    Extract địa chỉ thực tế từ Google Maps.

    QUAN TRỌNG:
    - Không dùng Title.
    - Không dùng coordinates.
    - Không yêu cầu address phải giống Excel mới trả về.
    - Nếu lấy được một candidate có vẻ là address -> trả về.
    - Sau đó decide_match() mới quyết định MATCH/MISMATCH.
    """

    all_candidates = []

    # ========================================================
    # 1. DOM SELECTORS
    # ========================================================

    try:
        dom_candidates = extract_dom_address_candidates(page)

        if dom_candidates:
            all_candidates.extend(dom_candidates)

    except Exception as exc:
        logger.debug(
            "DOM address extraction failed: %s",
            exc,
        )

    # ========================================================
    # 2. MAIN TEXT
    # ========================================================

    try:
        lines = get_text_lines(page)

        if lines:
            # ------------------------------------------------
            # Single lines
            # ------------------------------------------------

            for line in lines:
                cleaned = clean_address_candidate(line)

                if not cleaned:
                    continue

                if looks_like_address(cleaned):
                    all_candidates.append(cleaned)

            # ------------------------------------------------
            # 2-4 consecutive lines
            # ------------------------------------------------

            for i in range(len(lines)):
                for size in range(2, 5):
                    end = i + size

                    if end > len(lines):
                        break

                    joined = ", ".join(lines[i:end])

                    cleaned = clean_address_candidate(joined)

                    if not cleaned:
                        continue

                    if looks_like_address(cleaned):
                        all_candidates.append(cleaned)

    except Exception as exc:
        logger.debug(
            "Main text address extraction failed: %s",
            exc,
        )

    # ========================================================
    # 3. EXTRA FALLBACK:
    #    SEARCH ALL ELEMENTS WITH ADDRESS-LIKE ATTRIBUTES
    # ========================================================

    try:
        fallback_values = page.evaluate(
            """
            () => {
                const result = [];

                const nodes = document.querySelectorAll(
                    'button, a, div, span'
                );

                for (const node of nodes) {
                    const attrs = [
                        node.getAttribute('aria-label'),
                        node.getAttribute('data-item-id'),
                        node.getAttribute('data-tooltip'),
                        node.getAttribute('data-value'),
                        node.getAttribute('title')
                    ];

                    for (const value of attrs) {
                        if (!value) {
                            continue;
                        }

                        const text = value.trim();

                        if (!text) {
                            continue;
                        }

                        const lower = text.toLowerCase();

                        if (
                            lower.includes('address') ||
                            lower.includes('địa chỉ') ||
                            lower.includes('dia chi')
                        ) {
                            result.push(text);
                        }
                    }
                }

                return result;
            }
            """
        )

        if isinstance(fallback_values, list):
            all_candidates.extend(fallback_values)

    except Exception as exc:
        logger.debug(
            "Fallback DOM scan failed: %s",
            exc,
        )

    # ========================================================
    # 4. UNIQUE
    # ========================================================

    all_candidates = unique_address_candidates(all_candidates)

    if not all_candidates:
        return ""

    # ========================================================
    # 5. RANK
    # ========================================================

    best_address = ""
    best_score = -1.0

    for candidate in all_candidates:
        score = address_similarity(
            expected_address,
            candidate,
        )

        logger.debug(
            "Maps Address candidate | score=%.3f | %s",
            score,
            candidate,
        )

        if score > best_score:
            best_score = score
            best_address = candidate

    # ========================================================
    # 6. IMPORTANT
    #
    # ĐỪNG:
    #
    #     if best_score < 0.25:
    #         return ""
    #
    # Vì nếu Maps Address khác Excel thì phải trả
    # Address đó để decide_match() kết luận MISMATCH.
    # ========================================================

    if best_address:
        logger.info(
            "Maps Address candidate selected | score=%.3f | %s",
            best_score,
            best_address,
        )

        return best_address

    return ""


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
            reason = clean_value(result.get("reason")) or reason

        logger.warning(
            "Research failed: %s",
            reason,
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

    # Hết thời gian nhưng đã tìm thấy địa chỉ
    # thì vẫn trả về để decide_match() xử lý.
    return best_candidate


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
    Check một record.

    Return:
        result,
        current_page
    """

    title = clean_value(title)

    address = clean_value(address)

    maps_url = clean_value(maps_url)

    current_page = page

    # ========================================================
    # 1. FORCE RESEARCH
    # ========================================================

    if force_research:
        logger.info("Force research from Excel Address.")

        (
            current_page,
            fresh_url,
            research_result,
        ) = research_google_maps_from_address(
            current_page,
            title,
            address,
            context=context,
        )

        if not fresh_url:
            return (
                {
                    "status": "NEED_REVIEW",
                    "maps_check_address": "",
                    "maps_check_url": "",
                    "maps_address_match": False,
                    "maps_check_reason": ("RESEARCH_FROM_ADDRESS_FAILED"),
                    "maps_check_coordinates": "",
                },
                current_page,
            )

        maps_url = fresh_url

    # ========================================================
    # 2. NO MAPS URL
    # ========================================================

    if not maps_url:
        logger.info("No Maps URL. Research from Address.")

        (
            current_page,
            fresh_url,
            research_result,
        ) = research_google_maps_from_address(
            current_page,
            title,
            address,
            context=context,
        )

        if not fresh_url:
            return (
                {
                    "status": "NEED_REVIEW",
                    "maps_check_address": "",
                    "maps_check_url": "",
                    "maps_address_match": False,
                    "maps_check_reason": ("NO_MAPS_URL_AND_RESEARCH_FAILED"),
                    "maps_check_coordinates": "",
                },
                current_page,
            )

        maps_url = fresh_url

    # ========================================================
    # 3. INVALID URL
    # ========================================================

    elif not is_google_maps_place_url(maps_url):
        logger.info("Invalid Maps URL. Research from Address.")

        (
            current_page,
            fresh_url,
            research_result,
        ) = research_google_maps_from_address(
            current_page,
            title,
            address,
            context=context,
        )

        if not fresh_url:
            return (
                {
                    "status": "NEED_REVIEW",
                    "maps_check_address": "",
                    "maps_check_url": maps_url,
                    "maps_address_match": False,
                    "maps_check_reason": ("INVALID_URL_AND_RESEARCH_FAILED"),
                    "maps_check_coordinates": "",
                },
                current_page,
            )

        maps_url = fresh_url

    # ========================================================
    # 4. OPEN MAPS URL
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
                "maps_check_coordinates": (get_page_coordinates(current_page)),
            },
            current_page,
        )

    # ========================================================
    # 5. CURRENT URL
    # ========================================================

    try:
        current_url = clean_value(current_page.url)

    except Exception:
        current_url = maps_url

    if not is_google_maps_place_url(current_url):
        return (
            {
                "status": "NEED_REVIEW",
                "maps_check_address": "",
                "maps_check_url": current_url,
                "maps_address_match": False,
                "maps_check_reason": ("MAPS_URL_NOT_PLACE_AFTER_OPEN"),
                "maps_check_coordinates": (get_page_coordinates(current_page)),
            },
            current_page,
        )

    # ========================================================
    # 6. CLOSE POPUPS
    # ========================================================

    close_google_popups(current_page)

    # ========================================================
    # 7. GET COORDINATES
    #
    # DEBUG ONLY
    # ========================================================

    coordinates = get_page_coordinates(current_page)

    # ========================================================
    # 8. EXTRACT ADDRESS
    # ========================================================

    logger.info("Extracting Maps Address...")

    actual_address = wait_for_address(
        current_page,
        address,
        timeout=ADDRESS_WAIT_TIMEOUT,
    )

    # ========================================================
    # 9. ADDRESS STILL EMPTY
    #
    # Reload once and try again.
    # ========================================================

    if not actual_address and ADDRESS_RELOAD_ONCE:
        logger.warning("Maps Address is empty. Reloading page and trying again...")

        try:
            current_page.reload(
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            time.sleep(ADDRESS_RELOAD_WAIT)

            close_google_popups(current_page)

            actual_address = wait_for_address(
                current_page,
                address,
                timeout=ADDRESS_WAIT_TIMEOUT,
            )

        except Exception as exc:
            logger.warning(
                "Address reload failed: %s",
                exc,
            )

    # ========================================================
    # 10. FINAL DECISION
    # ========================================================

    decision = decide_match(
        address,
        actual_address,
    )

    # ========================================================
    # 11. IMPROVE REASON WHEN ADDRESS NOT FOUND
    # ========================================================

    reason = decision["reason"]

    if decision["status"] == "NEED_REVIEW" and not actual_address:
        reason = "MISSING_MAPS_ADDRESS_AFTER_RETRY"

    logger.info(
        "RESULT | status=%s | score=%.3f | Excel Address=%s | Maps Address=%s",
        decision["status"],
        decision["score"],
        address,
        actual_address,
    )

    return (
        {
            "status": decision["status"],
            "maps_check_address": (actual_address or ""),
            "maps_check_url": (current_url or maps_url),
            "maps_address_match": bool(decision["match"]),
            "maps_check_reason": reason,
            "maps_check_coordinates": (coordinates or ""),
            "address_score": decision["score"],
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
# MAIN
# ============================================================


def main():
    input_path = Path(INPUT_FILE)

    output_path = Path(OUTPUT_FILE)

    logger.info("============================================================")

    logger.info("GOOGLE MAPS ADDRESS CHECKER")

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

    # ========================================================
    # OUTPUT COLUMNS
    # ========================================================

    df = ensure_output_columns(df)

    # ========================================================
    # IMPORT PLAYWRIGHT
    # ========================================================

    from playwright.sync_api import (
        sync_playwright,
    )

    # ========================================================
    # COUNTERS
    # ========================================================

    counters = {
        "MATCH": 0,
        "MISMATCH": 0,
        "NEED_REVIEW": 0,
        "OPEN_FAILED": 0,
        "SKIPPED": 0,
    }

    processed = 0

    # ========================================================
    # PLAYWRIGHT
    # ========================================================

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
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

        for index, row in df.iterrows():
            row_number = index + 1

            title = clean_value(row.get(TITLE_COLUMN, ""))

            address = clean_value(row.get(ADDRESS_COLUMN, ""))

            old_maps_url = clean_value(row.get(GOOGLE_MAPS_COLUMN, ""))

            previous_status = clean_value(row.get(CHECK_STATUS_COLUMN, "")).upper()

            # =================================================
            # SKIP FINAL STATUS
            # =================================================

            if previous_status in RESUME_SKIP_STATUSES:
                counters["SKIPPED"] += 1

                logger.info(
                    "[%s/%s] SKIP FINAL | %s | %s",
                    row_number,
                    len(df),
                    previous_status,
                    title,
                )

                continue

            # =================================================
            # LOG
            # =================================================

            logger.info("")

            logger.info("============================================================")

            logger.info(
                "[%s/%s] %s",
                row_number,
                len(df),
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
            # VALIDATE ADDRESS
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
                }

                current_page = page

            else:
                # =============================================
                # FORCE RESEARCH?
                # =============================================

                force_research = previous_status in FORCE_RESEARCH_STATUSES

                # =============================================
                # CHECK
                # =============================================

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
                    }

                    current_page = page

            # =================================================
            # IMPORTANT:
            #
            # check_one() có thể trả page mới
            # =================================================

            if current_page is not None:
                page = current_page

            # =================================================
            # UPDATE GOOGLE MAPS URL
            # =================================================

            result_url = clean_value(result.get("maps_check_url", ""))

            if is_google_maps_place_url(result_url):
                df.at[
                    index,
                    GOOGLE_MAPS_COLUMN,
                ] = result_url

            # =================================================
            # WRITE RESULT
            # =================================================

            df.at[
                index,
                CHECK_STATUS_COLUMN,
            ] = clean_value(result.get("status", ""))

            df.at[
                index,
                CHECK_ADDRESS_COLUMN,
            ] = clean_value(result.get("maps_check_address", ""))

            df.at[
                index,
                CHECK_URL_COLUMN,
            ] = clean_value(result.get("maps_check_url", ""))

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
            ] = clean_value(result.get("maps_check_reason", ""))

            df.at[
                index,
                CHECK_COORDINATES_COLUMN,
            ] = clean_value(result.get("maps_check_coordinates", ""))

            df.at[
                index,
                CHECK_TIME_COLUMN,
            ] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # =================================================
            # COUNTER
            # =================================================

            status = clean_value(result.get("status", "")).upper()

            if status in counters:
                counters[status] += 1

            processed += 1

            # =================================================
            # RESULT LOG
            # =================================================

            logger.info(
                "RESULT: %s",
                status,
            )

            logger.info(
                "Maps Address: %s",
                clean_value(result.get("maps_check_address", "")) or "(NULL/EMPTY)",
            )

            logger.info(
                "Reason: %s",
                clean_value(result.get("maps_check_reason", "")),
            )

            logger.info(
                "Coordinates: %s",
                clean_value(result.get("maps_check_coordinates", "")) or "(none)",
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

        # ====================================================
        # CLOSE BROWSER
        # ====================================================

        try:
            context.close()
        except Exception:
            pass

        try:
            browser.close()
        except Exception:
            pass

    # ========================================================
    # SUMMARY
    # ========================================================

    logger.info("")

    logger.info("============================================================")

    logger.info("FINISHED")

    logger.info("============================================================")

    logger.info(
        "Total rows      : %s",
        len(df),
    )

    logger.info(
        "Processed       : %s",
        processed,
    )

    logger.info(
        "Skipped final   : %s",
        counters["SKIPPED"],
    )

    logger.info(
        "MATCH           : %s",
        counters["MATCH"],
    )

    logger.info(
        "MISMATCH        : %s",
        counters["MISMATCH"],
    )

    logger.info(
        "NEED_REVIEW     : %s",
        counters["NEED_REVIEW"],
    )

    logger.info(
        "OPEN_FAILED     : %s",
        counters["OPEN_FAILED"],
    )

    logger.info(
        "Output          : %s",
        output_path,
    )

    logger.info("============================================================")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
