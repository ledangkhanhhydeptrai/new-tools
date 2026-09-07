# # ============================================================
# # app/search.py
# # ADDRESS-FIRST + TITLE-FALLBACK GOOGLE MAPS SEARCH V10
# #
# # Goals:
# #   1. Address-first
# #   2. Prevent province/location mismatch
# #   3. Only accept real Google Maps /maps/place/ URLs
# #   4. Minimize Playwright DOM operations
# #   5. Max 3 search variants
# #   6. Max 3 candidates to verify
# #   7. Coordinates from URL first
# #   8. Deep DOM extraction only when necessary
# # ============================================================

# import re
# import time
# import unicodedata
# from difflib import SequenceMatcher
# from urllib.parse import quote, unquote, urlparse

# from config import (
#     SEARCH_POLL_INTERVAL,
#     SEARCH_POLL_COUNT,
#     FINAL_SEARCH_CHECK_DELAY,
#     PAGE_TIMEOUT,
# )

# from .recovery import safe_goto
# from .utils import (
#     safe_text,
#     clean_google_maps_url,
#     build_google_maps_search_url,
# )


# # ============================================================
# # PERFORMANCE CONFIG
# # ============================================================

# # IMPORTANT:
# # Keep these small for 1000+ records.

# SELECTED_PLACE_RETRIES = 1

# SELECTED_PLACE_RETRY_DELAY = min(
#     max(float(SEARCH_POLL_INTERVAL or 0.15), 0.15),
#     0.4,
# )

# # Search
# MAX_SEARCH_VARIANTS = 3
# MAX_TITLE_SEARCH_VARIANTS = 3
# MAX_TITLE_CANDIDATES_TO_VERIFY = 5

# # Candidate verification
# MAX_CANDIDATES_TO_VERIFY = 3

# # Result extraction
# RESULT_CARD_MAX = 25
# CARD_LINK_MAX = 8

# # Direct place links
# PLACE_URL_SCAN_MAX_LINKS = 160
# PLACE_URL_SCAN_MAX_DATA_ELEMENTS = 60

# # Coordinates
# COORDINATE_SCAN_MAX_LINKS = 10
# COORDINATE_SCAN_MAX_DATA_ELEMENTS = 10

# # Metadata
# MAX_TITLE_ELEMENTS = 3
# MAX_ADDRESS_ELEMENTS = 8

# # Verification thresholds
# ADDRESS_TITLE_MIN_SCORE = 0.88
# TITLE_SEARCH_MIN_SCORE = 0.92
# EXISTING_PLACE_MIN_TITLE_SCORE = 0.95

# # Retry the Maps address panel only for promising title matches.
# STRONG_TITLE_FOR_ADDRESS_RETRY = 0.90
# ADDRESS_RENDER_RETRIES = 2
# ADDRESS_RENDER_RETRY_DELAY_MS = 350

# # Cheap pre-filter before opening candidate URLs.
# # Prevents wasting seconds on obviously wrong businesses.
# ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN = 0.78
# TITLE_CANDIDATE_PREFILTER_TITLE_MIN = 0.82

# # Address DOM extraction
# MAX_ADDRESS_SELECTOR_ELEMENTS = 12

# # Search result hydration.
# # Google Maps often attaches [role=main] before place links appear.
# SEARCH_RESULT_HYDRATION_RETRIES = 3
# SEARCH_RESULT_HYDRATION_DELAY_MS = 300


# # ============================================================
# # LEGACY CONFIG
# # ============================================================

# ADDRESS_MATCH_MIN_SCORE = 0.55
# MIN_VALID_PLACE_ADDRESS_SCORE = 0.55
# MIN_VALID_PLACE_LOCATION_SCORE = 0.50
# MIN_LOCATION_MATCHES = 1

# REQUIRE_LOCATION_FOR_PLACE = False


# # ============================================================
# # GENERIC ADDRESS TOKENS
# # ============================================================

# GENERIC_ADDRESS_TOKENS = {
#     "vietnam",
#     "viet nam",
#     "vn",
#     "street",
#     "road",
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
#     "p",
#     "q",
# }


# # ============================================================
# # ADMINISTRATIVE ALIASES
# # ============================================================

# ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS = (
#     frozenset(
#         {
#             "binh dinh",
#             "gia lai",
#         }
#     ),
#     frozenset(
#         {
#             "ha nam",
#             "nam dinh",
#             "ninh binh",
#         }
#     ),
# )


# ADMIN_PREFIX_RE = re.compile(
#     r"^(?:phuong|xa|thi\s+tran|quan|huyen|thi\s+xa|"
#     r"thanh\s+pho|tp|tinh)\s+",
#     re.IGNORECASE,
# )


# # ============================================================
# # COORDINATE PATTERNS
# # ============================================================

# COORDINATE_PATTERNS = (
#     re.compile(
#         r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
#     re.compile(
#         r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
# )


# # ============================================================
# # TEXT NORMALIZATION
# # ============================================================


# def _normalize_text(value):
#     if value is None:
#         return ""

#     try:
#         value = safe_text(value)
#     except Exception:
#         try:
#             value = str(value)
#         except Exception:
#             return ""

#     return re.sub(
#         r"\s+",
#         " ",
#         str(value or "").replace("\xa0", " ").strip(),
#     )


# def _remove_accents(value):
#     value = _normalize_text(value).lower()

#     if not value:
#         return ""

#     try:
#         value = unicodedata.normalize("NFKD", value)
#         value = "".join(c for c in value if not unicodedata.combining(c))
#     except Exception:
#         pass

#     return value.replace("đ", "d")


# def _normalize_address(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = value.replace("&", " va ")

#     value = re.sub(
#         r"[/|;]+",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"[-_]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"[()\[\]{}]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"\s*,\s*",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"\s+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r",+",
#         ",",
#         value,
#     )

#     return value.strip(" ,")


# def _normalize_title(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = re.sub(
#         r"[^a-z0-9\s]+",
#         " ",
#         value,
#     )

#     return re.sub(
#         r"\s+",
#         " ",
#         value,
#     ).strip()


# def _extract_lines(value):
#     if value is None:
#         return []

#     try:
#         text = safe_text(value)
#     except Exception:
#         try:
#             text = str(value)
#         except Exception:
#             return []

#     if not text:
#         return []

#     text = str(text).replace("\r", "\n").replace("\xa0", " ")

#     result = []

#     for line in text.split("\n"):
#         line = re.sub(
#             r"\s+",
#             " ",
#             line,
#         ).strip()

#         if line:
#             result.append(line)

#     return result


# def _unique_texts(values):
#     result = []
#     seen = set()

#     for value in values:
#         text = _normalize_text(value)

#         if not text:
#             continue

#         key = text.casefold()

#         if key in seen:
#             continue

#         seen.add(key)
#         result.append(text)

#     return result


# # ============================================================
# # ADDRESS HELPERS
# # ============================================================


# def _clean_address_token(token):
#     return _normalize_address(token).strip(" ,.-")


# def _address_tokens(address):
#     normalized = _normalize_address(address)

#     if not normalized:
#         return []

#     result = []

#     for token in normalized.split(","):
#         token = _clean_address_token(token)

#         if not token or len(token) < 2 or token in GENERIC_ADDRESS_TOKENS:
#             continue

#         result.append(token)

#     return result


# def _canonical_address_token(token):
#     """
#     Normalize one administrative/location token.

#     IMPORTANT:
#         Hàm này KHÔNG chuyển tỉnh cũ -> tỉnh mới.

#     Ví dụ:
#         "Binh Dinh Province" -> "binh dinh"
#         "Bình Định"          -> "binh dinh"
#         "Gia Lai Province"   -> "gia lai"
#         "Quy Nhon City"      -> "quy nhon"

#     Việc xác định hai tỉnh trước/sau sáp nhập có tương đương
#     hay không được xử lý riêng bởi
#     _administrative_locations_equivalent().
#     """

#     token = _normalize_address(token)

#     if not token:
#         return ""

#     # --------------------------------------------------------
#     # Remove postal code
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove English administrative suffixes
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+(?:province|city|district|ward|commune|town)$",
#         "",
#         token,
#         flags=re.IGNORECASE,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove Vietnamese administrative prefixes
#     # --------------------------------------------------------

#     previous = None

#     while token != previous:
#         previous = token

#         token = ADMIN_PREFIX_RE.sub(
#             "",
#             token,
#             count=1,
#         ).strip()

#     # --------------------------------------------------------
#     # Postal code may remain after prefix stripping
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     return token


# def _administrative_locations_equivalent(
#     location_a,
#     location_b,
# ):
#     """
#     Check whether two province/city names should be considered
#     equivalent for location verification.

#     Supports:
#         1. Exact same administrative location
#         2. Old/new administrative names after merger

#     IMPORTANT:
#         This function does NOT rewrite either location.
#     """

#     a = _canonical_address_token(location_a)
#     b = _canonical_address_token(location_b)

#     if not a or not b:
#         return False

#     # Exact same location
#     if a == b:
#         return True

#     # Old/new administrative equivalence
#     for group in ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS:
#         if a in group and b in group:
#             return True

#     return False


# def _expand_address_token(token):
#     token = _normalize_address(token)

#     if not token:
#         return []

#     variants = [token]
#     current = token

#     while True:
#         stripped = ADMIN_PREFIX_RE.sub(
#             "",
#             current,
#             count=1,
#         ).strip()

#         if not stripped or stripped == current:
#             break

#         variants.append(stripped)
#         current = stripped

#     return _unique_texts(variants)


# def _token_matches_address(
#     token,
#     actual_address,
# ):
#     actual = _normalize_address(actual_address)

#     if not actual:
#         return False

#     canonical = _canonical_address_token(token)

#     if not canonical:
#         return False

#     variants = _unique_texts(_expand_address_token(token) + [canonical])

#     actual_parts = [p.strip() for p in actual.split(",") if p.strip()]

#     for variant in variants:
#         if len(variant) < 4:
#             continue

#         pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"

#         if re.search(
#             pattern,
#             actual,
#         ):
#             return True

#         for part in actual_parts:
#             if part == variant:
#                 return True

#             words = variant.split()

#             if len(words) >= 2:
#                 valid_words = [word for word in words if len(word) >= 3]

#                 if valid_words and all(
#                     re.search(
#                         r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])",
#                         part,
#                     )
#                     for word in valid_words
#                 ):
#                     return True

#             if (
#                 SequenceMatcher(
#                     None,
#                     variant,
#                     part,
#                 ).ratio()
#                 >= 0.88
#             ):
#                 return True

#     return False


# # ============================================================
# # LOCATION
# # ============================================================


# def _extract_location_parts(address):
#     result = []

#     for part in _normalize_address(address).split(","):
#         part = part.strip()

#         if not part:
#             continue

#         if part in {
#             "vietnam",
#             "viet nam",
#             "vn",
#         }:
#             continue

#         result.append(part)

#     return result


# def _canonical_location_parts(address):
#     result = []

#     for part in _extract_location_parts(address):
#         value = _canonical_address_token(part)

#         if value and value not in GENERIC_ADDRESS_TOKENS:
#             result.append(value)

#     return result


# def get_address_province(address):
#     parts = _canonical_location_parts(address)

#     if not parts:
#         return ""

#     return parts[-1]


# def _location_similarity(
#     input_location,
#     actual_location,
# ):
#     a = _canonical_address_token(input_location)

#     b = _canonical_address_token(actual_location)

#     if not a or not b:
#         return 0.0

#     # Exact same location
#     if a == b:
#         return 1.0

#     # Before / after administrative merger
#     if _administrative_locations_equivalent(
#         a,
#         b,
#     ):
#         return 1.0

#     if a in b or b in a:
#         return 0.90

#     return SequenceMatcher(
#         None,
#         a,
#         b,
#     ).ratio()


# def _location_match_info(
#     input_address,
#     actual_address,
# ):
#     inputs = _canonical_location_parts(input_address)

#     actuals = _canonical_location_parts(actual_address)

#     if not inputs or not actuals:
#         return {
#             "matches": [],
#             "count": 0,
#             "score": 0.0,
#             "tail_matches": 0,
#             "tail_score": 0.0,
#         }

#     matches = []
#     used = set()

#     for item in inputs:
#         best_score = 0.0
#         best_actual = ""
#         best_index = -1

#         for idx, candidate in enumerate(actuals):
#             if idx in used:
#                 continue

#             score = _location_similarity(
#                 item,
#                 candidate,
#             )

#             if score > best_score:
#                 best_score = score
#                 best_actual = candidate
#                 best_index = idx

#         if best_score >= 0.72:
#             used.add(best_index)

#             matches.append(
#                 {
#                     "input": item,
#                     "actual": best_actual,
#                     "score": round(
#                         best_score,
#                         4,
#                     ),
#                 }
#             )

#     tail = inputs[-2:] if len(inputs) >= 2 else inputs

#     tail_scores = []

#     for item in tail:
#         best = max(
#             (
#                 _location_similarity(
#                     item,
#                     candidate,
#                 )
#                 for candidate in actuals
#             ),
#             default=0.0,
#         )

#         tail_scores.append(best)

#     return {
#         "matches": matches,
#         "count": len(matches),
#         "score": round(
#             len(matches) / len(inputs),
#             4,
#         ),
#         "tail_matches": sum(1 for score in tail_scores if score >= 0.75),
#         "tail_score": round(
#             sum(tail_scores) / len(tail_scores),
#             4,
#         )
#         if tail_scores
#         else 0.0,
#     }


# # ============================================================
# # ADDRESS SCORE
# # ============================================================


# def address_match_score(
#     input_address,
#     actual_address,
# ):
#     a = _normalize_address(input_address)
#     b = _normalize_address(actual_address)

#     if not a or not b:
#         return {
#             "score": 0.0,
#             "matched_tokens": [],
#             "total_tokens": 0,
#             "location_matches": 0,
#             "location_score": 0.0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "strong_match": False,
#         }

#     if a == b:
#         tokens = _address_tokens(input_address)

#         location_parts = _canonical_location_parts(input_address)

#         return {
#             "score": 1.0,
#             "matched_tokens": tokens,
#             "total_tokens": len(tokens),
#             "location_matches": len(location_parts),
#             "location_score": 1.0,
#             "tail_location_matches": len(location_parts[-2:]),
#             "tail_location_score": 1.0,
#             "strong_match": True,
#         }

#     tokens = _address_tokens(input_address)

#     matched = [
#         token
#         for token in tokens
#         if _token_matches_address(
#             token,
#             actual_address,
#         )
#     ]

#     token_score = len(matched) / len(tokens) if tokens else 0.0

#     location = _location_match_info(
#         input_address,
#         actual_address,
#     )

#     location_score = location["score"]

#     substring_bonus = 0.15 if len(a) >= 10 and a in b else 0.0

#     score = min(
#         1.0,
#         token_score * 0.60 + location_score * 0.40 + substring_bonus,
#     )

#     strong = (
#         score >= 0.80
#         or (
#             score >= ADDRESS_MATCH_MIN_SCORE
#             and location["count"] >= MIN_LOCATION_MATCHES
#         )
#         or (len(tokens) <= 2 and bool(matched) and score >= 0.40)
#     )

#     return {
#         "score": round(
#             score,
#             4,
#         ),
#         "matched_tokens": matched,
#         "total_tokens": len(tokens),
#         "location_matches": location["count"],
#         "location_score": round(
#             location_score,
#             4,
#         ),
#         "tail_location_matches": location["tail_matches"],
#         "tail_location_score": location["tail_score"],
#         "strong_match": bool(strong),
#     }


# def address_is_related(
#     input_address,
#     actual_address,
# ):
#     return bool(
#         address_match_score(
#             input_address,
#             actual_address,
#         ).get("strong_match")
#     )


# # ============================================================
# # TITLE
# # ============================================================


# def title_match_score(
#     input_title,
#     actual_title,
#     actual_text="",
# ):
#     a = _normalize_title(input_title)

#     b = _normalize_title(actual_title)

#     combined = _normalize_title(actual_text)

#     if not a:
#         return 0.0

#     if a == b:
#         return 1.0

#     if b and a in b:
#         return 0.95

#     if b and b in a:
#         return 0.90

#     sequence = (
#         SequenceMatcher(
#             None,
#             a,
#             b,
#         ).ratio()
#         if b
#         else 0.0
#     )

#     wanted = {x for x in a.split() if len(x) >= 3}

#     got = {x for x in combined.split() if len(x) >= 3}

#     overlap = len(wanted & got) / len(wanted) if wanted else 0.0

#     return round(
#         max(
#             sequence,
#             overlap,
#         ),
#         4,
#     )


# def result_matches_title(
#     input_title,
#     result_title,
# ):
#     return (
#         title_match_score(
#             input_title,
#             result_title,
#         )
#         >= 0.75
#     )


# # ============================================================
# # ADDRESS RESULT VALIDATION
# # ============================================================


# def result_matches_address(
#     input_address,
#     result_address,
# ):
#     actual_address = _normalize_text(result_address)

#     if (
#         not actual_address
#         or not re.search(
#             r"[a-z0-9]",
#             actual_address,
#             re.IGNORECASE,
#         )
#         or not _address_tokens(actual_address)
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         actual_address,
#     )

#     if info.get("strong_match"):
#         return True

#     location_matches = info.get(
#         "location_matches",
#         0,
#     )

#     tail_matches = info.get(
#         "tail_location_matches",
#         0,
#     )

#     return location_matches >= 2 and tail_matches >= 1


# def _result_matches_address(
#     input_address,
#     result_address,
# ):
#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# # ============================================================
# # SEARCH IDENTITY
# # ============================================================


# def _matches_search_identity(
#     input_title,
#     input_address,
#     result_title,
#     result_address,
# ):
#     """
#     Strict validation used only when we HAVE metadata.

#     If metadata is unavailable, caller can use URL-level
#     validation instead of forcing expensive extraction.
#     """

#     if not result_matches_title(
#         input_title,
#         result_title,
#     ):
#         return False

#     if not _normalize_text(input_address):
#         return True

#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# def _matches_title_search_result(
#     input_title,
#     result_title,
#     result_address,
# ):
#     return result_matches_title(
#         input_title,
#         result_title,
#     ) and _is_usable_address_text(result_address)


# # ============================================================
# # ADDRESS USABILITY
# # ============================================================


# def _is_usable_address_text(value):
#     """
#     Return True only for text that plausibly represents a postal/street
#     address or useful locality string.

#     IMPORTANT:
#     Reject Google Maps rating/review snippets such as:
#         "4,7(1.060)"
#         "4.7 (1,060)"
#         "4,3(1.699)"
#         "1.060 reviews"
#     """
#     text = _normalize_text(value)

#     if not text:
#         return False

#     # Private-use glyphs / escaped unicode noise.
#     if re.search(
#         r"\\u[0-9a-f]{4}",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     if any(unicodedata.category(char) == "Co" for char in text):
#         return False

#     # --------------------------------------------------------
#     # Ratings / review counts
#     # --------------------------------------------------------
#     compact = re.sub(r"\s+", "", text)

#     rating_patterns = (
#         r"^[0-5](?:[.,]\d)?\(\d[\d.,]*\)$",
#         r"^[0-5](?:[.,]\d)?$",
#         r"^\d[\d.,]*\s*(?:reviews?|đánh\s*giá|ratings?)$",
#         r"^[0-5](?:[.,]\d)?\s*\(\d[\d.,]*\)\s*(?:reviews?|đánh\s*giá|ratings?)?$",
#     )

#     for pattern in rating_patterns:
#         if re.fullmatch(
#             pattern,
#             text,
#             re.IGNORECASE,
#         ) or re.fullmatch(
#             pattern,
#             compact,
#             re.IGNORECASE,
#         ):
#             return False

#     # Pure numeric-ish text is not an address.
#     if re.fullmatch(
#         r"[\d\s.,()/+-]+",
#         text,
#     ):
#         return False

#     # Category-only labels.
#     if re.fullmatch(
#         r"(?:khách sạn|hotel|nhà nghỉ|"
#         r"resort|restaurant|nhà hàng|"
#         r"homestay|lodge|love hotel|"
#         r"serviced apartment)"
#         r"(?:\s+\d+\s+sao)?",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     normalized = _normalize_address(text)

#     if not normalized:
#         return False

#     # --------------------------------------------------------
#     # Positive address evidence
#     # --------------------------------------------------------
#     has_digit = bool(
#         re.search(
#             r"\d",
#             normalized,
#         )
#     )

#     has_comma = "," in normalized

#     location_words = (
#         "vietnam",
#         "viet nam",
#         "gia lai",
#         "binh dinh",
#         "quy nhon",
#         "pleiku",
#         "street",
#         "road",
#         "ward",
#         "district",
#         "province",
#         "city",
#         "phuong",
#         "quan",
#         "huyen",
#         "tinh",
#         "xa",
#         "thanh pho",
#         "thi tran",
#         "thi xa",
#         "duong",
#         "đuong",
#         "đường",
#     )

#     has_location_word = any(token in normalized for token in location_words)

#     # Street/address usually has a number + locality separator/word.
#     if has_digit and (has_comma or has_location_word):
#         return True

#     # Locality-only address strings can still be useful.
#     if has_comma and has_location_word:
#         return True

#     # Require at least two comma-separated meaningful parts.
#     parts = [part.strip() for part in normalized.split(",") if part.strip()]

#     if len(parts) >= 2 and any(len(part) >= 3 for part in parts):
#         return True

#     return False


# # ============================================================
# # COORDINATES
# # ============================================================


# def _valid_lat_lng(
#     lat,
#     lng,
# ):
#     try:
#         lat = float(lat)
#         lng = float(lng)
#     except Exception:
#         return False

#     return -90 <= lat <= 90 and -180 <= lng <= 180


# def _extract_coordinates_from_text(
#     text,
# ):
#     if not text:
#         return None

#     try:
#         text = str(text)
#     except Exception:
#         return None

#     for pattern in COORDINATE_PATTERNS:
#         match = pattern.search(text)

#         if not match:
#             continue

#         if _valid_lat_lng(
#             match.group(1),
#             match.group(2),
#         ):
#             return (
#                 float(match.group(1)),
#                 float(match.group(2)),
#             )

#     return None


# def extract_coordinates_from_url(
#     url,
# ):
#     coordinates = _extract_coordinates_from_text(url)

#     if coordinates:
#         return coordinates

#     return None, None


# def get_current_page_coordinates(page):
#     try:
#         coordinates = _extract_coordinates_from_text(page.url or "")

#         if coordinates:
#             return coordinates

#     except Exception:
#         pass

#     return None, None


# def extract_coordinates_from_page(page):
#     """
#     FAST coordinate extraction.

#     Priority:
#         1. current URL
#         2. small number of links
#         3. small number of data attributes

#     This function is intentionally NOT expensive.
#     """

#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     try:
#         value = _extract_coordinates_from_text(page.url or "")

#         if value:
#             return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 2. Links
#     # --------------------------------------------------------

#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             COORDINATE_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             value = _extract_coordinates_from_text(links.nth(i).get_attribute("href"))

#             if value:
#                 return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             COORDINATE_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 value = _extract_coordinates_from_text(element.get_attribute(attr))

#                 if value:
#                     return value

#     except Exception:
#         pass

#     return None


# # ============================================================
# # GOOGLE MAPS URL
# # ============================================================


# def _is_google_maps_url(url):
#     if not url:
#         return False

#     try:
#         parsed = urlparse(str(url).strip())
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return False

#     return (
#         parsed.scheme
#         in {
#             "http",
#             "https",
#         }
#         and parsed.hostname
#         in {
#             "google.com",
#             "www.google.com",
#             "maps.google.com",
#         }
#         and parsed.path.lower().startswith("/maps")
#     )


# def is_google_maps_url(url):
#     return _is_google_maps_url(url)


# def _is_google_maps_place_url(url):
#     if not _is_google_maps_url(url):
#         return False

#     try:
#         value = str(url).strip().lower()
#     except Exception:
#         return False

#     if "/maps/search" in value:
#         return False

#     return "/maps/place/" in value or "/maps/place?" in value


# def is_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _is_usable_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _clean_place_url(url):
#     if not url:
#         return None

#     try:
#         value = str(url).strip()
#     except Exception:
#         return None

#     try:
#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     if _is_google_maps_place_url(value):
#         return value

#     return None


# def get_current_google_maps_url(page):
#     try:
#         return _clean_place_url(page.url or "")
#     except Exception:
#         return None


# def extract_place_title_from_url(
#     url,
# ):
#     if not url:
#         return ""

#     try:
#         path = urlparse(str(url)).path
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return ""

#     match = re.search(
#         r"/maps/place/([^/]+)",
#         path,
#         re.IGNORECASE,
#     )

#     if not match:
#         return ""

#     return _normalize_text(
#         unquote(match.group(1)).replace(
#             "+",
#             " ",
#         )
#     )


# def extract_google_maps_url_from_href(
#     href,
# ):
#     if not href:
#         return None

#     try:
#         value = (
#             str(href)
#             .strip()
#             .replace(
#                 "&amp;",
#                 "&",
#             )
#         )

#         value = (
#             value.replace(
#                 "\\/",
#                 "/",
#             )
#             .replace(
#                 "\\u003d",
#                 "=",
#             )
#             .replace(
#                 "\\u0026",
#                 "&",
#             )
#         )

#         if value.startswith("/maps/"):
#             value = "https://www.google.com" + value

#         decoded = unquote(value)

#         if _is_google_maps_place_url(decoded):
#             value = decoded

#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     return _clean_place_url(href)


# # ============================================================
# # POPUPS
# # ============================================================


# def close_google_popups(page):
#     if page is None:
#         return

#     selectors = [
#         'button:has-text("Accept all")',
#         'button:has-text("I agree")',
#         'button:has-text("Đồng ý")',
#         'button:has-text("Chấp nhận tất cả")',
#         '[aria-label="Accept all"]',
#         '[aria-label="I agree"]',
#         '[aria-label="Chấp nhận tất cả"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             if locator.count() and locator.first.is_visible(timeout=200):
#                 locator.first.click(timeout=700)

#                 break

#         except Exception:
#             continue


# # ============================================================
# # FAST WAIT
# # ============================================================


# def wait_for_search_results(
#     page,
#     timeout=None,
# ):
#     """
#     Much cheaper than repeatedly calling count()
#     on multiple selectors.
#     """

#     if page is None:
#         return False

#     timeout = min(
#         int(timeout or PAGE_TIMEOUT),
#         5000,
#     )

#     try:
#         if page.is_closed():
#             return False
#     except Exception:
#         return False

#     selector = 'a[href*="/maps/place/"], div.Nv2PK, [role="main"]'

#     try:
#         page.locator(selector).first.wait_for(
#             state="attached",
#             timeout=timeout,
#         )

#         return True

#     except Exception:
#         return False


# # ============================================================
# # PLAYWRIGHT TEXT
# # ============================================================


# def _read_locator_text(locator):
#     if locator is None:
#         return ""

#     try:
#         text = locator.inner_text(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     try:
#         text = locator.text_content(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # SELECTED PLACE TITLE
# # ============================================================


# def extract_selected_place_title(
#     page,
#     expected_title=None,
# ):
#     if page is None:
#         return ""

#     values = []

#     for selector in (
#         '[role="main"] h1',
#         "h1",
#         '[data-item-id="title"]',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_TITLE_ELEMENTS,
#             )

#             for i in range(count):
#                 text = _read_locator_text(locator.nth(i))

#                 if text:
#                     values.append(text)

#         except Exception:
#             continue

#     values = _unique_texts(values)

#     if not values:
#         return ""

#     if expected_title:
#         return max(
#             values,
#             key=lambda x: title_match_score(
#                 expected_title,
#                 x,
#             ),
#         )

#     return values[0]


# # ============================================================
# # SELECTED PLACE ADDRESS
# # ============================================================


# def extract_selected_place_address(
#     page,
#     expected_address="",
# ):
#     """
#     Extract ONLY official/structured Google Maps address UI.

#     IMPORTANT:
#         Do NOT use arbitrary main-panel text here.
#         Reviews/descriptions often contain street names and must never
#         be treated as the Place address.
#     """
#     if page is None:
#         return ""

#     candidates = []

#     selectors = [
#         'button[data-item-id="address"]',
#         '[data-item-id="address"]',
#         '[role="main"] button[data-item-id="address"]',
#         '[role="main"] [data-item-id="address"]',
#         'button[aria-label^="Địa chỉ"]',
#         'button[aria-label^="Address"]',
#         '[role="main"] button[aria-label^="Địa chỉ"]',
#         '[role="main"] button[aria-label^="Address"]',
#         '[aria-label^="Địa chỉ"]',
#         '[aria-label^="Address"]',
#         '[data-tooltip*="Địa chỉ"]',
#         '[data-tooltip*="Address"]',
#         '[title*="Địa chỉ"]',
#         '[title*="Address"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_ADDRESS_SELECTOR_ELEMENTS,
#             )

#             for i in range(count):
#                 element = locator.nth(i)

#                 # visible text
#                 text_value = _read_locator_text(element)

#                 if _is_usable_address_text(text_value):
#                     candidates.append(text_value)

#                 # structured attributes
#                 for attr in (
#                     "aria-label",
#                     "data-tooltip",
#                     "title",
#                 ):
#                     try:
#                         value = element.get_attribute(attr)
#                     except Exception:
#                         value = None

#                     if not value:
#                         continue

#                     value = _normalize_text(value)

#                     value = re.sub(
#                         r"^(?:địa\s*chỉ|address)\s*:\s*",
#                         "",
#                         value,
#                         flags=re.IGNORECASE,
#                     )

#                     if _is_usable_address_text(value):
#                         candidates.append(value)

#         except Exception:
#             continue

#     candidates = [
#         value for value in _unique_texts(candidates) if _is_usable_address_text(value)
#     ]

#     if not candidates:
#         return ""

#     if expected_address:

#         def score_address(value):
#             info = address_match_score(
#                 expected_address,
#                 value,
#             )

#             return (
#                 info.get("score", 0.0),
#                 info.get("location_score", 0.0),
#             )

#         return max(
#             candidates,
#             key=score_address,
#         )

#     return candidates[0]


# # ============================================================
# # MAIN TEXT FALLBACK
# # ============================================================


# def _find_best_address_from_main_text(
#     page,
#     expected_address="",
# ):
#     if page is None:
#         return ""

#     try:
#         main = page.locator('[role="main"]').first

#         if not main.count():
#             return ""

#         text = main.inner_text(timeout=1800)

#         lines = _extract_lines(text)

#         if not lines:
#             return ""

#         if expected_address:
#             scored = []

#             for line in lines:
#                 info = address_match_score(
#                     expected_address,
#                     line,
#                 )

#                 if info["score"] >= 0.20 or info["location_score"] >= 0.25:
#                     scored.append(
#                         (
#                             info["score"],
#                             info["location_score"],
#                             line,
#                         )
#                     )

#             if scored:
#                 scored.sort(
#                     key=lambda x: (
#                         x[0],
#                         x[1],
#                     ),
#                     reverse=True,
#                 )

#                 return scored[0][2]

#         for line in lines:
#             normalized = _normalize_address(line)

#             if not normalized:
#                 continue

#             has_number = bool(
#                 re.search(
#                     r"\d",
#                     normalized,
#                 )
#             )

#             has_comma = "," in normalized

#             has_location_word = any(
#                 token in normalized
#                 for token in (
#                     "vietnam",
#                     "viet nam",
#                     "street",
#                     "road",
#                     "ward",
#                     "district",
#                     "province",
#                     "city",
#                     "phuong",
#                     "quan",
#                     "huyen",
#                     "tinh",
#                     "xa",
#                     "thanh pho",
#                     "thi tran",
#                     "thi xa",
#                 )
#             )

#             if has_number and (has_comma or has_location_word):
#                 return line

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # CANDIDATE ADDRESS
# # ============================================================


# def _find_best_address_from_candidate(
#     candidate,
#     expected_address,
# ):
#     if not candidate:
#         return ""

#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     usable = [text for text in _unique_texts(texts) if _is_usable_address_text(text)]

#     if not usable:
#         return ""

#     return max(
#         usable,
#         key=lambda text: address_match_score(
#             expected_address,
#             text,
#         ).get(
#             "score",
#             0.0,
#         ),
#     )


# # ============================================================
# # PLACE URL EXTRACTION
# # ============================================================


# def extract_place_url_from_page(
#     page,
# ):
#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     current = get_current_google_maps_url(page)

#     if current:
#         return current

#     # --------------------------------------------------------
#     # 2. Direct place links
#     # --------------------------------------------------------

#     try:
#         links = page.locator('a[href*="/maps/place/"]')

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             url = extract_google_maps_url_from_href(links.nth(i).get_attribute("href"))

#             if url:
#                 return url

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 url = extract_google_maps_url_from_href(element.get_attribute(attr))

#                 if url:
#                     return url

#     except Exception:
#         pass

#     return None


# # ============================================================
# # RESULT CARD
# # ============================================================


# def _extract_card_texts(card):
#     values = []

#     try:
#         values.extend(_extract_lines(card.inner_text(timeout=700)))

#     except Exception:
#         pass

#     try:
#         aria = card.get_attribute("aria-label")

#         if aria:
#             values.append(aria)

#     except Exception:
#         pass

#     return _unique_texts(values)


# def _extract_title_from_card_texts(
#     texts,
# ):
#     if not texts:
#         return ""

#     return texts[0]


# def _candidate_text_score(
#     candidate,
#     input_title,
#     input_address,
# ):
#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     combined = " ".join(texts)

#     title_score = title_match_score(
#         input_title,
#         candidate.get(
#             "title",
#             "",
#         ),
#         combined,
#     )

#     address_info = address_match_score(
#         input_address,
#         combined,
#     )

#     return (
#         title_score * 0.45
#         + address_info["score"] * 0.40
#         + address_info["location_score"] * 0.15
#     )


# # ============================================================
# # EXTRACT SEARCH CANDIDATES
# # ============================================================


# def extract_result_candidates(page):
#     """
#     Extract Google Maps Place candidates without depending on one
#     specific Maps DOM version.

#     Sources:
#         1. Result cards
#         2. ALL anchors whose href resolves to /maps/place/
#         3. data-url / data-href
#         4. raw HTML fallback for hydrated/virtualized Maps markup

#     This function NEVER navigates.
#     """
#     if page is None:
#         return []

#     candidates = []
#     seen = set()

#     def add_candidate(
#         url,
#         title="",
#         texts=None,
#     ):
#         url = extract_google_maps_url_from_href(url)

#         if not url:
#             return

#         if url in seen:
#             return

#         seen.add(url)

#         title = _normalize_text(title)

#         if not title:
#             title = extract_place_title_from_url(url)

#         values = _unique_texts(list(texts or []) + ([title] if title else []))

#         candidates.append(
#             {
#                 "url": url,
#                 "google_maps_url": url,
#                 "title": title,
#                 "texts": values,
#             }
#         )

#     # ========================================================
#     # 1. RESULT CARDS
#     # ========================================================
#     for selector in (
#         "div.Nv2PK",
#         "div.bfdHYd",
#         '[role="article"]',
#         '[role="feed"] > div',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 RESULT_CARD_MAX,
#             )

#             if not count:
#                 continue

#             for i in range(count):
#                 try:
#                     card = locator.nth(i)
#                     texts = _extract_card_texts(card)

#                     links = card.locator('a[href*="/maps/place/"]')

#                     link_count = min(
#                         links.count(),
#                         CARD_LINK_MAX,
#                     )

#                     for j in range(link_count):
#                         href = links.nth(j).get_attribute("href")

#                         add_candidate(
#                             href,
#                             title=_extract_title_from_card_texts(texts),
#                             texts=texts,
#                         )

#                     if link_count == 0:
#                         links = card.locator("a[href]")

#                         link_count = min(
#                             links.count(),
#                             CARD_LINK_MAX,
#                         )

#                         for j in range(link_count):
#                             link = links.nth(j)
#                             href = link.get_attribute("href")

#                             url = extract_google_maps_url_from_href(href)

#                             if not url:
#                                 continue

#                             link_text = _read_locator_text(link)

#                             add_candidate(
#                                 url,
#                                 title=link_text
#                                 or _extract_title_from_card_texts(texts),
#                                 texts=texts + ([link_text] if link_text else []),
#                             )

#                 except Exception:
#                     continue

#         except Exception:
#             continue

#     # ========================================================
#     # 2. GLOBAL ANCHORS
#     # ========================================================
#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             try:
#                 link = links.nth(i)
#                 href = link.get_attribute("href")

#                 url = extract_google_maps_url_from_href(href)

#                 if not url:
#                     continue

#                 link_text = _read_locator_text(link)

#                 aria = ""
#                 try:
#                     aria = link.get_attribute("aria-label") or ""
#                 except Exception:
#                     pass

#                 title = link_text or aria or extract_place_title_from_url(url)

#                 add_candidate(
#                     url,
#                     title=title,
#                     texts=[link_text, aria],
#                 )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 3. DATA ATTRIBUTES
#     # ========================================================
#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             try:
#                 element = elements.nth(i)

#                 element_text = _read_locator_text(element)

#                 for attr in (
#                     "data-url",
#                     "data-href",
#                 ):
#                     value = element.get_attribute(attr)

#                     if not value:
#                         continue

#                     add_candidate(
#                         value,
#                         title=element_text,
#                         texts=[element_text],
#                     )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 4. RAW HTML FALLBACK
#     # ========================================================
#     if not candidates:
#         try:
#             html = page.content()

#             if html:
#                 raw_patterns = (
#                     r'https?://www\.google\.com/maps/place/[^"\'<>\s]+',
#                     r'https?://google\.com/maps/place/[^"\'<>\s]+',
#                     r'/maps/place/[^"\'<>\s]+',
#                 )

#                 for pattern in raw_patterns:
#                     for match in re.findall(
#                         pattern,
#                         html,
#                         flags=re.IGNORECASE,
#                     ):
#                         value = (
#                             match.replace("&amp;", "&")
#                             .replace("\\/", "/")
#                             .replace("\\u003d", "=")
#                             .replace("\\u0026", "&")
#                         )

#                         if value.startswith("/maps/"):
#                             value = "https://www.google.com" + value

#                         add_candidate(
#                             value,
#                             title=extract_place_title_from_url(value),
#                             texts=[],
#                         )

#                         if len(candidates) >= RESULT_CARD_MAX:
#                             break

#                     if candidates:
#                         break

#         except Exception:
#             pass

#     return candidates


# # ============================================================
# # CLICK / NAVIGATE
# # ============================================================


# def click_candidate(
#     page,
#     candidate,
#     context=None,
#     logger=None,
# ):
#     if page is None or not candidate:
#         return page, False, 0

#     url = candidate.get("url") or candidate.get("google_maps_url")

#     if not url:
#         return page, False, 0

#     if not _is_google_maps_place_url(url):
#         return page, False, 0

#     if context is None:
#         try:
#             context = page.context
#         except Exception:
#             pass

#     try:
#         if context is not None:
#             return safe_goto(
#                 page,
#                 context,
#                 url,
#                 logger=logger,
#                 timeout=PAGE_TIMEOUT,
#             )

#         page.goto(
#             url,
#             wait_until="domcontentloaded",
#             timeout=PAGE_TIMEOUT,
#         )

#         return page, True, 1

#     except Exception as error:
#         if logger:
#             logger.warning("Candidate navigation error: " + str(error)[:250])

#         return page, False, 1


# # ============================================================
# # FAST PROVINCE GUARD
# # ============================================================


# def _province_from_text(
#     text,
# ):
#     parts = _canonical_location_parts(text)

#     if not parts:
#         return ""

#     return parts[-1]


# def _province_matches(
#     input_address,
#     actual_address,
# ):
#     """
#     Protect against Google Maps jumping to another province.

#     Supports:
#         - Same province
#         - Province before/after administrative merger

#     Returns:
#         True
#             Province matches or is administratively equivalent.

#         False
#             Definitely another province.

#         None
#             Actual Maps address is unavailable, so cannot decide.
#     """

#     expected_province = get_address_province(
#         input_address,
#     )

#     actual_province = get_address_province(
#         actual_address,
#     )

#     # Input does not provide province information.
#     if not expected_province:
#         return True

#     # Google Maps address unavailable.
#     if not actual_province:
#         return None

#     return _administrative_locations_equivalent(
#         expected_province,
#         actual_province,
#     )


# # ============================================================
# # CANDIDATE VERIFICATION HELPERS
# # ============================================================


# def _candidate_location_evidence(
#     candidate,
#     input_address,
# ):
#     if not candidate or not _normalize_text(input_address):
#         return False

#     texts = candidate.get("texts", []) or []

#     if not texts:
#         return False

#     combined = " | ".join(
#         text for text in _unique_texts(texts) if _normalize_text(text)
#     )

#     if not combined:
#         return False

#     expected_province = get_address_province(input_address)
#     actual_province = get_address_province(combined)

#     if (
#         expected_province
#         and actual_province
#         and not _administrative_locations_equivalent(
#             expected_province,
#             actual_province,
#         )
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         combined,
#     )

#     return bool(
#         info.get("location_matches", 0) >= 1
#         and (
#             info.get("location_score", 0.0) >= 0.25
#             or info.get("tail_location_matches", 0) >= 1
#         )
#     )


# def _extract_candidate_address_with_retry(
#     page,
#     candidate,
#     input_address,
#     title_score,
# ):
#     """
#     SAFE ADDRESS EXTRACTION.

#     SOURCE OF TRUTH:
#         ONLY official Google Maps address DOM.

#     NEVER use:
#         - candidate card text
#         - reviews
#         - descriptions
#         - main panel arbitrary text
#         - combined candidate text

#     This prevents review snippets such as:
#         "... ở 11 An Dương Vương QN ..."
#     from becoming actual_address.
#     """

#     if page is None:
#         return ""

#     # ========================================================
#     # 1. OFFICIAL GOOGLE MAPS ADDRESS ELEMENT ONLY
#     # ========================================================
#     try:
#         actual_address = extract_selected_place_address(
#             page,
#             expected_address=input_address,
#         )
#     except Exception:
#         actual_address = ""

#     if actual_address and _is_usable_address_text(actual_address):
#         return actual_address

#     # ========================================================
#     # 2. ONLY RETRY FOR STRONG TITLE
#     # ========================================================
#     if title_score < STRONG_TITLE_FOR_ADDRESS_RETRY:
#         return ""

#     for _ in range(ADDRESS_RENDER_RETRIES):
#         try:
#             page.wait_for_timeout(ADDRESS_RENDER_RETRY_DELAY_MS)
#         except Exception:
#             time.sleep(ADDRESS_RENDER_RETRY_DELAY_MS / 1000.0)

#         try:
#             actual_address = extract_selected_place_address(
#                 page,
#                 expected_address=input_address,
#             )
#         except Exception:
#             actual_address = ""

#         if actual_address and _is_usable_address_text(actual_address):
#             return actual_address

#     return ""


# def _is_nearly_exact_title_match(
#     input_title,
#     actual_title,
#     actual_url,
# ):
#     """
#     Conservative identity fallback used only when address UI is absent.

#     Requires:
#         - Place URL
#         - title >= 0.99
#         - URL title itself also strongly matches the requested title
#     """
#     if not _is_google_maps_place_url(actual_url):
#         return False

#     direct_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     url_score = title_match_score(
#         input_title,
#         url_title,
#     )

#     return direct_score >= 0.99 and url_score >= 0.95


# # ============================================================
# # CANDIDATE VERIFY
# # ============================================================


# def verify_candidate_address(
#     page,
#     context,
#     candidate,
#     input_title,
#     input_address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Verify a Google Maps Place candidate.

#     Rules:
#         - /maps/place/ is required but never sufficient by itself.
#         - We NEVER infer address/province from reviews/main text.
#         - If official address exists, verify it normally.
#         - If official address is absent, only a nearly-exact title match
#           may survive without an address, and only in title/address search
#           mode. Existing-link reuse remains stricter.
#     """
#     result = {
#         "success": False,
#         "url": None,
#         "google_maps_url": None,
#         "title": "",
#         "address": "",
#         "score": 0.0,
#         "address_score": 0.0,
#         "title_score": 0.0,
#         "matched_tokens": [],
#         "coordinates": None,
#         "reason": "",
#         "page": page,
#         "attempts": 0,
#         "search_mode": search_mode,
#         "location_score": 0.0,
#         "location_matches": 0,
#         "tail_location_matches": 0,
#         "tail_location_score": 0.0,
#     }

#     if page is None:
#         result["reason"] = "PAGE_NONE"
#         return result

#     if not candidate:
#         result["reason"] = "CANDIDATE_NONE"
#         return result

#     candidate_url = candidate.get("url") or candidate.get("google_maps_url")

#     if not _is_google_maps_place_url(candidate_url):
#         result["reason"] = "INVALID_CANDIDATE_URL"
#         return result

#     page, success, attempts = click_candidate(
#         page,
#         candidate,
#         context=context,
#         logger=logger,
#     )

#     result["page"] = page
#     result["attempts"] = attempts

#     if not success or page is None:
#         result["reason"] = "NAVIGATION_FAILED"
#         return result

#     try:
#         page.wait_for_timeout(150)
#     except Exception:
#         pass

#     close_google_popups(page)

#     actual_url = get_current_google_maps_url(page) or _clean_place_url(candidate_url)

#     if not actual_url:
#         result["reason"] = "NO_VALID_MAPS_PLACE_URL"
#         return result

#     coordinates = _extract_coordinates_from_text(
#         actual_url,
#     )

#     # --------------------------------------------------------
#     # Title
#     # --------------------------------------------------------
#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     actual_title = url_title or candidate_title or ""

#     title_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     if title_score < 0.90:
#         try:
#             dom_title = extract_selected_place_title(
#                 page,
#                 expected_title=input_title,
#             )
#         except Exception:
#             dom_title = ""

#         if dom_title:
#             dom_score = title_match_score(
#                 input_title,
#                 dom_title,
#             )

#             if dom_score > title_score:
#                 actual_title = dom_title
#                 title_score = dom_score

#     # --------------------------------------------------------
#     # Official/structured address only
#     # --------------------------------------------------------
#     actual_address = _extract_candidate_address_with_retry(
#         page,
#         candidate,
#         input_address,
#         title_score,
#     )

#     address_available = bool(_is_usable_address_text(actual_address))

#     if address_available:
#         info = address_match_score(
#             input_address,
#             actual_address,
#         )
#     else:
#         info = {
#             "score": 0.0,
#             "location_score": 0.0,
#             "location_matches": 0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "matched_tokens": [],
#         }

#     address_score = info.get(
#         "score",
#         0.0,
#     )

#     location_score = info.get(
#         "location_score",
#         0.0,
#     )

#     province_result = (
#         _province_matches(
#             input_address,
#             actual_address,
#         )
#         if address_available
#         else None
#     )

#     exact_identity = _is_nearly_exact_title_match(
#         input_title,
#         actual_title,
#         actual_url,
#     )

#     result.update(
#         {
#             "url": actual_url,
#             "google_maps_url": actual_url,
#             "title": actual_title,
#             "address": actual_address,
#             "title_score": title_score,
#             "address_score": address_score,
#             "score": address_score,
#             "location_score": location_score,
#             "location_matches": info.get(
#                 "location_matches",
#                 0,
#             ),
#             "tail_location_matches": info.get(
#                 "tail_location_matches",
#                 0,
#             ),
#             "tail_location_score": info.get(
#                 "tail_location_score",
#                 0.0,
#             ),
#             "matched_tokens": info.get(
#                 "matched_tokens",
#                 [],
#             ),
#             "coordinates": coordinates,
#         }
#     )

#     # --------------------------------------------------------
#     # Hard province contradiction only when we have a real address
#     # --------------------------------------------------------
#     if province_result is False:
#         result["reason"] = "PROVINCE_MISMATCH"
#         accepted = False

#     else:
#         input_has_address = bool(_normalize_text(input_address))

#         address_ok = not input_has_address or (
#             address_available
#             and result_matches_address(
#                 input_address,
#                 actual_address,
#             )
#         )

#         # ====================================================
#         # ADDRESS SEARCH MODE
#         # ====================================================
#         if search_mode == "address":
#             if title_score < ADDRESS_TITLE_MIN_SCORE:
#                 result["reason"] = "ADDRESS_TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "ADDRESS_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "ADDRESS_MISMATCH"
#                     accepted = False

#             elif exact_identity and not input_has_address:
#                 # Exact-title-only acceptance is allowed ONLY when the
#                 # source record itself has no address to validate.
#                 result["reason"] = "ADDRESS_EXACT_TITLE_ACCEPTED"
#                 accepted = True

#             else:
#                 # If the source record has an address, title identity
#                 # alone is NOT enough.  A same-name business can exist
#                 # in another district/province, and Maps may temporarily
#                 # fail to render the address panel.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#         # ====================================================
#         # TITLE FALLBACK MODE
#         # ====================================================
#         elif search_mode == "title":
#             if title_score < TITLE_SEARCH_MIN_SCORE:
#                 result["reason"] = "TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "TITLE_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "TITLE_LOCATION_MISMATCH"
#                     accepted = False

#             elif exact_identity and not input_has_address:
#                 # No input address exists, so exact identity is the best
#                 # available evidence.
#                 result["reason"] = "TITLE_EXACT_MATCH_ACCEPTED"
#                 accepted = True

#             else:
#                 # Input address exists but Maps did not expose a usable
#                 # candidate address.  Do not silently accept by title only.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#         # ====================================================
#         # EXISTING/CURRENT PLACE MODE
#         # ====================================================
#         else:
#             if title_score < EXISTING_PLACE_MIN_TITLE_SCORE:
#                 result["reason"] = "EXISTING_TITLE_MISMATCH"
#                 accepted = False

#             elif not address_available:
#                 # Existing-link reuse remains conservative because an old
#                 # wrong link can otherwise silently survive forever.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#             elif not address_ok:
#                 result["reason"] = "EXISTING_LOCATION_MISMATCH"
#                 accepted = False

#             else:
#                 result["reason"] = "EXISTING_PLACE_ACCEPTED"
#                 accepted = True

#     result["success"] = bool(accepted)

#     if logger:
#         logger.info(
#             "VERIFY %s | "
#             "input_title=%r | actual_title=%r | "
#             "input_address=%r | actual_address=%r | "
#             "title_score=%.3f | address_score=%.3f | "
#             "location_score=%.3f | exact_identity=%s | "
#             "input_has_address=%s | success=%s | reason=%s | url=%r",
#             search_mode.upper(),
#             input_title,
#             actual_title,
#             input_address,
#             actual_address,
#             title_score,
#             address_score,
#             location_score,
#             exact_identity,
#             bool(_normalize_text(input_address)),
#             result["success"],
#             result["reason"],
#             actual_url,
#         )

#     return result


# # ============================================================
# # RECOVER COORDINATES
# # ============================================================


# def recover_candidate_coordinates(
#     page,
#     context,
#     candidate,
#     logger=None,
# ):
#     """
#     Only called after success when URL did not contain
#     coordinates.
#     """

#     for _ in range(SELECTED_PLACE_RETRIES):
#         try:
#             page, success, _ = click_candidate(
#                 page,
#                 candidate,
#                 context=context,
#                 logger=logger,
#             )

#             if not success:
#                 continue

#             try:
#                 page.wait_for_timeout(100)
#             except Exception:
#                 pass

#             coordinates = extract_coordinates_from_page(page)

#             if coordinates:
#                 return coordinates

#         except Exception:
#             pass

#         time.sleep(SELECTED_PLACE_RETRY_DELAY)

#     return None


# # ============================================================
# # SEARCH URL
# # ============================================================


# def _safe_build_search_url(
#     query,
# ):
#     query = safe_text(query)

#     if not query:
#         return ""

#     try:
#         url = build_google_maps_search_url(query)

#         if url:
#             return url

#     except Exception:
#         pass

#     return "https://www.google.com/maps/search/?api=1&query=" + quote(
#         query,
#         safe="",
#     )


# # ============================================================
# # SEARCH VARIANTS
# # ============================================================


# def build_search_variants(title, address):
#     """ADDRESS PHASE ONLY. Title-only has its own fallback phase."""
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()
#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if title and address:
#         add(f"{address}, {title}")
#         add(f"{title}, {address}")
#     if address:
#         add(address)
#     return variants[:MAX_SEARCH_VARIANTS]


# def build_title_search_variants(title, address):
#     """TITLE FALLBACK. Runs only after address phase failed."""
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()
#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if not title:
#         return variants

#     add(title)
#     parts = _extract_location_parts(address)
#     if len(parts) >= 2:
#         add(f"{title}, {', '.join(parts[-2:])}")
#     elif parts:
#         add(f"{title}, {parts[-1]}")
#     if address:
#         add(f"{title}, {address}")
#     return variants[:MAX_TITLE_SEARCH_VARIANTS]


# # ============================================================
# # LOG
# # ============================================================


# def _log_candidate_summary(
#     logger,
#     index,
#     total,
#     verified,
# ):
#     if not logger:
#         return

#     try:
#         logger.debug(
#             "Candidate %s/%s | "
#             "title=%r | "
#             "address=%r | "
#             "title_score=%.3f | "
#             "address_score=%.3f | "
#             "location_score=%.3f | "
#             "success=%s | "
#             "reason=%s",
#             index,
#             total,
#             verified.get(
#                 "title",
#                 "",
#             ),
#             verified.get(
#                 "address",
#                 "",
#             ),
#             verified.get(
#                 "title_score",
#                 0.0,
#             ),
#             verified.get(
#                 "address_score",
#                 0.0,
#             ),
#             verified.get(
#                 "location_score",
#                 0.0,
#             ),
#             verified.get(
#                 "success",
#                 False,
#             ),
#             verified.get(
#                 "reason",
#                 "",
#             ),
#         )

#     except Exception:
#         pass


# def _candidate_prefilter_title_score(
#     candidate,
#     input_title,
# ):
#     """
#     Compute a cheap title score from search-result text/URL
#     before opening the candidate Place.
#     """
#     if not candidate:
#         return 0.0

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     url_title = extract_place_title_from_url(
#         candidate.get("url") or candidate.get("google_maps_url") or ""
#     )

#     texts = " ".join(
#         candidate.get(
#             "texts",
#             [],
#         )
#         or []
#     )

#     return max(
#         title_match_score(
#             input_title,
#             candidate_title,
#             texts,
#         ),
#         title_match_score(
#             input_title,
#             url_title,
#             texts,
#         ),
#     )


# # ============================================================
# # PROCESS SEARCH PAGE
# # ============================================================


# def _process_search_page(
#     page,
#     context,
#     title,
#     address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Process one Google Maps search page.

#     Critical rules:
#         - Direct /maps/place/ redirect -> VERIFY then RETURN.
#         - Do not scan result cards after we are already on a Place page.
#         - Search DOM may hydrate late, so retry candidate extraction a
#           few short times before declaring NO_CANDIDATES.
#         - Candidate card/review text is used only for ranking, never as
#           the authoritative address.
#     """
#     if page is None:
#         return None

#     close_google_popups(page)

#     wait_for_search_results(
#         page,
#         PAGE_TIMEOUT,
#     )

#     # ========================================================
#     # 1. DIRECT PLACE REDIRECT
#     # ========================================================
#     current_url = get_current_google_maps_url(page)

#     if not current_url:
#         try:
#             page.wait_for_timeout(250)
#         except Exception:
#             pass

#         current_url = get_current_google_maps_url(page)

#     if current_url:
#         verified = verify_candidate_address(
#             page,
#             context,
#             {
#                 "url": current_url,
#                 "google_maps_url": current_url,
#                 "title": extract_place_title_from_url(current_url),
#                 "texts": [],
#             },
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         return verified

#     # ========================================================
#     # 2. SEARCH RESULTS PAGE - HYDRATION RETRIES
#     # ========================================================
#     candidates = []

#     for hydration_attempt in range(SEARCH_RESULT_HYDRATION_RETRIES + 1):
#         candidates = extract_result_candidates(page)

#         if candidates:
#             break

#         if hydration_attempt >= SEARCH_RESULT_HYDRATION_RETRIES:
#             break

#         try:
#             page.wait_for_timeout(SEARCH_RESULT_HYDRATION_DELAY_MS)
#         except Exception:
#             time.sleep(SEARCH_RESULT_HYDRATION_DELAY_MS / 1000.0)

#         current_url = get_current_google_maps_url(page)

#         if current_url:
#             return verify_candidate_address(
#                 page,
#                 context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": extract_place_title_from_url(current_url),
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode=search_mode,
#                 logger=logger,
#             )

#     if not candidates:
#         if logger:
#             logger.info(
#                 "NO_CANDIDATES | mode=%s | title=%r | address=%r | page_url=%r",
#                 search_mode,
#                 title,
#                 address,
#                 getattr(page, "url", ""),
#             )

#         return None

#     # ========================================================
#     # 3. RANK + CHEAP PREFILTER
#     # ========================================================
#     for candidate in candidates:
#         prefilter_title_score = _candidate_prefilter_title_score(
#             candidate,
#             title,
#         )

#         candidate["_prefilter_title_score"] = prefilter_title_score

#         if search_mode == "title":
#             candidate["_rank_score"] = prefilter_title_score
#         else:
#             candidate["_rank_score"] = _candidate_text_score(
#                 candidate,
#                 title,
#                 address,
#             )

#     candidates.sort(
#         key=lambda item: item.get(
#             "_rank_score",
#             0.0,
#         ),
#         reverse=True,
#     )

#     limit = (
#         MAX_TITLE_CANDIDATES_TO_VERIFY
#         if search_mode == "title"
#         else MAX_CANDIDATES_TO_VERIFY
#     )

#     min_pre_title = (
#         TITLE_CANDIDATE_PREFILTER_TITLE_MIN
#         if search_mode == "title"
#         else ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN
#     )

#     # ========================================================
#     # 4. VERIFY PLAUSIBLE CANDIDATES
#     # ========================================================
#     best_failed = None
#     opened = 0
#     had_candidate_after_prefilter = False

#     for candidate in candidates:
#         pre_title = candidate.get(
#             "_prefilter_title_score",
#             0.0,
#         )

#         if pre_title < min_pre_title:
#             if logger:
#                 logger.debug(
#                     "SKIP CANDIDATE PREFILTER | "
#                     "mode=%s | title_score=%.3f | "
#                     "candidate_title=%r | url=%r",
#                     search_mode,
#                     pre_title,
#                     candidate.get("title", ""),
#                     candidate.get("url"),
#                 )
#             continue

#         had_candidate_after_prefilter = True
#         opened += 1

#         verified = verify_candidate_address(
#             page,
#             context,
#             candidate,
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         _log_candidate_summary(
#             logger,
#             opened,
#             limit,
#             verified,
#         )

#         if verified.get("page") is not None:
#             page = verified["page"]

#         if verified.get("success"):
#             return verified

#         if search_mode == "title":
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.80
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.20
#             )
#         else:
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.55
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.45
#             )

#         previous_score = (
#             best_failed.get(
#                 "_page_failure_score",
#                 -1.0,
#             )
#             if best_failed
#             else -1.0
#         )

#         if failure_score > previous_score:
#             verified["_page_failure_score"] = failure_score
#             best_failed = verified

#         if opened >= limit:
#             break

#     # ========================================================
#     # 5. FINAL RESULT FOR THIS QUERY
#     # ========================================================
#     if best_failed is None and not had_candidate_after_prefilter:
#         return {
#             "success": False,
#             "url": None,
#             "google_maps_url": None,
#             "title": "",
#             "address": "",
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": (
#                 "TITLE_PREFILTER_REJECTED"
#                 if search_mode == "title"
#                 else "ADDRESS_PREFILTER_REJECTED"
#             ),
#             "page": page,
#             "search_mode": search_mode,
#         }

#     if best_failed is not None:
#         return best_failed

#     return None


# # ============================================================
# # PUBLIC RESULT STANDARDIZATION
# # ============================================================


# def _standardize_search_result(
#     result,
#     success=None,
#     detail_reason=None,
# ):
#     """
#     Normalize the FINAL result returned by GoogleMapsSearchEngine.search().

#     Public contract:
#         success=True
#             status = FOUND
#             reason = VERIFIED

#         success=False
#             status = MISSING
#             reason = NOT_VERIFIED

#     The technical/internal reason is preserved in:
#         detail_reason
#     """
#     result = dict(result or {})

#     if success is None:
#         success = bool(
#             result.get(
#                 "success",
#                 False,
#             )
#         )

#     internal_reason = (
#         detail_reason
#         or result.get("detail_reason")
#         or result.get("reason")
#         or ("VERIFIED_CANDIDATE" if success else "NOT_FOUND")
#     )

#     result["success"] = bool(success)
#     result["status"] = "FOUND" if success else "MISSING"
#     result["reason"] = "VERIFIED" if success else "NOT_VERIFIED"
#     result["detail_reason"] = internal_reason

#     if success:
#         result["google_maps_url"] = result.get("google_maps_url") or result.get("url")
#         result["url"] = result.get("url") or result.get("google_maps_url")
#     else:
#         # A failed public result must never expose a candidate as FOUND.
#         result["google_maps_url"] = None
#         result["url"] = None

#     return result


# def _failure_priority(reason):
#     """
#     Higher value = more useful final diagnostic reason.

#     Prevents a later weak failure such as TITLE_PREFILTER_REJECTED
#     from overwriting a stronger earlier failure such as
#     ADDRESS_UNAVAILABLE or TITLE_LOCATION_MISMATCH.
#     """
#     priority = {
#         "PROVINCE_MISMATCH": 100,
#         "TITLE_LOCATION_MISMATCH": 95,
#         "ADDRESS_MISMATCH": 90,
#         "EXISTING_LOCATION_MISMATCH": 90,
#         "ADDRESS_UNAVAILABLE": 85,
#         "TITLE_MISMATCH": 80,
#         "ADDRESS_TITLE_MISMATCH": 75,
#         "EXISTING_TITLE_MISMATCH": 75,
#         "TITLE_NAVIGATION_FAILED": 60,
#         "NAVIGATION_FAILED": 60,
#         "TITLE_NO_CANDIDATES": 40,
#         "ADDRESS_NO_CANDIDATES": 40,
#         "TITLE_PREFILTER_REJECTED": 30,
#         "ADDRESS_PREFILTER_REJECTED": 30,
#         "PAGE_NONE": 20,
#         "TITLE_EMPTY": 20,
#         "NOT_FOUND": 10,
#     }

#     return priority.get(
#         safe_text(reason),
#         50,
#     )


# # ============================================================
# # SEARCH ENGINE
# # ============================================================


# class GoogleMapsSearchEngine:
#     def __init__(
#         self,
#         page,
#         context=None,
#         logger=None,
#     ):
#         self.page = page
#         self.context = context
#         self.logger = logger

#     # ========================================================
#     # RECOVER PAGE
#     # ========================================================

#     def _recover_page(self):
#         if self.context is not None:
#             try:
#                 for page in reversed(self.context.pages):
#                     if not page.is_closed():
#                         self.page = page
#                         return page

#             except Exception:
#                 pass

#         return self.page

#     # ========================================================
#     # UPDATE PAGE
#     # ========================================================

#     def update_page(
#         self,
#         page,
#     ):
#         if page is None:
#             return

#         self.page = page

#         try:
#             if self.context is None:
#                 self.context = page.context
#         except Exception:
#             pass

#     # ========================================================
#     # LOG
#     # ========================================================

#     def _log_info(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.info(message)
#             except Exception:
#                 pass

#     def _log_warning(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.warning(message)
#             except Exception:
#                 pass

#     def _log_debug(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.debug(message)
#             except Exception:
#                 pass

#     # ========================================================
#     # CURRENT PLACE PAGE
#     # ========================================================

#     def _try_current_place_page(self, title, address):
#         """
#         Reuse current Place only if its URL title is already plausibly
#         related to the requested title. This avoids spending DOM work on
#         the previous record's Place page.
#         """
#         page = self.page

#         if page is None:
#             return None

#         try:
#             current_url = get_current_google_maps_url(page)

#             if not current_url:
#                 return None

#             current_title = extract_place_title_from_url(current_url)

#             if (
#                 title_match_score(
#                     title,
#                     current_title,
#                 )
#                 < 0.88
#             ):
#                 return None

#             return verify_candidate_address(
#                 page,
#                 self.context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": current_title,
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode="existing",
#                 logger=self.logger,
#             )

#         except Exception as error:
#             self._log_debug("Current place page check failed: " + str(error))

#         return None

#     # ========================================================
#     # SEARCH
#     # ========================================================

#     def search(self, title, address, timeout=None):
#         """
#         FINAL PUBLIC SEARCH CONTRACT

#         SUCCESS:
#             {
#                 "success": True,
#                 "status": "FOUND",
#                 "reason": "VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": "...",
#                 ...
#             }

#         FAILURE:
#             {
#                 "success": False,
#                 "status": "MISSING",
#                 "reason": "NOT_VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": None,
#                 ...
#             }

#         Internal verification functions still keep their detailed reasons.
#         """
#         timeout = timeout or PAGE_TIMEOUT
#         title = safe_text(title)
#         address = safe_text(address)

#         base = {
#             "success": False,
#             "status": "MISSING",
#             "url": None,
#             "google_maps_url": None,
#             "title": title,
#             "address": address,
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": "",
#             "detail_reason": "",
#             "attempts": 0,
#             "page": self.page,
#             "search_mode": "",
#         }

#         # ====================================================
#         # INPUT GUARDS
#         # ====================================================
#         if not title:
#             base["detail_reason"] = "TITLE_EMPTY"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="TITLE_EMPTY",
#             )

#         self._recover_page()

#         if self.page is None:
#             base["detail_reason"] = "PAGE_NONE"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="PAGE_NONE",
#             )

#         if self.context is None:
#             try:
#                 self.context = self.page.context
#             except Exception:
#                 pass

#         # ====================================================
#         # CURRENT PLACE REUSE
#         # ====================================================
#         current_result = self._try_current_place_page(
#             title,
#             address,
#         )

#         if current_result and current_result.get("success"):
#             current_result["search_mode"] = "existing"

#             return _standardize_search_result(
#                 current_result,
#                 success=True,
#                 detail_reason=current_result.get(
#                     "reason",
#                     "EXISTING_PLACE_ACCEPTED",
#                 ),
#             )

#         best_failed = None
#         best_failed_priority = -1
#         best_failed_score = -1.0

#         def remember_failure(result, fallback_reason):
#             nonlocal best_failed
#             nonlocal best_failed_priority
#             nonlocal best_failed_score

#             if result is None:
#                 detail = fallback_reason
#                 priority = _failure_priority(detail)
#                 score = 0.0

#                 candidate = {
#                     "success": False,
#                     "page": self.page,
#                     "title_score": 0.0,
#                     "address_score": 0.0,
#                     "location_score": 0.0,
#                     "coordinates": None,
#                     "matched_tokens": [],
#                     "reason": detail,
#                 }

#             else:
#                 detail = result.get("reason") or fallback_reason

#                 priority = _failure_priority(detail)

#                 score = (
#                     result.get(
#                         "title_score",
#                         0.0,
#                     )
#                     * 0.65
#                     + result.get(
#                         "address_score",
#                         0.0,
#                     )
#                     * 0.25
#                     + result.get(
#                         "location_score",
#                         0.0,
#                     )
#                     * 0.10
#                 )

#                 candidate = result

#             # Technical reason quality wins first.
#             # Score breaks ties.
#             if priority > best_failed_priority or (
#                 priority == best_failed_priority and score > best_failed_score
#             ):
#                 best_failed = candidate
#                 best_failed_priority = priority
#                 best_failed_score = score

#         # ====================================================
#         # PHASE 1: ADDRESS
#         # ====================================================
#         address_variants = build_search_variants(
#             title,
#             address,
#         )

#         for idx, query in enumerate(
#             address_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps ADDRESS search {idx}/{len(address_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps ADDRESS navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="address",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "ADDRESS_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "address"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "ADDRESS_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "ADDRESS_NO_CANDIDATES",
#             )

#         # ====================================================
#         # PHASE 2: TITLE FALLBACK
#         # ====================================================
#         title_variants = build_title_search_variants(
#             title,
#             address,
#         )

#         self._log_info(f"ADDRESS PHASE FAILED -> TITLE FALLBACK | {title} | {address}")

#         for idx, query in enumerate(
#             title_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps TITLE fallback {idx}/{len(title_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps TITLE navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="title",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "TITLE_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "title"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "TITLE_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "TITLE_NO_CANDIDATES",
#             )

#         # ====================================================
#         # FINAL FAILURE
#         # ====================================================
#         base["page"] = self.page
#         base["search_mode"] = "title_fallback_exhausted"

#         detail_reason = "NOT_FOUND"

#         if best_failed:
#             detail_reason = best_failed.get("reason") or "NOT_FOUND"

#             base["title_score"] = best_failed.get(
#                 "title_score",
#                 0.0,
#             )

#             base["address_score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["location_score"] = best_failed.get(
#                 "location_score",
#                 0.0,
#             )

#             base["score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["coordinates"] = best_failed.get("coordinates")

#             base["matched_tokens"] = best_failed.get(
#                 "matched_tokens",
#                 [],
#             )

#         base["detail_reason"] = detail_reason

#         self._log_warning(
#             "Google Maps NOT_VERIFIED after ADDRESS + TITLE fallback | "
#             f"title={title!r} | "
#             f"address={address!r} | "
#             f"detail_reason={detail_reason}"
#         )

#         return _standardize_search_result(
#             base,
#             success=False,
#             detail_reason=detail_reason,
#         )


# # ============================================================
# # PUBLIC FUNCTION
# # ============================================================


# def search_google_maps(
#     page,
#     title,
#     address,
#     context=None,
#     logger=None,
#     timeout=None,
# ):
#     return GoogleMapsSearchEngine(
#         page,
#         context=context,
#         logger=logger,
#     ).search(
#         title,
#         address,
#         timeout=timeout,
#     )
# ============================================================
# app/search.py
# ADDRESS-FIRST + TITLE-FALLBACK GOOGLE MAPS SEARCH V10
#
# Goals:
#   1. Address-first
#   2. Prevent province/location mismatch
#   3. Only accept real Google Maps /maps/place/ URLs
#   4. Minimize Playwright DOM operations
#   5. Max 3 search variants
#   6. Max 3 candidates to verify
#   7. Coordinates from URL first
#   8. Deep DOM extraction only when necessary
# ============================================================

# import re
# import time
# import unicodedata
# from difflib import SequenceMatcher
# from urllib.parse import quote, unquote, urlparse

# from config import (
#     SEARCH_POLL_INTERVAL,
#     SEARCH_POLL_COUNT,
#     FINAL_SEARCH_CHECK_DELAY,
#     PAGE_TIMEOUT,
# )

# from .recovery import safe_goto
# from .utils import (
#     safe_text,
#     clean_google_maps_url,
#     build_google_maps_search_url,
# )


# # ============================================================
# # PERFORMANCE CONFIG
# # ============================================================

# # IMPORTANT:
# # Keep these small for 1000+ records.

# SELECTED_PLACE_RETRIES = 1

# SELECTED_PLACE_RETRY_DELAY = min(
#     max(float(SEARCH_POLL_INTERVAL or 0.15), 0.15),
#     0.4,
# )

# # Search
# MAX_SEARCH_VARIANTS = 3
# MAX_TITLE_SEARCH_VARIANTS = 3
# MAX_TITLE_CANDIDATES_TO_VERIFY = 5

# # Candidate verification
# MAX_CANDIDATES_TO_VERIFY = 3

# # Result extraction
# RESULT_CARD_MAX = 25
# CARD_LINK_MAX = 8

# # Direct place links
# PLACE_URL_SCAN_MAX_LINKS = 160
# PLACE_URL_SCAN_MAX_DATA_ELEMENTS = 60

# # Coordinates
# COORDINATE_SCAN_MAX_LINKS = 10
# COORDINATE_SCAN_MAX_DATA_ELEMENTS = 10

# # Metadata
# MAX_TITLE_ELEMENTS = 3
# MAX_ADDRESS_ELEMENTS = 8

# # Verification thresholds
# ADDRESS_TITLE_MIN_SCORE = 0.88
# TITLE_SEARCH_MIN_SCORE = 0.92
# # When the source row has NO address, allow a slightly lower title score
# # if Google Maps exposes a real structured address on the candidate Place.
# TITLE_ONLY_NO_ADDRESS_MIN_SCORE = 0.85
# EXISTING_PLACE_MIN_TITLE_SCORE = 0.95

# # Retry the Maps address panel only for promising title matches.
# STRONG_TITLE_FOR_ADDRESS_RETRY = 0.90
# ADDRESS_RENDER_RETRIES = 2
# ADDRESS_RENDER_RETRY_DELAY_MS = 350

# # Cheap pre-filter before opening candidate URLs.
# # Prevents wasting seconds on obviously wrong businesses.
# ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN = 0.78
# TITLE_CANDIDATE_PREFILTER_TITLE_MIN = 0.82

# # Address DOM extraction
# MAX_ADDRESS_SELECTOR_ELEMENTS = 12

# # Search result hydration.
# # Google Maps often attaches [role=main] before place links appear.
# SEARCH_RESULT_HYDRATION_RETRIES = 3
# SEARCH_RESULT_HYDRATION_DELAY_MS = 300

# TITLE_ONLY_HYDRATION_RETRIES = 6
# TITLE_ONLY_HYDRATION_DELAY_MS = 450


# # ============================================================
# # LEGACY CONFIG
# # ============================================================

# ADDRESS_MATCH_MIN_SCORE = 0.55
# MIN_VALID_PLACE_ADDRESS_SCORE = 0.55
# MIN_VALID_PLACE_LOCATION_SCORE = 0.50
# MIN_LOCATION_MATCHES = 1

# REQUIRE_LOCATION_FOR_PLACE = False


# # ============================================================
# # GENERIC ADDRESS TOKENS
# # ============================================================

# GENERIC_ADDRESS_TOKENS = {
#     "vietnam",
#     "viet nam",
#     "vn",
#     "street",
#     "road",
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
#     "p",
#     "q",
# }


# # ============================================================
# # ADMINISTRATIVE ALIASES
# # ============================================================

# ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS = (
#     frozenset(
#         {
#             "binh dinh",
#             "gia lai",
#         }
#     ),
#     frozenset(
#         {
#             "ha nam",
#             "nam dinh",
#             "ninh binh",
#         }
#     ),
#     frozenset(
#         {
#             "tay ninh",
#             "long an",
#         }
#     ),
# )


# ADMIN_PREFIX_RE = re.compile(
#     r"^(?:phuong|xa|thi\s+tran|quan|huyen|thi\s+xa|"
#     r"thanh\s+pho|tp|tinh)\s+",
#     re.IGNORECASE,
# )


# # ============================================================
# # COORDINATE PATTERNS
# # ============================================================

# COORDINATE_PATTERNS = (
#     re.compile(
#         r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
#     re.compile(
#         r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
# )


# # ============================================================
# # TEXT NORMALIZATION
# # ============================================================


# def _normalize_text(value):
#     if value is None:
#         return ""

#     try:
#         value = safe_text(value)
#     except Exception:
#         try:
#             value = str(value)
#         except Exception:
#             return ""

#     return re.sub(
#         r"\s+",
#         " ",
#         str(value or "").replace("\xa0", " ").strip(),
#     )


# def _remove_accents(value):
#     value = _normalize_text(value).lower()

#     if not value:
#         return ""

#     try:
#         value = unicodedata.normalize("NFKD", value)
#         value = "".join(c for c in value if not unicodedata.combining(c))
#     except Exception:
#         pass

#     return value.replace("đ", "d")


# def _normalize_address(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = value.replace("&", " va ")

#     value = re.sub(
#         r"[/|;]+",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"[-_]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"[()\[\]{}]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"\s*,\s*",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"\s+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r",+",
#         ",",
#         value,
#     )

#     return value.strip(" ,")


# def _normalize_title(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = re.sub(
#         r"[^a-z0-9\s]+",
#         " ",
#         value,
#     )

#     return re.sub(
#         r"\s+",
#         " ",
#         value,
#     ).strip()


# def _extract_lines(value):
#     if value is None:
#         return []

#     try:
#         text = safe_text(value)
#     except Exception:
#         try:
#             text = str(value)
#         except Exception:
#             return []

#     if not text:
#         return []

#     text = str(text).replace("\r", "\n").replace("\xa0", " ")

#     result = []

#     for line in text.split("\n"):
#         line = re.sub(
#             r"\s+",
#             " ",
#             line,
#         ).strip()

#         if line:
#             result.append(line)

#     return result


# def _unique_texts(values):
#     result = []
#     seen = set()

#     for value in values:
#         text = _normalize_text(value)

#         if not text:
#             continue

#         key = text.casefold()

#         if key in seen:
#             continue

#         seen.add(key)
#         result.append(text)

#     return result


# # ============================================================
# # ADDRESS HELPERS
# # ============================================================


# def _clean_address_token(token):
#     return _normalize_address(token).strip(" ,.-")


# def _address_tokens(address):
#     normalized = _normalize_address(address)

#     if not normalized:
#         return []

#     result = []

#     for token in normalized.split(","):
#         token = _clean_address_token(token)

#         if not token or len(token) < 2 or token in GENERIC_ADDRESS_TOKENS:
#             continue

#         result.append(token)

#     return result


# def _canonical_address_token(token):
#     """
#     Normalize one administrative/location token.

#     IMPORTANT:
#         Hàm này KHÔNG chuyển tỉnh cũ -> tỉnh mới.

#     Ví dụ:
#         "Binh Dinh Province" -> "binh dinh"
#         "Bình Định"          -> "binh dinh"
#         "Gia Lai Province"   -> "gia lai"
#         "Quy Nhon City"      -> "quy nhon"

#     Việc xác định hai tỉnh trước/sau sáp nhập có tương đương
#     hay không được xử lý riêng bởi
#     _administrative_locations_equivalent().
#     """

#     token = _normalize_address(token)

#     if not token:
#         return ""

#     # --------------------------------------------------------
#     # Remove postal code
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove English administrative suffixes
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+(?:province|city|district|ward|commune|town)$",
#         "",
#         token,
#         flags=re.IGNORECASE,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove Vietnamese administrative prefixes
#     # --------------------------------------------------------

#     previous = None

#     while token != previous:
#         previous = token

#         token = ADMIN_PREFIX_RE.sub(
#             "",
#             token,
#             count=1,
#         ).strip()

#     # --------------------------------------------------------
#     # Postal code may remain after prefix stripping
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     return token


# def _administrative_locations_equivalent(
#     location_a,
#     location_b,
# ):
#     """
#     Check whether two province/city names should be considered
#     equivalent for location verification.

#     Supports:
#         1. Exact same administrative location
#         2. Old/new administrative names after merger

#     IMPORTANT:
#         This function does NOT rewrite either location.
#     """

#     a = _canonical_address_token(location_a)
#     b = _canonical_address_token(location_b)

#     if not a or not b:
#         return False

#     # Exact same location
#     if a == b:
#         return True

#     # Old/new administrative equivalence
#     for group in ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS:
#         if a in group and b in group:
#             return True

#     return False


# def _expand_address_token(token):
#     token = _normalize_address(token)

#     if not token:
#         return []

#     variants = [token]
#     current = token

#     while True:
#         stripped = ADMIN_PREFIX_RE.sub(
#             "",
#             current,
#             count=1,
#         ).strip()

#         if not stripped or stripped == current:
#             break

#         variants.append(stripped)
#         current = stripped

#     return _unique_texts(variants)


# def _token_matches_address(
#     token,
#     actual_address,
# ):
#     actual = _normalize_address(actual_address)

#     if not actual:
#         return False

#     canonical = _canonical_address_token(token)

#     if not canonical:
#         return False

#     variants = _unique_texts(_expand_address_token(token) + [canonical])

#     actual_parts = [p.strip() for p in actual.split(",") if p.strip()]

#     for variant in variants:
#         if len(variant) < 4:
#             continue

#         pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"

#         if re.search(
#             pattern,
#             actual,
#         ):
#             return True

#         for part in actual_parts:
#             if part == variant:
#                 return True

#             words = variant.split()

#             if len(words) >= 2:
#                 valid_words = [word for word in words if len(word) >= 3]

#                 if valid_words and all(
#                     re.search(
#                         r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])",
#                         part,
#                     )
#                     for word in valid_words
#                 ):
#                     return True

#             if (
#                 SequenceMatcher(
#                     None,
#                     variant,
#                     part,
#                 ).ratio()
#                 >= 0.88
#             ):
#                 return True

#     return False


# # ============================================================
# # LOCATION
# # ============================================================


# def _extract_location_parts(address):
#     result = []

#     for part in _normalize_address(address).split(","):
#         part = part.strip()

#         if not part:
#             continue

#         if part in {
#             "vietnam",
#             "viet nam",
#             "vn",
#         }:
#             continue

#         result.append(part)

#     return result


# def _canonical_location_parts(address):
#     result = []

#     for part in _extract_location_parts(address):
#         value = _canonical_address_token(part)

#         if value and value not in GENERIC_ADDRESS_TOKENS:
#             result.append(value)

#     return result


# def get_address_province(address):
#     parts = _canonical_location_parts(address)

#     if not parts:
#         return ""

#     return parts[-1]


# def _location_similarity(
#     input_location,
#     actual_location,
# ):
#     a = _canonical_address_token(input_location)

#     b = _canonical_address_token(actual_location)

#     if not a or not b:
#         return 0.0

#     # Exact same location
#     if a == b:
#         return 1.0

#     # Before / after administrative merger
#     if _administrative_locations_equivalent(
#         a,
#         b,
#     ):
#         return 1.0

#     if a in b or b in a:
#         return 0.90

#     return SequenceMatcher(
#         None,
#         a,
#         b,
#     ).ratio()


# def _location_match_info(
#     input_address,
#     actual_address,
# ):
#     inputs = _canonical_location_parts(input_address)

#     actuals = _canonical_location_parts(actual_address)

#     if not inputs or not actuals:
#         return {
#             "matches": [],
#             "count": 0,
#             "score": 0.0,
#             "tail_matches": 0,
#             "tail_score": 0.0,
#         }

#     matches = []
#     used = set()

#     for item in inputs:
#         best_score = 0.0
#         best_actual = ""
#         best_index = -1

#         for idx, candidate in enumerate(actuals):
#             if idx in used:
#                 continue

#             score = _location_similarity(
#                 item,
#                 candidate,
#             )

#             if score > best_score:
#                 best_score = score
#                 best_actual = candidate
#                 best_index = idx

#         if best_score >= 0.72:
#             used.add(best_index)

#             matches.append(
#                 {
#                     "input": item,
#                     "actual": best_actual,
#                     "score": round(
#                         best_score,
#                         4,
#                     ),
#                 }
#             )

#     tail = inputs[-2:] if len(inputs) >= 2 else inputs

#     tail_scores = []

#     for item in tail:
#         best = max(
#             (
#                 _location_similarity(
#                     item,
#                     candidate,
#                 )
#                 for candidate in actuals
#             ),
#             default=0.0,
#         )

#         tail_scores.append(best)

#     return {
#         "matches": matches,
#         "count": len(matches),
#         "score": round(
#             len(matches) / len(inputs),
#             4,
#         ),
#         "tail_matches": sum(1 for score in tail_scores if score >= 0.75),
#         "tail_score": round(
#             sum(tail_scores) / len(tail_scores),
#             4,
#         )
#         if tail_scores
#         else 0.0,
#     }


# # ============================================================
# # ADDRESS SCORE
# # ============================================================


# def address_match_score(
#     input_address,
#     actual_address,
# ):
#     a = _normalize_address(input_address)
#     b = _normalize_address(actual_address)

#     if not a or not b:
#         return {
#             "score": 0.0,
#             "matched_tokens": [],
#             "total_tokens": 0,
#             "location_matches": 0,
#             "location_score": 0.0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "strong_match": False,
#         }

#     if a == b:
#         tokens = _address_tokens(input_address)

#         location_parts = _canonical_location_parts(input_address)

#         return {
#             "score": 1.0,
#             "matched_tokens": tokens,
#             "total_tokens": len(tokens),
#             "location_matches": len(location_parts),
#             "location_score": 1.0,
#             "tail_location_matches": len(location_parts[-2:]),
#             "tail_location_score": 1.0,
#             "strong_match": True,
#         }

#     tokens = _address_tokens(input_address)

#     matched = [
#         token
#         for token in tokens
#         if _token_matches_address(
#             token,
#             actual_address,
#         )
#     ]

#     token_score = len(matched) / len(tokens) if tokens else 0.0

#     location = _location_match_info(
#         input_address,
#         actual_address,
#     )

#     location_score = location["score"]

#     substring_bonus = 0.15 if len(a) >= 10 and a in b else 0.0

#     score = min(
#         1.0,
#         token_score * 0.60 + location_score * 0.40 + substring_bonus,
#     )

#     strong = (
#         score >= 0.80
#         or (
#             score >= ADDRESS_MATCH_MIN_SCORE
#             and location["count"] >= MIN_LOCATION_MATCHES
#         )
#         or (len(tokens) <= 2 and bool(matched) and score >= 0.40)
#     )

#     return {
#         "score": round(
#             score,
#             4,
#         ),
#         "matched_tokens": matched,
#         "total_tokens": len(tokens),
#         "location_matches": location["count"],
#         "location_score": round(
#             location_score,
#             4,
#         ),
#         "tail_location_matches": location["tail_matches"],
#         "tail_location_score": location["tail_score"],
#         "strong_match": bool(strong),
#     }


# def address_is_related(
#     input_address,
#     actual_address,
# ):
#     return bool(
#         address_match_score(
#             input_address,
#             actual_address,
#         ).get("strong_match")
#     )


# # ============================================================
# # TITLE
# # ============================================================


# def title_match_score(
#     input_title,
#     actual_title,
#     actual_text="",
# ):
#     a = _normalize_title(input_title)

#     b = _normalize_title(actual_title)

#     combined = _normalize_title(actual_text)

#     if not a:
#         return 0.0

#     if a == b:
#         return 1.0

#     if b and a in b:
#         return 0.95

#     if b and b in a:
#         return 0.90

#     sequence = (
#         SequenceMatcher(
#             None,
#             a,
#             b,
#         ).ratio()
#         if b
#         else 0.0
#     )

#     wanted = {x for x in a.split() if len(x) >= 3}

#     got = {x for x in combined.split() if len(x) >= 3}

#     overlap = len(wanted & got) / len(wanted) if wanted else 0.0

#     return round(
#         max(
#             sequence,
#             overlap,
#         ),
#         4,
#     )


# def result_matches_title(
#     input_title,
#     result_title,
# ):
#     return (
#         title_match_score(
#             input_title,
#             result_title,
#         )
#         >= 0.75
#     )


# # ============================================================
# # ADDRESS RESULT VALIDATION
# # ============================================================


# def result_matches_address(
#     input_address,
#     result_address,
# ):
#     actual_address = _normalize_text(result_address)

#     if (
#         not actual_address
#         or not re.search(
#             r"[a-z0-9]",
#             actual_address,
#             re.IGNORECASE,
#         )
#         or not _address_tokens(actual_address)
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         actual_address,
#     )

#     if info.get("strong_match"):
#         return True

#     location_matches = info.get(
#         "location_matches",
#         0,
#     )

#     tail_matches = info.get(
#         "tail_location_matches",
#         0,
#     )

#     return location_matches >= 2 and tail_matches >= 1


# def _result_matches_address(
#     input_address,
#     result_address,
# ):
#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# # ============================================================
# # SEARCH IDENTITY
# # ============================================================


# def _matches_search_identity(
#     input_title,
#     input_address,
#     result_title,
#     result_address,
# ):
#     """
#     Strict validation used only when we HAVE metadata.

#     If metadata is unavailable, caller can use URL-level
#     validation instead of forcing expensive extraction.
#     """

#     if not result_matches_title(
#         input_title,
#         result_title,
#     ):
#         return False

#     if not _normalize_text(input_address):
#         return True

#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# def _matches_title_search_result(
#     input_title,
#     result_title,
#     result_address,
# ):
#     return result_matches_title(
#         input_title,
#         result_title,
#     ) and _is_usable_address_text(result_address)


# # ============================================================
# # ADDRESS USABILITY
# # ============================================================


# def _is_usable_address_text(value):
#     """
#     Return True only for text that plausibly represents a postal/street
#     address or useful locality string.

#     IMPORTANT:
#     Reject Google Maps rating/review snippets such as:
#         "4,7(1.060)"
#         "4.7 (1,060)"
#         "4,3(1.699)"
#         "1.060 reviews"
#     """
#     text = _normalize_text(value)

#     if not text:
#         return False

#     # Private-use glyphs / escaped unicode noise.
#     if re.search(
#         r"\\u[0-9a-f]{4}",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     if any(unicodedata.category(char) == "Co" for char in text):
#         return False

#     # --------------------------------------------------------
#     # Ratings / review counts
#     # --------------------------------------------------------
#     compact = re.sub(r"\s+", "", text)

#     rating_patterns = (
#         r"^[0-5](?:[.,]\d)?\(\d[\d.,]*\)$",
#         r"^[0-5](?:[.,]\d)?$",
#         r"^\d[\d.,]*\s*(?:reviews?|đánh\s*giá|ratings?)$",
#         r"^[0-5](?:[.,]\d)?\s*\(\d[\d.,]*\)\s*(?:reviews?|đánh\s*giá|ratings?)?$",
#     )

#     for pattern in rating_patterns:
#         if re.fullmatch(
#             pattern,
#             text,
#             re.IGNORECASE,
#         ) or re.fullmatch(
#             pattern,
#             compact,
#             re.IGNORECASE,
#         ):
#             return False

#     # Pure numeric-ish text is not an address.
#     if re.fullmatch(
#         r"[\d\s.,()/+-]+",
#         text,
#     ):
#         return False

#     # Category-only labels.
#     if re.fullmatch(
#         r"(?:khách sạn|hotel|nhà nghỉ|"
#         r"resort|restaurant|nhà hàng|"
#         r"homestay|lodge|love hotel|"
#         r"serviced apartment)"
#         r"(?:\s+\d+\s+sao)?",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     normalized = _normalize_address(text)

#     if not normalized:
#         return False

#     # --------------------------------------------------------
#     # Positive address evidence
#     # --------------------------------------------------------
#     has_digit = bool(
#         re.search(
#             r"\d",
#             normalized,
#         )
#     )

#     has_comma = "," in normalized

#     location_words = (
#         "vietnam",
#         "viet nam",
#         "gia lai",
#         "binh dinh",
#         "quy nhon",
#         "pleiku",
#         "street",
#         "road",
#         "ward",
#         "district",
#         "province",
#         "city",
#         "phuong",
#         "quan",
#         "huyen",
#         "tinh",
#         "xa",
#         "thanh pho",
#         "thi tran",
#         "thi xa",
#         "duong",
#         "đuong",
#         "đường",
#     )

#     has_location_word = any(token in normalized for token in location_words)

#     # Street/address usually has a number + locality separator/word.
#     if has_digit and (has_comma or has_location_word):
#         return True

#     # Locality-only address strings can still be useful.
#     if has_comma and has_location_word:
#         return True

#     # Require at least two comma-separated meaningful parts.
#     parts = [part.strip() for part in normalized.split(",") if part.strip()]

#     if len(parts) >= 2 and any(len(part) >= 3 for part in parts):
#         return True

#     return False


# # ============================================================
# # COORDINATES
# # ============================================================


# def _valid_lat_lng(
#     lat,
#     lng,
# ):
#     try:
#         lat = float(lat)
#         lng = float(lng)
#     except Exception:
#         return False

#     return -90 <= lat <= 90 and -180 <= lng <= 180


# def _extract_coordinates_from_text(
#     text,
# ):
#     if not text:
#         return None

#     try:
#         text = str(text)
#     except Exception:
#         return None

#     for pattern in COORDINATE_PATTERNS:
#         match = pattern.search(text)

#         if not match:
#             continue

#         if _valid_lat_lng(
#             match.group(1),
#             match.group(2),
#         ):
#             return (
#                 float(match.group(1)),
#                 float(match.group(2)),
#             )

#     return None


# def extract_coordinates_from_url(
#     url,
# ):
#     coordinates = _extract_coordinates_from_text(url)

#     if coordinates:
#         return coordinates

#     return None, None


# def get_current_page_coordinates(page):
#     try:
#         coordinates = _extract_coordinates_from_text(page.url or "")

#         if coordinates:
#             return coordinates

#     except Exception:
#         pass

#     return None, None


# def extract_coordinates_from_page(page):
#     """
#     FAST coordinate extraction.

#     Priority:
#         1. current URL
#         2. small number of links
#         3. small number of data attributes

#     This function is intentionally NOT expensive.
#     """

#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     try:
#         value = _extract_coordinates_from_text(page.url or "")

#         if value:
#             return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 2. Links
#     # --------------------------------------------------------

#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             COORDINATE_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             value = _extract_coordinates_from_text(links.nth(i).get_attribute("href"))

#             if value:
#                 return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             COORDINATE_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 value = _extract_coordinates_from_text(element.get_attribute(attr))

#                 if value:
#                     return value

#     except Exception:
#         pass

#     return None


# # ============================================================
# # GOOGLE MAPS URL
# # ============================================================


# def _is_google_maps_url(url):
#     if not url:
#         return False

#     try:
#         parsed = urlparse(str(url).strip())
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return False

#     return (
#         parsed.scheme
#         in {
#             "http",
#             "https",
#         }
#         and parsed.hostname
#         in {
#             "google.com",
#             "www.google.com",
#             "maps.google.com",
#         }
#         and parsed.path.lower().startswith("/maps")
#     )


# def is_google_maps_url(url):
#     return _is_google_maps_url(url)


# def _is_google_maps_place_url(url):
#     if not _is_google_maps_url(url):
#         return False

#     try:
#         value = str(url).strip().lower()
#     except Exception:
#         return False

#     if "/maps/search" in value:
#         return False

#     return "/maps/place/" in value or "/maps/place?" in value


# def is_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _is_usable_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _clean_place_url(url):
#     if not url:
#         return None

#     try:
#         value = str(url).strip()
#     except Exception:
#         return None

#     try:
#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     if _is_google_maps_place_url(value):
#         return value

#     return None


# def get_current_google_maps_url(page):
#     try:
#         return _clean_place_url(page.url or "")
#     except Exception:
#         return None


# def get_current_google_maps_place_url_robust(page):
#     """
#     Detect a direct Google Maps /maps/place/ redirect as robustly as possible.
#     """
#     if page is None:
#         return None

#     try:
#         raw_url = str(page.url or "").strip()
#     except Exception:
#         raw_url = ""

#     if raw_url:
#         if _is_google_maps_place_url(raw_url):
#             return raw_url

#         try:
#             decoded_url = unquote(raw_url)
#             if _is_google_maps_place_url(decoded_url):
#                 return decoded_url
#         except Exception:
#             pass

#     return get_current_google_maps_url(page)


# def extract_place_title_from_url(
#     url,
# ):
#     if not url:
#         return ""

#     try:
#         path = urlparse(str(url)).path
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return ""

#     match = re.search(
#         r"/maps/place/([^/]+)",
#         path,
#         re.IGNORECASE,
#     )

#     if not match:
#         return ""

#     return _normalize_text(
#         unquote(match.group(1)).replace(
#             "+",
#             " ",
#         )
#     )


# def extract_google_maps_url_from_href(
#     href,
# ):
#     if not href:
#         return None

#     try:
#         value = (
#             str(href)
#             .strip()
#             .replace(
#                 "&amp;",
#                 "&",
#             )
#         )

#         value = (
#             value.replace(
#                 "\\/",
#                 "/",
#             )
#             .replace(
#                 "\\u003d",
#                 "=",
#             )
#             .replace(
#                 "\\u0026",
#                 "&",
#             )
#         )

#         if value.startswith("/maps/"):
#             value = "https://www.google.com" + value

#         decoded = unquote(value)

#         if _is_google_maps_place_url(decoded):
#             value = decoded

#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     return _clean_place_url(href)


# # ============================================================
# # POPUPS
# # ============================================================


# def close_google_popups(page):
#     if page is None:
#         return

#     selectors = [
#         'button:has-text("Accept all")',
#         'button:has-text("I agree")',
#         'button:has-text("Đồng ý")',
#         'button:has-text("Chấp nhận tất cả")',
#         '[aria-label="Accept all"]',
#         '[aria-label="I agree"]',
#         '[aria-label="Chấp nhận tất cả"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             if locator.count() and locator.first.is_visible(timeout=200):
#                 locator.first.click(timeout=700)

#                 break

#         except Exception:
#             continue


# # ============================================================
# # FAST WAIT
# # ============================================================


# def wait_for_search_results(
#     page,
#     timeout=None,
# ):
#     """
#     Much cheaper than repeatedly calling count()
#     on multiple selectors.
#     """

#     if page is None:
#         return False

#     timeout = min(
#         int(timeout or PAGE_TIMEOUT),
#         5000,
#     )

#     try:
#         if page.is_closed():
#             return False
#     except Exception:
#         return False

#     selector = 'a[href*="/maps/place/"], div.Nv2PK, [role="main"]'

#     try:
#         page.locator(selector).first.wait_for(
#             state="attached",
#             timeout=timeout,
#         )

#         return True

#     except Exception:
#         return False


# # ============================================================
# # PLAYWRIGHT TEXT
# # ============================================================


# def _read_locator_text(locator):
#     if locator is None:
#         return ""

#     try:
#         text = locator.inner_text(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     try:
#         text = locator.text_content(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # SELECTED PLACE TITLE
# # ============================================================


# def extract_selected_place_title(
#     page,
#     expected_title=None,
# ):
#     if page is None:
#         return ""

#     values = []

#     for selector in (
#         '[role="main"] h1',
#         "h1",
#         '[data-item-id="title"]',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_TITLE_ELEMENTS,
#             )

#             for i in range(count):
#                 text = _read_locator_text(locator.nth(i))

#                 if text:
#                     values.append(text)

#         except Exception:
#             continue

#     values = _unique_texts(values)

#     if not values:
#         return ""

#     if expected_title:
#         return max(
#             values,
#             key=lambda x: title_match_score(
#                 expected_title,
#                 x,
#             ),
#         )

#     return values[0]


# # ============================================================
# # SELECTED PLACE ADDRESS
# # ============================================================


# def extract_selected_place_address(
#     page,
#     expected_address="",
# ):
#     """
#     Extract ONLY official/structured Google Maps address UI.

#     IMPORTANT:
#         Do NOT use arbitrary main-panel text here.
#         Reviews/descriptions often contain street names and must never
#         be treated as the Place address.
#     """
#     if page is None:
#         return ""

#     candidates = []

#     selectors = [
#         'button[data-item-id="address"]',
#         '[data-item-id="address"]',
#         '[role="main"] button[data-item-id="address"]',
#         '[role="main"] [data-item-id="address"]',
#         'button[aria-label^="Địa chỉ"]',
#         'button[aria-label^="Address"]',
#         '[role="main"] button[aria-label^="Địa chỉ"]',
#         '[role="main"] button[aria-label^="Address"]',
#         '[aria-label^="Địa chỉ"]',
#         '[aria-label^="Address"]',
#         '[data-tooltip*="Địa chỉ"]',
#         '[data-tooltip*="Address"]',
#         '[title*="Địa chỉ"]',
#         '[title*="Address"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_ADDRESS_SELECTOR_ELEMENTS,
#             )

#             for i in range(count):
#                 element = locator.nth(i)

#                 # visible text
#                 text_value = _read_locator_text(element)

#                 if _is_usable_address_text(text_value):
#                     candidates.append(text_value)

#                 # structured attributes
#                 for attr in (
#                     "aria-label",
#                     "data-tooltip",
#                     "title",
#                 ):
#                     try:
#                         value = element.get_attribute(attr)
#                     except Exception:
#                         value = None

#                     if not value:
#                         continue

#                     value = _normalize_text(value)

#                     value = re.sub(
#                         r"^(?:địa\s*chỉ|address)\s*:\s*",
#                         "",
#                         value,
#                         flags=re.IGNORECASE,
#                     )

#                     if _is_usable_address_text(value):
#                         candidates.append(value)

#         except Exception:
#             continue

#     candidates = [
#         value for value in _unique_texts(candidates) if _is_usable_address_text(value)
#     ]

#     if not candidates:
#         return ""

#     if expected_address:

#         def score_address(value):
#             info = address_match_score(
#                 expected_address,
#                 value,
#             )

#             return (
#                 info.get("score", 0.0),
#                 info.get("location_score", 0.0),
#             )

#         return max(
#             candidates,
#             key=score_address,
#         )

#     return candidates[0]


# # ============================================================
# # MAIN TEXT FALLBACK
# # ============================================================


# def _find_best_address_from_main_text(
#     page,
#     expected_address="",
# ):
#     if page is None:
#         return ""

#     try:
#         main = page.locator('[role="main"]').first

#         if not main.count():
#             return ""

#         text = main.inner_text(timeout=1800)

#         lines = _extract_lines(text)

#         if not lines:
#             return ""

#         if expected_address:
#             scored = []

#             for line in lines:
#                 info = address_match_score(
#                     expected_address,
#                     line,
#                 )

#                 if info["score"] >= 0.20 or info["location_score"] >= 0.25:
#                     scored.append(
#                         (
#                             info["score"],
#                             info["location_score"],
#                             line,
#                         )
#                     )

#             if scored:
#                 scored.sort(
#                     key=lambda x: (
#                         x[0],
#                         x[1],
#                     ),
#                     reverse=True,
#                 )

#                 return scored[0][2]

#         for line in lines:
#             normalized = _normalize_address(line)

#             if not normalized:
#                 continue

#             has_number = bool(
#                 re.search(
#                     r"\d",
#                     normalized,
#                 )
#             )

#             has_comma = "," in normalized

#             has_location_word = any(
#                 token in normalized
#                 for token in (
#                     "vietnam",
#                     "viet nam",
#                     "street",
#                     "road",
#                     "ward",
#                     "district",
#                     "province",
#                     "city",
#                     "phuong",
#                     "quan",
#                     "huyen",
#                     "tinh",
#                     "xa",
#                     "thanh pho",
#                     "thi tran",
#                     "thi xa",
#                 )
#             )

#             if has_number and (has_comma or has_location_word):
#                 return line

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # CANDIDATE ADDRESS
# # ============================================================


# def _find_best_address_from_candidate(
#     candidate,
#     expected_address,
# ):
#     if not candidate:
#         return ""

#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     usable = [text for text in _unique_texts(texts) if _is_usable_address_text(text)]

#     if not usable:
#         return ""

#     return max(
#         usable,
#         key=lambda text: address_match_score(
#             expected_address,
#             text,
#         ).get(
#             "score",
#             0.0,
#         ),
#     )


# # ============================================================
# # PLACE URL EXTRACTION
# # ============================================================


# def extract_place_url_from_page(
#     page,
# ):
#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     current = get_current_google_maps_url(page)

#     if current:
#         return current

#     # --------------------------------------------------------
#     # 2. Direct place links
#     # --------------------------------------------------------

#     try:
#         links = page.locator('a[href*="/maps/place/"]')

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             url = extract_google_maps_url_from_href(links.nth(i).get_attribute("href"))

#             if url:
#                 return url

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 url = extract_google_maps_url_from_href(element.get_attribute(attr))

#                 if url:
#                     return url

#     except Exception:
#         pass

#     return None


# # ============================================================
# # RESULT CARD
# # ============================================================


# def _extract_card_texts(card):
#     values = []

#     try:
#         values.extend(_extract_lines(card.inner_text(timeout=700)))

#     except Exception:
#         pass

#     try:
#         aria = card.get_attribute("aria-label")

#         if aria:
#             values.append(aria)

#     except Exception:
#         pass

#     return _unique_texts(values)


# def _extract_title_from_card_texts(
#     texts,
# ):
#     if not texts:
#         return ""

#     return texts[0]


# def _candidate_text_score(
#     candidate,
#     input_title,
#     input_address,
# ):
#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     combined = " ".join(texts)

#     title_score = title_match_score(
#         input_title,
#         candidate.get(
#             "title",
#             "",
#         ),
#         combined,
#     )

#     address_info = address_match_score(
#         input_address,
#         combined,
#     )

#     return (
#         title_score * 0.45
#         + address_info["score"] * 0.40
#         + address_info["location_score"] * 0.15
#     )


# # ============================================================
# # EXTRACT SEARCH CANDIDATES
# # ============================================================


# def extract_result_candidates(page):
#     """
#     Extract Google Maps Place candidates without depending on one
#     specific Maps DOM version.

#     Sources:
#         1. Result cards
#         2. ALL anchors whose href resolves to /maps/place/
#         3. data-url / data-href
#         4. raw HTML fallback for hydrated/virtualized Maps markup

#     This function NEVER navigates.
#     """
#     if page is None:
#         return []

#     candidates = []
#     seen = set()

#     def add_candidate(
#         url,
#         title="",
#         texts=None,
#     ):
#         url = extract_google_maps_url_from_href(url)

#         if not url:
#             return

#         if url in seen:
#             return

#         seen.add(url)

#         title = _normalize_text(title)

#         if not title:
#             title = extract_place_title_from_url(url)

#         values = _unique_texts(list(texts or []) + ([title] if title else []))

#         candidates.append(
#             {
#                 "url": url,
#                 "google_maps_url": url,
#                 "title": title,
#                 "texts": values,
#             }
#         )

#     # ========================================================
#     # 1. RESULT CARDS
#     # ========================================================
#     for selector in (
#         "div.Nv2PK",
#         "div.bfdHYd",
#         '[role="article"]',
#         '[role="feed"] > div',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 RESULT_CARD_MAX,
#             )

#             if not count:
#                 continue

#             for i in range(count):
#                 try:
#                     card = locator.nth(i)
#                     texts = _extract_card_texts(card)

#                     links = card.locator('a[href*="/maps/place/"]')

#                     link_count = min(
#                         links.count(),
#                         CARD_LINK_MAX,
#                     )

#                     for j in range(link_count):
#                         href = links.nth(j).get_attribute("href")

#                         add_candidate(
#                             href,
#                             title=_extract_title_from_card_texts(texts),
#                             texts=texts,
#                         )

#                     if link_count == 0:
#                         links = card.locator("a[href]")

#                         link_count = min(
#                             links.count(),
#                             CARD_LINK_MAX,
#                         )

#                         for j in range(link_count):
#                             link = links.nth(j)
#                             href = link.get_attribute("href")

#                             url = extract_google_maps_url_from_href(href)

#                             if not url:
#                                 continue

#                             link_text = _read_locator_text(link)

#                             add_candidate(
#                                 url,
#                                 title=link_text
#                                 or _extract_title_from_card_texts(texts),
#                                 texts=texts + ([link_text] if link_text else []),
#                             )

#                 except Exception:
#                     continue

#         except Exception:
#             continue

#     # ========================================================
#     # 2. GLOBAL ANCHORS
#     # ========================================================
#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             try:
#                 link = links.nth(i)
#                 href = link.get_attribute("href")

#                 url = extract_google_maps_url_from_href(href)

#                 if not url:
#                     continue

#                 link_text = _read_locator_text(link)

#                 aria = ""
#                 try:
#                     aria = link.get_attribute("aria-label") or ""
#                 except Exception:
#                     pass

#                 title = link_text or aria or extract_place_title_from_url(url)

#                 add_candidate(
#                     url,
#                     title=title,
#                     texts=[link_text, aria],
#                 )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 3. DATA ATTRIBUTES
#     # ========================================================
#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             try:
#                 element = elements.nth(i)

#                 element_text = _read_locator_text(element)

#                 for attr in (
#                     "data-url",
#                     "data-href",
#                 ):
#                     value = element.get_attribute(attr)

#                     if not value:
#                         continue

#                     add_candidate(
#                         value,
#                         title=element_text,
#                         texts=[element_text],
#                     )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 4. RAW HTML FALLBACK
#     # ========================================================
#     if not candidates:
#         try:
#             html = page.content()

#             if html:
#                 raw_patterns = (
#                     r'https?://www\.google\.com/maps/place/[^"\'<>\s]+',
#                     r'https?://google\.com/maps/place/[^"\'<>\s]+',
#                     r'/maps/place/[^"\'<>\s]+',
#                 )

#                 for pattern in raw_patterns:
#                     for match in re.findall(
#                         pattern,
#                         html,
#                         flags=re.IGNORECASE,
#                     ):
#                         value = (
#                             match.replace("&amp;", "&")
#                             .replace("\\/", "/")
#                             .replace("\\u003d", "=")
#                             .replace("\\u0026", "&")
#                         )

#                         if value.startswith("/maps/"):
#                             value = "https://www.google.com" + value

#                         add_candidate(
#                             value,
#                             title=extract_place_title_from_url(value),
#                             texts=[],
#                         )

#                         if len(candidates) >= RESULT_CARD_MAX:
#                             break

#                     if candidates:
#                         break

#         except Exception:
#             pass

#     return candidates


# # ============================================================
# # CLICK / NAVIGATE
# # ============================================================


# def click_candidate(
#     page,
#     candidate,
#     context=None,
#     logger=None,
# ):
#     if page is None or not candidate:
#         return page, False, 0

#     url = candidate.get("url") or candidate.get("google_maps_url")

#     if not url:
#         return page, False, 0

#     if not _is_google_maps_place_url(url):
#         return page, False, 0

#     if context is None:
#         try:
#             context = page.context
#         except Exception:
#             pass

#     try:
#         if context is not None:
#             return safe_goto(
#                 page,
#                 context,
#                 url,
#                 logger=logger,
#                 timeout=PAGE_TIMEOUT,
#             )

#         page.goto(
#             url,
#             wait_until="domcontentloaded",
#             timeout=PAGE_TIMEOUT,
#         )

#         return page, True, 1

#     except Exception as error:
#         if logger:
#             logger.warning("Candidate navigation error: " + str(error)[:250])

#         return page, False, 1


# # ============================================================
# # FAST PROVINCE GUARD
# # ============================================================


# def _province_from_text(
#     text,
# ):
#     parts = _canonical_location_parts(text)

#     if not parts:
#         return ""

#     return parts[-1]


# def _province_matches(
#     input_address,
#     actual_address,
# ):
#     """
#     Protect against Google Maps jumping to another province.

#     Supports:
#         - Same province
#         - Province before/after administrative merger

#     Returns:
#         True
#             Province matches or is administratively equivalent.

#         False
#             Definitely another province.

#         None
#             Actual Maps address is unavailable, so cannot decide.
#     """

#     expected_province = get_address_province(
#         input_address,
#     )

#     actual_province = get_address_province(
#         actual_address,
#     )

#     # Input does not provide province information.
#     if not expected_province:
#         return True

#     # Google Maps address unavailable.
#     if not actual_province:
#         return None

#     return _administrative_locations_equivalent(
#         expected_province,
#         actual_province,
#     )


# # ============================================================
# # CANDIDATE VERIFICATION HELPERS
# # ============================================================


# def _candidate_location_evidence(
#     candidate,
#     input_address,
# ):
#     if not candidate or not _normalize_text(input_address):
#         return False

#     texts = candidate.get("texts", []) or []

#     if not texts:
#         return False

#     combined = " | ".join(
#         text for text in _unique_texts(texts) if _normalize_text(text)
#     )

#     if not combined:
#         return False

#     expected_province = get_address_province(input_address)
#     actual_province = get_address_province(combined)

#     if (
#         expected_province
#         and actual_province
#         and not _administrative_locations_equivalent(
#             expected_province,
#             actual_province,
#         )
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         combined,
#     )

#     return bool(
#         info.get("location_matches", 0) >= 1
#         and (
#             info.get("location_score", 0.0) >= 0.25
#             or info.get("tail_location_matches", 0) >= 1
#         )
#     )


# def _extract_candidate_address_with_retry(
#     page,
#     candidate,
#     input_address,
#     title_score,
# ):
#     """
#     SAFE ADDRESS EXTRACTION.

#     SOURCE OF TRUTH:
#         ONLY official Google Maps address DOM.

#     NEVER use:
#         - candidate card text
#         - reviews
#         - descriptions
#         - main panel arbitrary text
#         - combined candidate text

#     This prevents review snippets such as:
#         "... ở 11 An Dương Vương QN ..."
#     from becoming actual_address.
#     """

#     if page is None:
#         return ""

#     # ========================================================
#     # 1. OFFICIAL GOOGLE MAPS ADDRESS ELEMENT ONLY
#     # ========================================================
#     try:
#         actual_address = extract_selected_place_address(
#             page,
#             expected_address=input_address,
#         )
#     except Exception:
#         actual_address = ""

#     if actual_address and _is_usable_address_text(actual_address):
#         return actual_address

#     # ========================================================
#     # 2. ONLY RETRY FOR STRONG TITLE
#     # ========================================================
#     if title_score < STRONG_TITLE_FOR_ADDRESS_RETRY:
#         return ""

#     for _ in range(ADDRESS_RENDER_RETRIES):
#         try:
#             page.wait_for_timeout(ADDRESS_RENDER_RETRY_DELAY_MS)
#         except Exception:
#             time.sleep(ADDRESS_RENDER_RETRY_DELAY_MS / 1000.0)

#         try:
#             actual_address = extract_selected_place_address(
#                 page,
#                 expected_address=input_address,
#             )
#         except Exception:
#             actual_address = ""

#         if actual_address and _is_usable_address_text(actual_address):
#             return actual_address

#     return ""


# def _is_nearly_exact_title_match(
#     input_title,
#     actual_title,
#     actual_url,
# ):
#     """
#     Conservative identity fallback used only when address UI is absent.

#     Requires:
#         - Place URL
#         - title >= 0.99
#         - URL title itself also strongly matches the requested title
#     """
#     if not _is_google_maps_place_url(actual_url):
#         return False

#     direct_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     url_score = title_match_score(
#         input_title,
#         url_title,
#     )

#     return direct_score >= 0.99 and url_score >= 0.95


# # ============================================================
# # CANDIDATE VERIFY
# # ============================================================


# def verify_candidate_address(
#     page,
#     context,
#     candidate,
#     input_title,
#     input_address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Verify a Google Maps Place candidate.

#     Rules:
#         - /maps/place/ is required but never sufficient by itself.
#         - We NEVER infer address/province from reviews/main text.
#         - If official address exists, verify it normally.
#         - If the SOURCE row has no address, a strong title match plus a real
#           structured Maps address may recover the missing address and accept
#           the candidate without running a second address search.
#         - If official address is absent, only a nearly-exact title match
#           may survive without an address, and only in title/address search
#           mode. Existing-link reuse remains stricter.
#     """
#     result = {
#         "success": False,
#         "url": None,
#         "google_maps_url": None,
#         "title": "",
#         "address": "",
#         "score": 0.0,
#         "address_score": 0.0,
#         "title_score": 0.0,
#         "matched_tokens": [],
#         "coordinates": None,
#         "reason": "",
#         "page": page,
#         "attempts": 0,
#         "search_mode": search_mode,
#         "location_score": 0.0,
#         "location_matches": 0,
#         "tail_location_matches": 0,
#         "tail_location_score": 0.0,
#     }

#     if page is None:
#         result["reason"] = "PAGE_NONE"
#         return result

#     if not candidate:
#         result["reason"] = "CANDIDATE_NONE"
#         return result

#     candidate_url = candidate.get("url") or candidate.get("google_maps_url")

#     if not _is_google_maps_place_url(candidate_url):
#         result["reason"] = "INVALID_CANDIDATE_URL"
#         return result

#     page, success, attempts = click_candidate(
#         page,
#         candidate,
#         context=context,
#         logger=logger,
#     )

#     result["page"] = page
#     result["attempts"] = attempts

#     if not success or page is None:
#         result["reason"] = "NAVIGATION_FAILED"
#         return result

#     try:
#         page.wait_for_timeout(150)
#     except Exception:
#         pass

#     close_google_popups(page)

#     actual_url = get_current_google_maps_url(page) or _clean_place_url(candidate_url)

#     if not actual_url:
#         result["reason"] = "NO_VALID_MAPS_PLACE_URL"
#         return result

#     coordinates = _extract_coordinates_from_text(
#         actual_url,
#     )

#     # --------------------------------------------------------
#     # Title
#     # --------------------------------------------------------
#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     actual_title = url_title or candidate_title or ""

#     title_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     if title_score < 0.90:
#         try:
#             dom_title = extract_selected_place_title(
#                 page,
#                 expected_title=input_title,
#             )
#         except Exception:
#             dom_title = ""

#         if dom_title:
#             dom_score = title_match_score(
#                 input_title,
#                 dom_title,
#             )

#             if dom_score > title_score:
#                 actual_title = dom_title
#                 title_score = dom_score

#     # --------------------------------------------------------
#     # Official/structured address only
#     # --------------------------------------------------------
#     actual_address = _extract_candidate_address_with_retry(
#         page,
#         candidate,
#         input_address,
#         title_score,
#     )

#     address_available = bool(_is_usable_address_text(actual_address))

#     if address_available:
#         info = address_match_score(
#             input_address,
#             actual_address,
#         )
#     else:
#         info = {
#             "score": 0.0,
#             "location_score": 0.0,
#             "location_matches": 0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "matched_tokens": [],
#         }

#     address_score = info.get(
#         "score",
#         0.0,
#     )

#     location_score = info.get(
#         "location_score",
#         0.0,
#     )

#     province_result = (
#         _province_matches(
#             input_address,
#             actual_address,
#         )
#         if address_available
#         else None
#     )

#     exact_identity = _is_nearly_exact_title_match(
#         input_title,
#         actual_title,
#         actual_url,
#     )

#     result.update(
#         {
#             "url": actual_url,
#             "google_maps_url": actual_url,
#             "title": actual_title,
#             "address": actual_address,
#             "title_score": title_score,
#             "address_score": address_score,
#             "score": address_score,
#             "location_score": location_score,
#             "location_matches": info.get(
#                 "location_matches",
#                 0,
#             ),
#             "tail_location_matches": info.get(
#                 "tail_location_matches",
#                 0,
#             ),
#             "tail_location_score": info.get(
#                 "tail_location_score",
#                 0.0,
#             ),
#             "matched_tokens": info.get(
#                 "matched_tokens",
#                 [],
#             ),
#             "coordinates": coordinates,
#         }
#     )

#     # --------------------------------------------------------
#     # Hard province contradiction only when we have a real address
#     # --------------------------------------------------------
#     if province_result is False:
#         result["reason"] = "PROVINCE_MISMATCH"
#         accepted = False

#     else:
#         input_has_address = bool(_normalize_text(input_address))

#         address_ok = not input_has_address or (
#             address_available
#             and result_matches_address(
#                 input_address,
#                 actual_address,
#             )
#         )

#         # ====================================================
#         # ADDRESS SEARCH MODE
#         # ====================================================
#         if search_mode == "address":
#             if title_score < ADDRESS_TITLE_MIN_SCORE:
#                 result["reason"] = "ADDRESS_TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "ADDRESS_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "ADDRESS_MISMATCH"
#                     accepted = False

#             elif exact_identity and not input_has_address:
#                 # Exact-title-only acceptance is allowed ONLY when the
#                 # source record itself has no address to validate.
#                 result["reason"] = "ADDRESS_EXACT_TITLE_ACCEPTED"
#                 accepted = True

#             else:
#                 # If the source record has an address, title identity
#                 # alone is NOT enough.  A same-name business can exist
#                 # in another district/province, and Maps may temporarily
#                 # fail to render the address panel.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#         # ====================================================
#         # TITLE FALLBACK MODE
#         # ====================================================
#         elif search_mode == "title":
#             # ====================================================
#             # TITLE-ONLY SOURCE ROW (input address is empty)
#             # ====================================================
#             if not input_has_address:
#                 # For address-less source rows, Maps itself becomes the
#                 # source of the missing address. We still require:
#                 #   - a real /maps/place/ URL
#                 #   - a sufficiently strong title match
#                 #   - a real structured Maps address when available
#                 #
#                 # IMPORTANT:
#                 # We do NOT run a second search from the recovered address.
#                 # The current Place URL is already the candidate we verified.
#                 if title_score >= TITLE_ONLY_NO_ADDRESS_MIN_SCORE:
#                     if address_available:
#                         result["reason"] = "TITLE_ONLY_ADDRESS_RECOVERED"
#                         accepted = True

#                     elif exact_identity:
#                         # Very conservative fallback when Maps temporarily
#                         # fails to render the structured address panel.
#                         result["reason"] = "TITLE_EXACT_MATCH_ACCEPTED"
#                         accepted = True

#                     else:
#                         result["reason"] = "ADDRESS_UNAVAILABLE"
#                         accepted = False

#                 else:
#                     result["reason"] = "TITLE_MISMATCH"
#                     accepted = False

#             # ====================================================
#             # NORMAL TITLE FALLBACK (input address exists)
#             # ====================================================
#             elif title_score < TITLE_SEARCH_MIN_SCORE:
#                 result["reason"] = "TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "TITLE_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "TITLE_LOCATION_MISMATCH"
#                     accepted = False

#             else:
#                 # Input address exists but Maps did not expose a usable
#                 # candidate address. Do not silently accept by title only.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#         # ====================================================
#         # EXISTING/CURRENT PLACE MODE
#         # ====================================================
#         else:
#             if title_score < EXISTING_PLACE_MIN_TITLE_SCORE:
#                 result["reason"] = "EXISTING_TITLE_MISMATCH"
#                 accepted = False

#             elif not address_available:
#                 # Existing-link reuse remains conservative because an old
#                 # wrong link can otherwise silently survive forever.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#             elif not address_ok:
#                 result["reason"] = "EXISTING_LOCATION_MISMATCH"
#                 accepted = False

#             else:
#                 result["reason"] = "EXISTING_PLACE_ACCEPTED"
#                 accepted = True

#     result["success"] = bool(accepted)

#     if logger:
#         logger.info(
#             "VERIFY %s | "
#             "input_title=%r | actual_title=%r | "
#             "input_address=%r | actual_address=%r | "
#             "title_score=%.3f | address_score=%.3f | "
#             "location_score=%.3f | exact_identity=%s | "
#             "input_has_address=%s | success=%s | reason=%s | url=%r",
#             search_mode.upper(),
#             input_title,
#             actual_title,
#             input_address,
#             actual_address,
#             title_score,
#             address_score,
#             location_score,
#             exact_identity,
#             bool(_normalize_text(input_address)),
#             result["success"],
#             result["reason"],
#             actual_url,
#         )

#     return result


# # ============================================================
# # RECOVER COORDINATES
# # ============================================================


# def recover_candidate_coordinates(
#     page,
#     context,
#     candidate,
#     logger=None,
# ):
#     """
#     Only called after success when URL did not contain
#     coordinates.
#     """

#     for _ in range(SELECTED_PLACE_RETRIES):
#         try:
#             page, success, _ = click_candidate(
#                 page,
#                 candidate,
#                 context=context,
#                 logger=logger,
#             )

#             if not success:
#                 continue

#             try:
#                 page.wait_for_timeout(100)
#             except Exception:
#                 pass

#             coordinates = extract_coordinates_from_page(page)

#             if coordinates:
#                 return coordinates

#         except Exception:
#             pass

#         time.sleep(SELECTED_PLACE_RETRY_DELAY)

#     return None


# # ============================================================
# # SEARCH URL
# # ============================================================


# def _safe_build_search_url(
#     query,
# ):
#     query = safe_text(query)

#     if not query:
#         return ""

#     try:
#         url = build_google_maps_search_url(query)

#         if url:
#             return url

#     except Exception:
#         pass

#     return "https://www.google.com/maps/search/?api=1&query=" + quote(
#         query,
#         safe="",
#     )


# # ============================================================
# # SEARCH VARIANTS
# # ============================================================


# def build_search_variants(title, address):
#     """ADDRESS PHASE ONLY. Title-only has its own fallback phase."""
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()
#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if title and address:
#         add(f"{address}, {title}")
#         add(f"{title}, {address}")
#     if address:
#         add(address)
#     return variants[:MAX_SEARCH_VARIANTS]


# def build_title_search_variants(title, address):
#     """
#     TITLE FALLBACK.

#     Address-less row:
#         1. original title
#         2. relaxed title without leading "The"
#         3. quoted exact title
#     """
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()
#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if not title:
#         return variants

#     add(title)

#     if not _normalize_text(address):
#         relaxed = re.sub(
#             r"^the\s+",
#             "",
#             title,
#             flags=re.IGNORECASE,
#         ).strip()

#         if relaxed and relaxed.casefold() != title.casefold():
#             add(relaxed)

#         add(f'"{title}"')
#         return variants[:MAX_TITLE_SEARCH_VARIANTS]

#     parts = _extract_location_parts(address)

#     if len(parts) >= 2:
#         add(f"{title}, {', '.join(parts[-2:])}")
#     elif parts:
#         add(f"{title}, {parts[-1]}")

#     if address:
#         add(f"{title}, {address}")

#     return variants[:MAX_TITLE_SEARCH_VARIANTS]


# # ============================================================
# # LOG
# # ============================================================


# def _log_candidate_summary(
#     logger,
#     index,
#     total,
#     verified,
# ):
#     if not logger:
#         return

#     try:
#         logger.debug(
#             "Candidate %s/%s | "
#             "title=%r | "
#             "address=%r | "
#             "title_score=%.3f | "
#             "address_score=%.3f | "
#             "location_score=%.3f | "
#             "success=%s | "
#             "reason=%s",
#             index,
#             total,
#             verified.get(
#                 "title",
#                 "",
#             ),
#             verified.get(
#                 "address",
#                 "",
#             ),
#             verified.get(
#                 "title_score",
#                 0.0,
#             ),
#             verified.get(
#                 "address_score",
#                 0.0,
#             ),
#             verified.get(
#                 "location_score",
#                 0.0,
#             ),
#             verified.get(
#                 "success",
#                 False,
#             ),
#             verified.get(
#                 "reason",
#                 "",
#             ),
#         )

#     except Exception:
#         pass


# def _candidate_prefilter_title_score(
#     candidate,
#     input_title,
# ):
#     """
#     Compute a cheap title score from search-result text/URL
#     before opening the candidate Place.
#     """
#     if not candidate:
#         return 0.0

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     url_title = extract_place_title_from_url(
#         candidate.get("url") or candidate.get("google_maps_url") or ""
#     )

#     texts = " ".join(
#         candidate.get(
#             "texts",
#             [],
#         )
#         or []
#     )

#     return max(
#         title_match_score(
#             input_title,
#             candidate_title,
#             texts,
#         ),
#         title_match_score(
#             input_title,
#             url_title,
#             texts,
#         ),
#     )


# # ============================================================
# # PROCESS SEARCH PAGE
# # ============================================================


# def _process_search_page(
#     page,
#     context,
#     title,
#     address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Process one Google Maps search page.

#     Critical rules:
#         - Direct /maps/place/ redirect -> VERIFY then RETURN.
#         - Do not scan result cards after we are already on a Place page.
#         - Search DOM may hydrate late, so retry candidate extraction a
#           few short times before declaring NO_CANDIDATES.
#         - Candidate card/review text is used only for ranking, never as
#           the authoritative address.
#     """
#     if page is None:
#         return None

#     close_google_popups(page)

#     wait_for_search_results(
#         page,
#         PAGE_TIMEOUT,
#     )

#     # ========================================================
#     # 1. DIRECT PLACE REDIRECT
#     # ========================================================
#     current_url = None

#     for direct_attempt in range(5):
#         current_url = get_current_google_maps_place_url_robust(page)

#         if current_url:
#             if logger:
#                 logger.info(
#                     "DIRECT PLACE DETECTED | attempt=%s/5 | mode=%s | title=%r | url=%r",
#                     direct_attempt + 1,
#                     search_mode,
#                     title,
#                     current_url,
#                 )
#             break

#         if direct_attempt < 4:
#             try:
#                 page.wait_for_timeout(250)
#             except Exception:
#                 time.sleep(0.25)

#     if current_url:
#         verified = verify_candidate_address(
#             page,
#             context,
#             {
#                 "url": current_url,
#                 "google_maps_url": current_url,
#                 "title": extract_place_title_from_url(current_url),
#                 "texts": [],
#             },
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         return verified

#     # ========================================================
#     # 2. SEARCH RESULTS PAGE - HYDRATION RETRIES
#     # ========================================================
#     candidates = []

#     input_has_address = bool(_normalize_text(address))

#     if search_mode == "title" and not input_has_address:
#         hydration_retries = TITLE_ONLY_HYDRATION_RETRIES
#         hydration_delay_ms = TITLE_ONLY_HYDRATION_DELAY_MS
#     else:
#         hydration_retries = SEARCH_RESULT_HYDRATION_RETRIES
#         hydration_delay_ms = SEARCH_RESULT_HYDRATION_DELAY_MS

#     for hydration_attempt in range(hydration_retries + 1):
#         candidates = extract_result_candidates(page)

#         if candidates:
#             break

#         current_url = get_current_google_maps_place_url_robust(page)

#         if current_url:
#             if logger:
#                 logger.info(
#                     "DIRECT PLACE DETECTED DURING HYDRATION | attempt=%s/%s | mode=%s | title=%r | url=%r",
#                     hydration_attempt + 1,
#                     hydration_retries + 1,
#                     search_mode,
#                     title,
#                     current_url,
#                 )

#             return verify_candidate_address(
#                 page,
#                 context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": extract_place_title_from_url(current_url),
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode=search_mode,
#                 logger=logger,
#             )

#         if hydration_attempt >= hydration_retries:
#             break

#         try:
#             page.wait_for_timeout(hydration_delay_ms)
#         except Exception:
#             time.sleep(hydration_delay_ms / 1000.0)

#     if not candidates:
#         current_url = get_current_google_maps_place_url_robust(page)

#         if current_url:
#             if logger:
#                 logger.info(
#                     "DIRECT PLACE RECOVERED BEFORE NO_CANDIDATES | mode=%s | title=%r | url=%r",
#                     search_mode,
#                     title,
#                     current_url,
#                 )

#             return verify_candidate_address(
#                 page,
#                 context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": extract_place_title_from_url(current_url),
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode=search_mode,
#                 logger=logger,
#             )

#         if logger:
#             logger.info(
#                 "NO_CANDIDATES | mode=%s | title=%r | address=%r | page_url=%r",
#                 search_mode,
#                 title,
#                 address,
#                 getattr(page, "url", ""),
#             )

#         return None

#     # ========================================================
#     # 3. RANK + CHEAP PREFILTER
#     # ========================================================
#     for candidate in candidates:
#         prefilter_title_score = _candidate_prefilter_title_score(
#             candidate,
#             title,
#         )

#         candidate["_prefilter_title_score"] = prefilter_title_score

#         if search_mode == "title":
#             candidate["_rank_score"] = prefilter_title_score
#         else:
#             candidate["_rank_score"] = _candidate_text_score(
#                 candidate,
#                 title,
#                 address,
#             )

#     candidates.sort(
#         key=lambda item: item.get(
#             "_rank_score",
#             0.0,
#         ),
#         reverse=True,
#     )

#     limit = (
#         MAX_TITLE_CANDIDATES_TO_VERIFY
#         if search_mode == "title"
#         else MAX_CANDIDATES_TO_VERIFY
#     )

#     min_pre_title = (
#         TITLE_CANDIDATE_PREFILTER_TITLE_MIN
#         if search_mode == "title"
#         else ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN
#     )

#     # ========================================================
#     # 4. VERIFY PLAUSIBLE CANDIDATES
#     # ========================================================
#     best_failed = None
#     opened = 0
#     had_candidate_after_prefilter = False

#     for candidate in candidates:
#         pre_title = candidate.get(
#             "_prefilter_title_score",
#             0.0,
#         )

#         if pre_title < min_pre_title:
#             if logger:
#                 logger.debug(
#                     "SKIP CANDIDATE PREFILTER | "
#                     "mode=%s | title_score=%.3f | "
#                     "candidate_title=%r | url=%r",
#                     search_mode,
#                     pre_title,
#                     candidate.get("title", ""),
#                     candidate.get("url"),
#                 )
#             continue

#         had_candidate_after_prefilter = True
#         opened += 1

#         verified = verify_candidate_address(
#             page,
#             context,
#             candidate,
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         _log_candidate_summary(
#             logger,
#             opened,
#             limit,
#             verified,
#         )

#         if verified.get("page") is not None:
#             page = verified["page"]

#         if verified.get("success"):
#             return verified

#         if search_mode == "title":
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.80
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.20
#             )
#         else:
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.55
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.45
#             )

#         previous_score = (
#             best_failed.get(
#                 "_page_failure_score",
#                 -1.0,
#             )
#             if best_failed
#             else -1.0
#         )

#         if failure_score > previous_score:
#             verified["_page_failure_score"] = failure_score
#             best_failed = verified

#         if opened >= limit:
#             break

#     # ========================================================
#     # 5. FINAL RESULT FOR THIS QUERY
#     # ========================================================
#     if best_failed is None and not had_candidate_after_prefilter:
#         return {
#             "success": False,
#             "url": None,
#             "google_maps_url": None,
#             "title": "",
#             "address": "",
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": (
#                 "TITLE_PREFILTER_REJECTED"
#                 if search_mode == "title"
#                 else "ADDRESS_PREFILTER_REJECTED"
#             ),
#             "page": page,
#             "search_mode": search_mode,
#         }

#     if best_failed is not None:
#         return best_failed

#     return None


# # ============================================================
# # PUBLIC RESULT STANDARDIZATION
# # ============================================================


# def _standardize_search_result(
#     result,
#     success=None,
#     detail_reason=None,
# ):
#     """
#     Normalize the FINAL result returned by GoogleMapsSearchEngine.search().

#     Public contract:
#         success=True
#             status = FOUND
#             reason = VERIFIED

#         success=False
#             status = MISSING
#             reason = NOT_VERIFIED

#     The technical/internal reason is preserved in:
#         detail_reason
#     """
#     result = dict(result or {})

#     if success is None:
#         success = bool(
#             result.get(
#                 "success",
#                 False,
#             )
#         )

#     internal_reason = (
#         detail_reason
#         or result.get("detail_reason")
#         or result.get("reason")
#         or ("VERIFIED_CANDIDATE" if success else "NOT_FOUND")
#     )

#     result["success"] = bool(success)
#     result["status"] = "FOUND" if success else "MISSING"
#     result["reason"] = "VERIFIED" if success else "NOT_VERIFIED"
#     result["detail_reason"] = internal_reason

#     if success:
#         result["google_maps_url"] = result.get("google_maps_url") or result.get("url")
#         result["url"] = result.get("url") or result.get("google_maps_url")
#     else:
#         # A failed public result must never expose a candidate as FOUND.
#         result["google_maps_url"] = None
#         result["url"] = None

#     return result


# def _failure_priority(reason):
#     """
#     Higher value = more useful final diagnostic reason.

#     Prevents a later weak failure such as TITLE_PREFILTER_REJECTED
#     from overwriting a stronger earlier failure such as
#     ADDRESS_UNAVAILABLE or TITLE_LOCATION_MISMATCH.
#     """
#     priority = {
#         "PROVINCE_MISMATCH": 100,
#         "TITLE_LOCATION_MISMATCH": 95,
#         "ADDRESS_MISMATCH": 90,
#         "EXISTING_LOCATION_MISMATCH": 90,
#         "ADDRESS_UNAVAILABLE": 85,
#         "TITLE_MISMATCH": 80,
#         "ADDRESS_TITLE_MISMATCH": 75,
#         "EXISTING_TITLE_MISMATCH": 75,
#         "TITLE_NAVIGATION_FAILED": 60,
#         "NAVIGATION_FAILED": 60,
#         "TITLE_NO_CANDIDATES": 40,
#         "ADDRESS_NO_CANDIDATES": 40,
#         "TITLE_PREFILTER_REJECTED": 30,
#         "ADDRESS_PREFILTER_REJECTED": 30,
#         "PAGE_NONE": 20,
#         "TITLE_EMPTY": 20,
#         "NOT_FOUND": 10,
#     }

#     return priority.get(
#         safe_text(reason),
#         50,
#     )


# # ============================================================
# # SEARCH ENGINE
# # ============================================================


# class GoogleMapsSearchEngine:
#     def __init__(
#         self,
#         page,
#         context=None,
#         logger=None,
#     ):
#         self.page = page
#         self.context = context
#         self.logger = logger

#     # ========================================================
#     # RECOVER PAGE
#     # ========================================================

#     def _recover_page(self):
#         if self.context is not None:
#             try:
#                 for page in reversed(self.context.pages):
#                     if not page.is_closed():
#                         self.page = page
#                         return page

#             except Exception:
#                 pass

#         return self.page

#     # ========================================================
#     # UPDATE PAGE
#     # ========================================================

#     def update_page(
#         self,
#         page,
#     ):
#         if page is None:
#             return

#         self.page = page

#         try:
#             if self.context is None:
#                 self.context = page.context
#         except Exception:
#             pass

#     # ========================================================
#     # LOG
#     # ========================================================

#     def _log_info(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.info(message)
#             except Exception:
#                 pass

#     def _log_warning(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.warning(message)
#             except Exception:
#                 pass

#     def _log_debug(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.debug(message)
#             except Exception:
#                 pass

#     # ========================================================
#     # CURRENT PLACE PAGE
#     # ========================================================

#     def _try_current_place_page(self, title, address):
#         """
#         Reuse current Place only if its URL title is already plausibly
#         related to the requested title. This avoids spending DOM work on
#         the previous record's Place page.
#         """
#         page = self.page

#         if page is None:
#             return None

#         try:
#             current_url = get_current_google_maps_url(page)

#             if not current_url:
#                 return None

#             current_title = extract_place_title_from_url(current_url)

#             if (
#                 title_match_score(
#                     title,
#                     current_title,
#                 )
#                 < 0.88
#             ):
#                 return None

#             return verify_candidate_address(
#                 page,
#                 self.context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": current_title,
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode="existing",
#                 logger=self.logger,
#             )

#         except Exception as error:
#             self._log_debug("Current place page check failed: " + str(error))

#         return None

#     # ========================================================
#     # SEARCH
#     # ========================================================

#     def search(self, title, address, timeout=None):
#         """
#         FINAL PUBLIC SEARCH CONTRACT

#         SUCCESS:
#             {
#                 "success": True,
#                 "status": "FOUND",
#                 "reason": "VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": "...",
#                 ...
#             }

#         FAILURE:
#             {
#                 "success": False,
#                 "status": "MISSING",
#                 "reason": "NOT_VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": None,
#                 ...
#             }

#         Internal verification functions still keep their detailed reasons.
#         """
#         timeout = timeout or PAGE_TIMEOUT
#         title = safe_text(title)
#         address = safe_text(address)

#         base = {
#             "success": False,
#             "status": "MISSING",
#             "url": None,
#             "google_maps_url": None,
#             "title": title,
#             "address": address,
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": "",
#             "detail_reason": "",
#             "attempts": 0,
#             "page": self.page,
#             "search_mode": "",
#         }

#         # ====================================================
#         # INPUT GUARDS
#         # ====================================================
#         if not title:
#             base["detail_reason"] = "TITLE_EMPTY"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="TITLE_EMPTY",
#             )

#         self._recover_page()

#         if self.page is None:
#             base["detail_reason"] = "PAGE_NONE"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="PAGE_NONE",
#             )

#         if self.context is None:
#             try:
#                 self.context = self.page.context
#             except Exception:
#                 pass

#         # ====================================================
#         # CURRENT PLACE REUSE
#         # ====================================================
#         current_result = self._try_current_place_page(
#             title,
#             address,
#         )

#         if current_result and current_result.get("success"):
#             current_result["search_mode"] = "existing"

#             return _standardize_search_result(
#                 current_result,
#                 success=True,
#                 detail_reason=current_result.get(
#                     "reason",
#                     "EXISTING_PLACE_ACCEPTED",
#                 ),
#             )

#         best_failed = None
#         best_failed_priority = -1
#         best_failed_score = -1.0

#         def remember_failure(result, fallback_reason):
#             nonlocal best_failed
#             nonlocal best_failed_priority
#             nonlocal best_failed_score

#             if result is None:
#                 detail = fallback_reason
#                 priority = _failure_priority(detail)
#                 score = 0.0

#                 candidate = {
#                     "success": False,
#                     "page": self.page,
#                     "title_score": 0.0,
#                     "address_score": 0.0,
#                     "location_score": 0.0,
#                     "coordinates": None,
#                     "matched_tokens": [],
#                     "reason": detail,
#                 }

#             else:
#                 detail = result.get("reason") or fallback_reason

#                 priority = _failure_priority(detail)

#                 score = (
#                     result.get(
#                         "title_score",
#                         0.0,
#                     )
#                     * 0.65
#                     + result.get(
#                         "address_score",
#                         0.0,
#                     )
#                     * 0.25
#                     + result.get(
#                         "location_score",
#                         0.0,
#                     )
#                     * 0.10
#                 )

#                 candidate = result

#             # Technical reason quality wins first.
#             # Score breaks ties.
#             if priority > best_failed_priority or (
#                 priority == best_failed_priority and score > best_failed_score
#             ):
#                 best_failed = candidate
#                 best_failed_priority = priority
#                 best_failed_score = score

#         # ====================================================
#         # PHASE 1: ADDRESS
#         # ====================================================
#         address_variants = build_search_variants(
#             title,
#             address,
#         )

#         for idx, query in enumerate(
#             address_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps ADDRESS search {idx}/{len(address_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps ADDRESS navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="address",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "ADDRESS_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "address"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "ADDRESS_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "ADDRESS_NO_CANDIDATES",
#             )

#         # ====================================================
#         # PHASE 2: TITLE FALLBACK
#         # ====================================================
#         title_variants = build_title_search_variants(
#             title,
#             address,
#         )

#         self._log_info(f"ADDRESS PHASE FAILED -> TITLE FALLBACK | {title} | {address}")

#         for idx, query in enumerate(
#             title_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps TITLE fallback {idx}/{len(title_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps TITLE navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="title",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "TITLE_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "title"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "TITLE_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "TITLE_NO_CANDIDATES",
#             )

#         # ====================================================
#         # FINAL FAILURE
#         # ====================================================
#         base["page"] = self.page
#         base["search_mode"] = "title_fallback_exhausted"

#         detail_reason = "NOT_FOUND"

#         if best_failed:
#             detail_reason = best_failed.get("reason") or "NOT_FOUND"

#             base["title_score"] = best_failed.get(
#                 "title_score",
#                 0.0,
#             )

#             base["address_score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["location_score"] = best_failed.get(
#                 "location_score",
#                 0.0,
#             )

#             base["score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["coordinates"] = best_failed.get("coordinates")

#             base["matched_tokens"] = best_failed.get(
#                 "matched_tokens",
#                 [],
#             )

#         base["detail_reason"] = detail_reason

#         self._log_warning(
#             "Google Maps NOT_VERIFIED after ADDRESS + TITLE fallback | "
#             f"title={title!r} | "
#             f"address={address!r} | "
#             f"detail_reason={detail_reason}"
#         )

#         return _standardize_search_result(
#             base,
#             success=False,
#             detail_reason=detail_reason,
#         )


# # ============================================================
# # PUBLIC FUNCTION
# # ============================================================


# def search_google_maps(
#     page,
#     title,
#     address,
#     context=None,
#     logger=None,
#     timeout=None,
# ):
#     return GoogleMapsSearchEngine(
#         page,
#         context=context,
#         logger=logger,
#     ).search(
#         title,
#         address,
#         timeout=timeout,
#     )
# import re
# import time
# import unicodedata
# from difflib import SequenceMatcher
# from urllib.parse import quote, unquote, urlparse

# from config import (
#     SEARCH_POLL_INTERVAL,
#     SEARCH_POLL_COUNT,
#     FINAL_SEARCH_CHECK_DELAY,
#     PAGE_TIMEOUT,
# )

# from .recovery import safe_goto
# from .utils import (
#     safe_text,
#     clean_google_maps_url,
#     build_google_maps_search_url,
# )


# # ============================================================
# # PERFORMANCE CONFIG
# # ============================================================

# # IMPORTANT:
# # Keep these small for 1000+ records.

# SELECTED_PLACE_RETRIES = 1

# SELECTED_PLACE_RETRY_DELAY = min(
#     max(float(SEARCH_POLL_INTERVAL or 0.15), 0.15),
#     0.4,
# )

# # Search
# MAX_SEARCH_VARIANTS = 3
# MAX_TITLE_SEARCH_VARIANTS = 4
# MAX_TITLE_CANDIDATES_TO_VERIFY = 5

# # Candidate verification
# MAX_CANDIDATES_TO_VERIFY = 3

# # Result extraction
# RESULT_CARD_MAX = 25
# CARD_LINK_MAX = 8

# # Direct place links
# PLACE_URL_SCAN_MAX_LINKS = 160
# PLACE_URL_SCAN_MAX_DATA_ELEMENTS = 60

# # Coordinates
# COORDINATE_SCAN_MAX_LINKS = 10
# COORDINATE_SCAN_MAX_DATA_ELEMENTS = 10

# # Metadata
# MAX_TITLE_ELEMENTS = 3
# MAX_ADDRESS_ELEMENTS = 8

# # Verification thresholds
# ADDRESS_TITLE_MIN_SCORE = 0.88
# TITLE_SEARCH_MIN_SCORE = 0.92
# # When the source row has NO address, allow a slightly lower title score
# # if Google Maps exposes a real structured address on the candidate Place.
# TITLE_ONLY_NO_ADDRESS_MIN_SCORE = 0.85

# # Controlled fallback when Maps Place is strong but address DOM is unavailable.
# STRONG_TITLE_LOCATION_HINT_MIN_SCORE = 0.93
# EXISTING_PLACE_MIN_TITLE_SCORE = 0.95

# # Retry the Maps address panel only for promising title matches.
# STRONG_TITLE_FOR_ADDRESS_RETRY = 0.90
# ADDRESS_RENDER_RETRIES = 2
# ADDRESS_RENDER_RETRY_DELAY_MS = 350

# # Cheap pre-filter before opening candidate URLs.
# # Prevents wasting seconds on obviously wrong businesses.
# ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN = 0.78
# TITLE_CANDIDATE_PREFILTER_TITLE_MIN = 0.82

# # Address DOM extraction
# MAX_ADDRESS_SELECTOR_ELEMENTS = 12

# # Search result hydration.
# # Google Maps often attaches [role=main] before place links appear.
# SEARCH_RESULT_HYDRATION_RETRIES = 3
# SEARCH_RESULT_HYDRATION_DELAY_MS = 300


# # ============================================================
# # LEGACY CONFIG
# # ============================================================

# ADDRESS_MATCH_MIN_SCORE = 0.55
# MIN_VALID_PLACE_ADDRESS_SCORE = 0.55
# MIN_VALID_PLACE_LOCATION_SCORE = 0.50
# MIN_LOCATION_MATCHES = 1

# REQUIRE_LOCATION_FOR_PLACE = False


# # ============================================================
# # GENERIC ADDRESS TOKENS
# # ============================================================

# GENERIC_ADDRESS_TOKENS = {
#     "vietnam",
#     "viet nam",
#     "vn",
#     "street",
#     "road",
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
#     "p",
#     "q",
# }


# # ============================================================
# # ADMINISTRATIVE ALIASES
# # ============================================================

# ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS = (
#     frozenset(
#         {
#             "binh dinh",
#             "gia lai",
#         }
#     ),
#     frozenset(
#         {
#             "ha nam",
#             "nam dinh",
#             "ninh binh",
#         }
#     ),
# )


# ADMIN_PREFIX_RE = re.compile(
#     r"^(?:phuong|xa|thi\s+tran|quan|huyen|thi\s+xa|"
#     r"thanh\s+pho|tp|tinh)\s+",
#     re.IGNORECASE,
# )


# # ============================================================
# # COORDINATE PATTERNS
# # ============================================================

# COORDINATE_PATTERNS = (
#     re.compile(
#         r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
#     re.compile(
#         r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
#         re.IGNORECASE,
#     ),
# )


# # ============================================================
# # TEXT NORMALIZATION
# # ============================================================


# def _normalize_text(value):
#     if value is None:
#         return ""

#     try:
#         value = safe_text(value)
#     except Exception:
#         try:
#             value = str(value)
#         except Exception:
#             return ""

#     return re.sub(
#         r"\s+",
#         " ",
#         str(value or "").replace("\xa0", " ").strip(),
#     )


# def _remove_accents(value):
#     value = _normalize_text(value).lower()

#     if not value:
#         return ""

#     try:
#         value = unicodedata.normalize("NFKD", value)
#         value = "".join(c for c in value if not unicodedata.combining(c))
#     except Exception:
#         pass

#     return value.replace("đ", "d")


# def _normalize_address(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = value.replace("&", " va ")

#     value = re.sub(
#         r"[/|;]+",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"[-_]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"[()\[\]{}]+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r"\s*,\s*",
#         ",",
#         value,
#     )

#     value = re.sub(
#         r"\s+",
#         " ",
#         value,
#     )

#     value = re.sub(
#         r",+",
#         ",",
#         value,
#     )

#     return value.strip(" ,")


# def _normalize_title(value):
#     value = _remove_accents(value)

#     if not value:
#         return ""

#     value = re.sub(
#         r"[^a-z0-9\s]+",
#         " ",
#         value,
#     )

#     return re.sub(
#         r"\s+",
#         " ",
#         value,
#     ).strip()


# def _extract_lines(value):
#     if value is None:
#         return []

#     try:
#         text = safe_text(value)
#     except Exception:
#         try:
#             text = str(value)
#         except Exception:
#             return []

#     if not text:
#         return []

#     text = str(text).replace("\r", "\n").replace("\xa0", " ")

#     result = []

#     for line in text.split("\n"):
#         line = re.sub(
#             r"\s+",
#             " ",
#             line,
#         ).strip()

#         if line:
#             result.append(line)

#     return result


# def _unique_texts(values):
#     result = []
#     seen = set()

#     for value in values:
#         text = _normalize_text(value)

#         if not text:
#             continue

#         key = text.casefold()

#         if key in seen:
#             continue

#         seen.add(key)
#         result.append(text)

#     return result


# # ============================================================
# # ADDRESS HELPERS
# # ============================================================


# def _clean_address_token(token):
#     return _normalize_address(token).strip(" ,.-")


# def _address_tokens(address):
#     normalized = _normalize_address(address)

#     if not normalized:
#         return []

#     result = []

#     for token in normalized.split(","):
#         token = _clean_address_token(token)

#         if not token or len(token) < 2 or token in GENERIC_ADDRESS_TOKENS:
#             continue

#         result.append(token)

#     return result


# def _canonical_address_token(token):
#     """
#     Normalize one administrative/location token.

#     IMPORTANT:
#         Hàm này KHÔNG chuyển tỉnh cũ -> tỉnh mới.

#     Ví dụ:
#         "Binh Dinh Province" -> "binh dinh"
#         "Bình Định"          -> "binh dinh"
#         "Gia Lai Province"   -> "gia lai"
#         "Quy Nhon City"      -> "quy nhon"

#     Việc xác định hai tỉnh trước/sau sáp nhập có tương đương
#     hay không được xử lý riêng bởi
#     _administrative_locations_equivalent().
#     """

#     token = _normalize_address(token)

#     if not token:
#         return ""

#     # --------------------------------------------------------
#     # Remove postal code
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove English administrative suffixes
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+(?:province|city|district|ward|commune|town)$",
#         "",
#         token,
#         flags=re.IGNORECASE,
#     ).strip()

#     # --------------------------------------------------------
#     # Remove Vietnamese administrative prefixes
#     # --------------------------------------------------------

#     previous = None

#     while token != previous:
#         previous = token

#         token = ADMIN_PREFIX_RE.sub(
#             "",
#             token,
#             count=1,
#         ).strip()

#     # --------------------------------------------------------
#     # Postal code may remain after prefix stripping
#     # --------------------------------------------------------

#     token = re.sub(
#         r"\s+\d{4,6}$",
#         "",
#         token,
#     ).strip()

#     return token


# def _administrative_locations_equivalent(
#     location_a,
#     location_b,
# ):
#     """
#     Check whether two province/city names should be considered
#     equivalent for location verification.

#     Supports:
#         1. Exact same administrative location
#         2. Old/new administrative names after merger

#     IMPORTANT:
#         This function does NOT rewrite either location.
#     """

#     a = _canonical_address_token(location_a)
#     b = _canonical_address_token(location_b)

#     if not a or not b:
#         return False

#     # Exact same location
#     if a == b:
#         return True

#     # Old/new administrative equivalence
#     for group in ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS:
#         if a in group and b in group:
#             return True

#     return False


# def _expand_address_token(token):
#     token = _normalize_address(token)

#     if not token:
#         return []

#     variants = [token]
#     current = token

#     while True:
#         stripped = ADMIN_PREFIX_RE.sub(
#             "",
#             current,
#             count=1,
#         ).strip()

#         if not stripped or stripped == current:
#             break

#         variants.append(stripped)
#         current = stripped

#     return _unique_texts(variants)


# def _token_matches_address(
#     token,
#     actual_address,
# ):
#     actual = _normalize_address(actual_address)

#     if not actual:
#         return False

#     canonical = _canonical_address_token(token)

#     if not canonical:
#         return False

#     variants = _unique_texts(_expand_address_token(token) + [canonical])

#     actual_parts = [p.strip() for p in actual.split(",") if p.strip()]

#     for variant in variants:
#         if len(variant) < 4:
#             continue

#         pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"

#         if re.search(
#             pattern,
#             actual,
#         ):
#             return True

#         for part in actual_parts:
#             if part == variant:
#                 return True

#             words = variant.split()

#             if len(words) >= 2:
#                 valid_words = [word for word in words if len(word) >= 3]

#                 if valid_words and all(
#                     re.search(
#                         r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])",
#                         part,
#                     )
#                     for word in valid_words
#                 ):
#                     return True

#             if (
#                 SequenceMatcher(
#                     None,
#                     variant,
#                     part,
#                 ).ratio()
#                 >= 0.88
#             ):
#                 return True

#     return False


# # ============================================================
# # LOCATION
# # ============================================================


# def _extract_location_parts(address):
#     result = []

#     for part in _normalize_address(address).split(","):
#         part = part.strip()

#         if not part:
#             continue

#         if part in {
#             "vietnam",
#             "viet nam",
#             "vn",
#         }:
#             continue

#         result.append(part)

#     return result


# def _canonical_location_parts(address):
#     result = []

#     for part in _extract_location_parts(address):
#         value = _canonical_address_token(part)

#         if value and value not in GENERIC_ADDRESS_TOKENS:
#             result.append(value)

#     return result


# def get_address_province(address):
#     parts = _canonical_location_parts(address)

#     if not parts:
#         return ""

#     return parts[-1]


# def _location_similarity(
#     input_location,
#     actual_location,
# ):
#     a = _canonical_address_token(input_location)

#     b = _canonical_address_token(actual_location)

#     if not a or not b:
#         return 0.0

#     # Exact same location
#     if a == b:
#         return 1.0

#     # Before / after administrative merger
#     if _administrative_locations_equivalent(
#         a,
#         b,
#     ):
#         return 1.0

#     if a in b or b in a:
#         return 0.90

#     return SequenceMatcher(
#         None,
#         a,
#         b,
#     ).ratio()


# def _location_match_info(
#     input_address,
#     actual_address,
# ):
#     inputs = _canonical_location_parts(input_address)

#     actuals = _canonical_location_parts(actual_address)

#     if not inputs or not actuals:
#         return {
#             "matches": [],
#             "count": 0,
#             "score": 0.0,
#             "tail_matches": 0,
#             "tail_score": 0.0,
#         }

#     matches = []
#     used = set()

#     for item in inputs:
#         best_score = 0.0
#         best_actual = ""
#         best_index = -1

#         for idx, candidate in enumerate(actuals):
#             if idx in used:
#                 continue

#             score = _location_similarity(
#                 item,
#                 candidate,
#             )

#             if score > best_score:
#                 best_score = score
#                 best_actual = candidate
#                 best_index = idx

#         if best_score >= 0.72:
#             used.add(best_index)

#             matches.append(
#                 {
#                     "input": item,
#                     "actual": best_actual,
#                     "score": round(
#                         best_score,
#                         4,
#                     ),
#                 }
#             )

#     tail = inputs[-2:] if len(inputs) >= 2 else inputs

#     tail_scores = []

#     for item in tail:
#         best = max(
#             (
#                 _location_similarity(
#                     item,
#                     candidate,
#                 )
#                 for candidate in actuals
#             ),
#             default=0.0,
#         )

#         tail_scores.append(best)

#     return {
#         "matches": matches,
#         "count": len(matches),
#         "score": round(
#             len(matches) / len(inputs),
#             4,
#         ),
#         "tail_matches": sum(1 for score in tail_scores if score >= 0.75),
#         "tail_score": round(
#             sum(tail_scores) / len(tail_scores),
#             4,
#         )
#         if tail_scores
#         else 0.0,
#     }


# # ============================================================
# # ADDRESS SCORE
# # ============================================================


# def address_match_score(
#     input_address,
#     actual_address,
# ):
#     a = _normalize_address(input_address)
#     b = _normalize_address(actual_address)

#     if not a or not b:
#         return {
#             "score": 0.0,
#             "matched_tokens": [],
#             "total_tokens": 0,
#             "location_matches": 0,
#             "location_score": 0.0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "strong_match": False,
#         }

#     if a == b:
#         tokens = _address_tokens(input_address)

#         location_parts = _canonical_location_parts(input_address)

#         return {
#             "score": 1.0,
#             "matched_tokens": tokens,
#             "total_tokens": len(tokens),
#             "location_matches": len(location_parts),
#             "location_score": 1.0,
#             "tail_location_matches": len(location_parts[-2:]),
#             "tail_location_score": 1.0,
#             "strong_match": True,
#         }

#     tokens = _address_tokens(input_address)

#     matched = [
#         token
#         for token in tokens
#         if _token_matches_address(
#             token,
#             actual_address,
#         )
#     ]

#     token_score = len(matched) / len(tokens) if tokens else 0.0

#     location = _location_match_info(
#         input_address,
#         actual_address,
#     )

#     location_score = location["score"]

#     substring_bonus = 0.15 if len(a) >= 10 and a in b else 0.0

#     score = min(
#         1.0,
#         token_score * 0.60 + location_score * 0.40 + substring_bonus,
#     )

#     strong = (
#         score >= 0.80
#         or (
#             score >= ADDRESS_MATCH_MIN_SCORE
#             and location["count"] >= MIN_LOCATION_MATCHES
#         )
#         or (len(tokens) <= 2 and bool(matched) and score >= 0.40)
#     )

#     return {
#         "score": round(
#             score,
#             4,
#         ),
#         "matched_tokens": matched,
#         "total_tokens": len(tokens),
#         "location_matches": location["count"],
#         "location_score": round(
#             location_score,
#             4,
#         ),
#         "tail_location_matches": location["tail_matches"],
#         "tail_location_score": location["tail_score"],
#         "strong_match": bool(strong),
#     }


# def address_is_related(
#     input_address,
#     actual_address,
# ):
#     return bool(
#         address_match_score(
#             input_address,
#             actual_address,
#         ).get("strong_match")
#     )


# # ============================================================
# # TITLE
# # ============================================================


# def title_match_score(
#     input_title,
#     actual_title,
#     actual_text="",
# ):
#     a = _normalize_title(input_title)

#     b = _normalize_title(actual_title)

#     combined = _normalize_title(actual_text)

#     if not a:
#         return 0.0

#     if a == b:
#         return 1.0

#     if b and a in b:
#         return 0.95

#     if b and b in a:
#         return 0.90

#     sequence = (
#         SequenceMatcher(
#             None,
#             a,
#             b,
#         ).ratio()
#         if b
#         else 0.0
#     )

#     wanted = {x for x in a.split() if len(x) >= 3}

#     got = {x for x in combined.split() if len(x) >= 3}

#     overlap = len(wanted & got) / len(wanted) if wanted else 0.0

#     return round(
#         max(
#             sequence,
#             overlap,
#         ),
#         4,
#     )


# def result_matches_title(
#     input_title,
#     result_title,
# ):
#     return (
#         title_match_score(
#             input_title,
#             result_title,
#         )
#         >= 0.75
#     )


# # ============================================================
# # ADDRESS RESULT VALIDATION
# # ============================================================


# def result_matches_address(
#     input_address,
#     result_address,
# ):
#     actual_address = _normalize_text(result_address)

#     if (
#         not actual_address
#         or not re.search(
#             r"[a-z0-9]",
#             actual_address,
#             re.IGNORECASE,
#         )
#         or not _address_tokens(actual_address)
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         actual_address,
#     )

#     if info.get("strong_match"):
#         return True

#     location_matches = info.get(
#         "location_matches",
#         0,
#     )

#     tail_matches = info.get(
#         "tail_location_matches",
#         0,
#     )

#     return location_matches >= 2 and tail_matches >= 1


# def _result_matches_address(
#     input_address,
#     result_address,
# ):
#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# # ============================================================
# # SEARCH IDENTITY
# # ============================================================


# def _matches_search_identity(
#     input_title,
#     input_address,
#     result_title,
#     result_address,
# ):
#     """
#     Strict validation used only when we HAVE metadata.

#     If metadata is unavailable, caller can use URL-level
#     validation instead of forcing expensive extraction.
#     """

#     if not result_matches_title(
#         input_title,
#         result_title,
#     ):
#         return False

#     if not _normalize_text(input_address):
#         return True

#     return result_matches_address(
#         input_address,
#         result_address,
#     )


# def _matches_title_search_result(
#     input_title,
#     result_title,
#     result_address,
# ):
#     return result_matches_title(
#         input_title,
#         result_title,
#     ) and _is_usable_address_text(result_address)


# # ============================================================
# # ADDRESS USABILITY
# # ============================================================


# def _is_usable_address_text(value):
#     """
#     Return True only for text that plausibly represents a postal/street
#     address or useful locality string.

#     IMPORTANT:
#     Reject Google Maps rating/review snippets such as:
#         "4,7(1.060)"
#         "4.7 (1,060)"
#         "4,3(1.699)"
#         "1.060 reviews"
#     """
#     text = _normalize_text(value)

#     if not text:
#         return False

#     # Private-use glyphs / escaped unicode noise.
#     if re.search(
#         r"\\u[0-9a-f]{4}",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     if any(unicodedata.category(char) == "Co" for char in text):
#         return False

#     # --------------------------------------------------------
#     # Ratings / review counts
#     # --------------------------------------------------------
#     compact = re.sub(r"\s+", "", text)

#     rating_patterns = (
#         r"^[0-5](?:[.,]\d)?\(\d[\d.,]*\)$",
#         r"^[0-5](?:[.,]\d)?$",
#         r"^\d[\d.,]*\s*(?:reviews?|đánh\s*giá|ratings?)$",
#         r"^[0-5](?:[.,]\d)?\s*\(\d[\d.,]*\)\s*(?:reviews?|đánh\s*giá|ratings?)?$",
#     )

#     for pattern in rating_patterns:
#         if re.fullmatch(
#             pattern,
#             text,
#             re.IGNORECASE,
#         ) or re.fullmatch(
#             pattern,
#             compact,
#             re.IGNORECASE,
#         ):
#             return False

#     # Pure numeric-ish text is not an address.
#     if re.fullmatch(
#         r"[\d\s.,()/+-]+",
#         text,
#     ):
#         return False

#     # Category-only labels.
#     if re.fullmatch(
#         r"(?:khách sạn|hotel|nhà nghỉ|"
#         r"resort|restaurant|nhà hàng|"
#         r"homestay|lodge|love hotel|"
#         r"serviced apartment)"
#         r"(?:\s+\d+\s+sao)?",
#         text,
#         re.IGNORECASE,
#     ):
#         return False

#     normalized = _normalize_address(text)

#     if not normalized:
#         return False

#     # --------------------------------------------------------
#     # Positive address evidence
#     # --------------------------------------------------------
#     has_digit = bool(
#         re.search(
#             r"\d",
#             normalized,
#         )
#     )

#     has_comma = "," in normalized

#     location_words = (
#         "vietnam",
#         "viet nam",
#         "gia lai",
#         "binh dinh",
#         "quy nhon",
#         "pleiku",
#         "street",
#         "road",
#         "ward",
#         "district",
#         "province",
#         "city",
#         "phuong",
#         "quan",
#         "huyen",
#         "tinh",
#         "xa",
#         "thanh pho",
#         "thi tran",
#         "thi xa",
#         "duong",
#         "đuong",
#         "đường",
#     )

#     has_location_word = any(token in normalized for token in location_words)

#     # Street/address usually has a number + locality separator/word.
#     if has_digit and (has_comma or has_location_word):
#         return True

#     # Locality-only address strings can still be useful.
#     if has_comma and has_location_word:
#         return True

#     # Require at least two comma-separated meaningful parts.
#     parts = [part.strip() for part in normalized.split(",") if part.strip()]

#     if len(parts) >= 2 and any(len(part) >= 3 for part in parts):
#         return True

#     return False


# # ============================================================
# # COORDINATES
# # ============================================================


# def _valid_lat_lng(
#     lat,
#     lng,
# ):
#     try:
#         lat = float(lat)
#         lng = float(lng)
#     except Exception:
#         return False

#     return -90 <= lat <= 90 and -180 <= lng <= 180


# def _extract_coordinates_from_text(
#     text,
# ):
#     if not text:
#         return None

#     try:
#         text = str(text)
#     except Exception:
#         return None

#     for pattern in COORDINATE_PATTERNS:
#         match = pattern.search(text)

#         if not match:
#             continue

#         if _valid_lat_lng(
#             match.group(1),
#             match.group(2),
#         ):
#             return (
#                 float(match.group(1)),
#                 float(match.group(2)),
#             )

#     return None


# def extract_coordinates_from_url(
#     url,
# ):
#     coordinates = _extract_coordinates_from_text(url)

#     if coordinates:
#         return coordinates

#     return None, None


# def get_current_page_coordinates(page):
#     try:
#         coordinates = _extract_coordinates_from_text(page.url or "")

#         if coordinates:
#             return coordinates

#     except Exception:
#         pass

#     return None, None


# def extract_coordinates_from_page(page):
#     """
#     FAST coordinate extraction.

#     Priority:
#         1. current URL
#         2. small number of links
#         3. small number of data attributes

#     This function is intentionally NOT expensive.
#     """

#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     try:
#         value = _extract_coordinates_from_text(page.url or "")

#         if value:
#             return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 2. Links
#     # --------------------------------------------------------

#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             COORDINATE_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             value = _extract_coordinates_from_text(links.nth(i).get_attribute("href"))

#             if value:
#                 return value

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             COORDINATE_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 value = _extract_coordinates_from_text(element.get_attribute(attr))

#                 if value:
#                     return value

#     except Exception:
#         pass

#     return None


# # ============================================================
# # GOOGLE MAPS URL
# # ============================================================


# def _is_google_maps_url(url):
#     if not url:
#         return False

#     try:
#         parsed = urlparse(str(url).strip())
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return False

#     return (
#         parsed.scheme
#         in {
#             "http",
#             "https",
#         }
#         and parsed.hostname
#         in {
#             "google.com",
#             "www.google.com",
#             "maps.google.com",
#         }
#         and parsed.path.lower().startswith("/maps")
#     )


# def is_google_maps_url(url):
#     return _is_google_maps_url(url)


# def _is_google_maps_place_url(url):
#     if not _is_google_maps_url(url):
#         return False

#     try:
#         value = str(url).strip().lower()
#     except Exception:
#         return False

#     if "/maps/search" in value:
#         return False

#     return "/maps/place/" in value or "/maps/place?" in value


# def is_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _is_usable_google_maps_place_url(url):
#     return _is_google_maps_place_url(url)


# def _clean_place_url(url):
#     if not url:
#         return None

#     try:
#         value = str(url).strip()
#     except Exception:
#         return None

#     try:
#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     if _is_google_maps_place_url(value):
#         return value

#     return None


# def get_current_google_maps_url(page):
#     try:
#         return _clean_place_url(page.url or "")
#     except Exception:
#         return None


# def get_current_google_maps_place_url_robust(page):
#     """
#     Robust direct /maps/place/ detection.

#     Sometimes Google has already redirected to a Place page while the
#     normal cleaner still returns None for a short moment.
#     """
#     if page is None:
#         return None

#     try:
#         raw_url = str(page.url or "").strip()
#     except Exception:
#         raw_url = ""

#     if raw_url:
#         if _is_google_maps_place_url(raw_url):
#             return raw_url

#         try:
#             decoded_url = unquote(raw_url)
#             if _is_google_maps_place_url(decoded_url):
#                 return decoded_url
#         except Exception:
#             pass

#     return get_current_google_maps_url(page)


# def extract_place_title_from_url(
#     url,
# ):
#     if not url:
#         return ""

#     try:
#         path = urlparse(str(url)).path
#     except (
#         TypeError,
#         ValueError,
#     ):
#         return ""

#     match = re.search(
#         r"/maps/place/([^/]+)",
#         path,
#         re.IGNORECASE,
#     )

#     if not match:
#         return ""

#     return _normalize_text(
#         unquote(match.group(1)).replace(
#             "+",
#             " ",
#         )
#     )


# def extract_google_maps_url_from_href(
#     href,
# ):
#     if not href:
#         return None

#     try:
#         value = (
#             str(href)
#             .strip()
#             .replace(
#                 "&amp;",
#                 "&",
#             )
#         )

#         value = (
#             value.replace(
#                 "\\/",
#                 "/",
#             )
#             .replace(
#                 "\\u003d",
#                 "=",
#             )
#             .replace(
#                 "\\u0026",
#                 "&",
#             )
#         )

#         if value.startswith("/maps/"):
#             value = "https://www.google.com" + value

#         decoded = unquote(value)

#         if _is_google_maps_place_url(decoded):
#             value = decoded

#         cleaned = clean_google_maps_url(value)

#         if cleaned and _is_google_maps_place_url(cleaned):
#             return cleaned

#     except Exception:
#         pass

#     return _clean_place_url(href)


# # ============================================================
# # POPUPS
# # ============================================================


# def close_google_popups(page):
#     if page is None:
#         return

#     selectors = [
#         'button:has-text("Accept all")',
#         'button:has-text("I agree")',
#         'button:has-text("Đồng ý")',
#         'button:has-text("Chấp nhận tất cả")',
#         '[aria-label="Accept all"]',
#         '[aria-label="I agree"]',
#         '[aria-label="Chấp nhận tất cả"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             if locator.count() and locator.first.is_visible(timeout=200):
#                 locator.first.click(timeout=700)

#                 break

#         except Exception:
#             continue


# # ============================================================
# # FAST WAIT
# # ============================================================


# def wait_for_search_results(
#     page,
#     timeout=None,
# ):
#     """
#     Much cheaper than repeatedly calling count()
#     on multiple selectors.
#     """

#     if page is None:
#         return False

#     timeout = min(
#         int(timeout or PAGE_TIMEOUT),
#         5000,
#     )

#     try:
#         if page.is_closed():
#             return False
#     except Exception:
#         return False

#     selector = 'a[href*="/maps/place/"], div.Nv2PK, [role="main"]'

#     try:
#         page.locator(selector).first.wait_for(
#             state="attached",
#             timeout=timeout,
#         )

#         return True

#     except Exception:
#         return False


# # ============================================================
# # PLAYWRIGHT TEXT
# # ============================================================


# def _read_locator_text(locator):
#     if locator is None:
#         return ""

#     try:
#         text = locator.inner_text(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     try:
#         text = locator.text_content(timeout=900)

#         if text:
#             return safe_text(text)

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # SELECTED PLACE TITLE
# # ============================================================


# def extract_selected_place_title(
#     page,
#     expected_title=None,
# ):
#     if page is None:
#         return ""

#     values = []

#     for selector in (
#         '[role="main"] h1',
#         "h1",
#         '[data-item-id="title"]',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_TITLE_ELEMENTS,
#             )

#             for i in range(count):
#                 text = _read_locator_text(locator.nth(i))

#                 if text:
#                     values.append(text)

#         except Exception:
#             continue

#     values = _unique_texts(values)

#     if not values:
#         return ""

#     if expected_title:
#         return max(
#             values,
#             key=lambda x: title_match_score(
#                 expected_title,
#                 x,
#             ),
#         )

#     return values[0]


# # ============================================================
# # SELECTED PLACE ADDRESS
# # ============================================================


# def extract_selected_place_address(
#     page,
#     expected_address="",
# ):
#     """
#     Extract ONLY official/structured Google Maps address UI.

#     IMPORTANT:
#         Do NOT use arbitrary main-panel text here.
#         Reviews/descriptions often contain street names and must never
#         be treated as the Place address.
#     """
#     if page is None:
#         return ""

#     candidates = []

#     selectors = [
#         'button[data-item-id="address"]',
#         '[data-item-id="address"]',
#         '[role="main"] button[data-item-id="address"]',
#         '[role="main"] [data-item-id="address"]',
#         'button[aria-label^="Địa chỉ"]',
#         'button[aria-label^="Address"]',
#         '[role="main"] button[aria-label^="Địa chỉ"]',
#         '[role="main"] button[aria-label^="Address"]',
#         '[aria-label^="Địa chỉ"]',
#         '[aria-label^="Address"]',
#         '[data-tooltip*="Địa chỉ"]',
#         '[data-tooltip*="Address"]',
#         '[title*="Địa chỉ"]',
#         '[title*="Address"]',
#     ]

#     for selector in selectors:
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 MAX_ADDRESS_SELECTOR_ELEMENTS,
#             )

#             for i in range(count):
#                 element = locator.nth(i)

#                 # visible text
#                 text_value = _read_locator_text(element)

#                 if _is_usable_address_text(text_value):
#                     candidates.append(text_value)

#                 # structured attributes
#                 for attr in (
#                     "aria-label",
#                     "data-tooltip",
#                     "title",
#                 ):
#                     try:
#                         value = element.get_attribute(attr)
#                     except Exception:
#                         value = None

#                     if not value:
#                         continue

#                     value = _normalize_text(value)

#                     value = re.sub(
#                         r"^(?:địa\s*chỉ|address)\s*:\s*",
#                         "",
#                         value,
#                         flags=re.IGNORECASE,
#                     )

#                     if _is_usable_address_text(value):
#                         candidates.append(value)

#         except Exception:
#             continue

#     candidates = [
#         value for value in _unique_texts(candidates) if _is_usable_address_text(value)
#     ]

#     if not candidates:
#         return ""

#     if expected_address:

#         def score_address(value):
#             info = address_match_score(
#                 expected_address,
#                 value,
#             )

#             return (
#                 info.get("score", 0.0),
#                 info.get("location_score", 0.0),
#             )

#         return max(
#             candidates,
#             key=score_address,
#         )

#     return candidates[0]


# # ============================================================
# # MAIN TEXT FALLBACK
# # ============================================================


# def _find_best_address_from_main_text(
#     page,
#     expected_address="",
# ):
#     if page is None:
#         return ""

#     try:
#         main = page.locator('[role="main"]').first

#         if not main.count():
#             return ""

#         text = main.inner_text(timeout=1800)

#         lines = _extract_lines(text)

#         if not lines:
#             return ""

#         if expected_address:
#             scored = []

#             for line in lines:
#                 info = address_match_score(
#                     expected_address,
#                     line,
#                 )

#                 if info["score"] >= 0.20 or info["location_score"] >= 0.25:
#                     scored.append(
#                         (
#                             info["score"],
#                             info["location_score"],
#                             line,
#                         )
#                     )

#             if scored:
#                 scored.sort(
#                     key=lambda x: (
#                         x[0],
#                         x[1],
#                     ),
#                     reverse=True,
#                 )

#                 return scored[0][2]

#         for line in lines:
#             normalized = _normalize_address(line)

#             if not normalized:
#                 continue

#             has_number = bool(
#                 re.search(
#                     r"\d",
#                     normalized,
#                 )
#             )

#             has_comma = "," in normalized

#             has_location_word = any(
#                 token in normalized
#                 for token in (
#                     "vietnam",
#                     "viet nam",
#                     "street",
#                     "road",
#                     "ward",
#                     "district",
#                     "province",
#                     "city",
#                     "phuong",
#                     "quan",
#                     "huyen",
#                     "tinh",
#                     "xa",
#                     "thanh pho",
#                     "thi tran",
#                     "thi xa",
#                 )
#             )

#             if has_number and (has_comma or has_location_word):
#                 return line

#     except Exception:
#         pass

#     return ""


# # ============================================================
# # CANDIDATE ADDRESS
# # ============================================================


# def _find_best_address_from_candidate(
#     candidate,
#     expected_address,
# ):
#     if not candidate:
#         return ""

#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     usable = [text for text in _unique_texts(texts) if _is_usable_address_text(text)]

#     if not usable:
#         return ""

#     return max(
#         usable,
#         key=lambda text: address_match_score(
#             expected_address,
#             text,
#         ).get(
#             "score",
#             0.0,
#         ),
#     )


# # ============================================================
# # PLACE URL EXTRACTION
# # ============================================================


# def extract_place_url_from_page(
#     page,
# ):
#     if page is None:
#         return None

#     # --------------------------------------------------------
#     # 1. Current URL
#     # --------------------------------------------------------

#     current = get_current_google_maps_url(page)

#     if current:
#         return current

#     # --------------------------------------------------------
#     # 2. Direct place links
#     # --------------------------------------------------------

#     try:
#         links = page.locator('a[href*="/maps/place/"]')

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             url = extract_google_maps_url_from_href(links.nth(i).get_attribute("href"))

#             if url:
#                 return url

#     except Exception:
#         pass

#     # --------------------------------------------------------
#     # 3. Data attributes
#     # --------------------------------------------------------

#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             element = elements.nth(i)

#             for attr in (
#                 "data-url",
#                 "data-href",
#             ):
#                 url = extract_google_maps_url_from_href(element.get_attribute(attr))

#                 if url:
#                     return url

#     except Exception:
#         pass

#     return None


# # ============================================================
# # RESULT CARD
# # ============================================================


# def _extract_card_texts(card):
#     values = []

#     try:
#         values.extend(_extract_lines(card.inner_text(timeout=700)))

#     except Exception:
#         pass

#     try:
#         aria = card.get_attribute("aria-label")

#         if aria:
#             values.append(aria)

#     except Exception:
#         pass

#     return _unique_texts(values)


# def _extract_title_from_card_texts(
#     texts,
# ):
#     if not texts:
#         return ""

#     return texts[0]


# def _candidate_text_score(
#     candidate,
#     input_title,
#     input_address,
# ):
#     texts = candidate.get(
#         "texts",
#         [],
#     )

#     combined = " ".join(texts)

#     title_score = title_match_score(
#         input_title,
#         candidate.get(
#             "title",
#             "",
#         ),
#         combined,
#     )

#     address_info = address_match_score(
#         input_address,
#         combined,
#     )

#     return (
#         title_score * 0.45
#         + address_info["score"] * 0.40
#         + address_info["location_score"] * 0.15
#     )


# # ============================================================
# # EXTRACT SEARCH CANDIDATES
# # ============================================================


# def extract_result_candidates(page):
#     """
#     Extract Google Maps Place candidates without depending on one
#     specific Maps DOM version.

#     Sources:
#         1. Result cards
#         2. ALL anchors whose href resolves to /maps/place/
#         3. data-url / data-href
#         4. raw HTML fallback for hydrated/virtualized Maps markup

#     This function NEVER navigates.
#     """
#     if page is None:
#         return []

#     candidates = []
#     seen = set()

#     def add_candidate(
#         url,
#         title="",
#         texts=None,
#     ):
#         url = extract_google_maps_url_from_href(url)

#         if not url:
#             return

#         if url in seen:
#             return

#         seen.add(url)

#         title = _normalize_text(title)

#         if not title:
#             title = extract_place_title_from_url(url)

#         values = _unique_texts(list(texts or []) + ([title] if title else []))

#         candidates.append(
#             {
#                 "url": url,
#                 "google_maps_url": url,
#                 "title": title,
#                 "texts": values,
#             }
#         )

#     # ========================================================
#     # 1. RESULT CARDS
#     # ========================================================
#     for selector in (
#         "div.Nv2PK",
#         "div.bfdHYd",
#         '[role="article"]',
#         '[role="feed"] > div',
#     ):
#         try:
#             locator = page.locator(selector)

#             count = min(
#                 locator.count(),
#                 RESULT_CARD_MAX,
#             )

#             if not count:
#                 continue

#             for i in range(count):
#                 try:
#                     card = locator.nth(i)
#                     texts = _extract_card_texts(card)

#                     links = card.locator('a[href*="/maps/place/"]')

#                     link_count = min(
#                         links.count(),
#                         CARD_LINK_MAX,
#                     )

#                     for j in range(link_count):
#                         href = links.nth(j).get_attribute("href")

#                         add_candidate(
#                             href,
#                             title=_extract_title_from_card_texts(texts),
#                             texts=texts,
#                         )

#                     if link_count == 0:
#                         links = card.locator("a[href]")

#                         link_count = min(
#                             links.count(),
#                             CARD_LINK_MAX,
#                         )

#                         for j in range(link_count):
#                             link = links.nth(j)
#                             href = link.get_attribute("href")

#                             url = extract_google_maps_url_from_href(href)

#                             if not url:
#                                 continue

#                             link_text = _read_locator_text(link)

#                             add_candidate(
#                                 url,
#                                 title=link_text
#                                 or _extract_title_from_card_texts(texts),
#                                 texts=texts + ([link_text] if link_text else []),
#                             )

#                 except Exception:
#                     continue

#         except Exception:
#             continue

#     # ========================================================
#     # 2. GLOBAL ANCHORS
#     # ========================================================
#     try:
#         links = page.locator("a[href]")

#         count = min(
#             links.count(),
#             PLACE_URL_SCAN_MAX_LINKS,
#         )

#         for i in range(count):
#             try:
#                 link = links.nth(i)
#                 href = link.get_attribute("href")

#                 url = extract_google_maps_url_from_href(href)

#                 if not url:
#                     continue

#                 link_text = _read_locator_text(link)

#                 aria = ""
#                 try:
#                     aria = link.get_attribute("aria-label") or ""
#                 except Exception:
#                     pass

#                 title = link_text or aria or extract_place_title_from_url(url)

#                 add_candidate(
#                     url,
#                     title=title,
#                     texts=[link_text, aria],
#                 )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 3. DATA ATTRIBUTES
#     # ========================================================
#     try:
#         elements = page.locator("[data-url], [data-href]")

#         count = min(
#             elements.count(),
#             PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
#         )

#         for i in range(count):
#             try:
#                 element = elements.nth(i)

#                 element_text = _read_locator_text(element)

#                 for attr in (
#                     "data-url",
#                     "data-href",
#                 ):
#                     value = element.get_attribute(attr)

#                     if not value:
#                         continue

#                     add_candidate(
#                         value,
#                         title=element_text,
#                         texts=[element_text],
#                     )

#             except Exception:
#                 continue

#     except Exception:
#         pass

#     # ========================================================
#     # 4. RAW HTML FALLBACK
#     # ========================================================
#     if not candidates:
#         try:
#             html = page.content()

#             if html:
#                 raw_patterns = (
#                     r'https?://www\.google\.com/maps/place/[^"\'<>\s]+',
#                     r'https?://google\.com/maps/place/[^"\'<>\s]+',
#                     r'/maps/place/[^"\'<>\s]+',
#                 )

#                 for pattern in raw_patterns:
#                     for match in re.findall(
#                         pattern,
#                         html,
#                         flags=re.IGNORECASE,
#                     ):
#                         value = (
#                             match.replace("&amp;", "&")
#                             .replace("\\/", "/")
#                             .replace("\\u003d", "=")
#                             .replace("\\u0026", "&")
#                         )

#                         if value.startswith("/maps/"):
#                             value = "https://www.google.com" + value

#                         add_candidate(
#                             value,
#                             title=extract_place_title_from_url(value),
#                             texts=[],
#                         )

#                         if len(candidates) >= RESULT_CARD_MAX:
#                             break

#                     if candidates:
#                         break

#         except Exception:
#             pass

#     return candidates


# # ============================================================
# # CLICK / NAVIGATE
# # ============================================================


# def click_candidate(
#     page,
#     candidate,
#     context=None,
#     logger=None,
# ):
#     if page is None or not candidate:
#         return page, False, 0

#     url = candidate.get("url") or candidate.get("google_maps_url")

#     if not url:
#         return page, False, 0

#     if not _is_google_maps_place_url(url):
#         return page, False, 0

#     if context is None:
#         try:
#             context = page.context
#         except Exception:
#             pass

#     try:
#         if context is not None:
#             return safe_goto(
#                 page,
#                 context,
#                 url,
#                 logger=logger,
#                 timeout=PAGE_TIMEOUT,
#             )

#         page.goto(
#             url,
#             wait_until="domcontentloaded",
#             timeout=PAGE_TIMEOUT,
#         )

#         return page, True, 1

#     except Exception as error:
#         if logger:
#             logger.warning("Candidate navigation error: " + str(error)[:250])

#         return page, False, 1


# # ============================================================
# # FAST PROVINCE GUARD
# # ============================================================


# def _province_from_text(
#     text,
# ):
#     parts = _canonical_location_parts(text)

#     if not parts:
#         return ""

#     return parts[-1]


# def _province_matches(
#     input_address,
#     actual_address,
# ):
#     """
#     Protect against Google Maps jumping to another province.

#     Supports:
#         - Same province
#         - Province before/after administrative merger

#     Returns:
#         True
#             Province matches or is administratively equivalent.

#         False
#             Definitely another province.

#         None
#             Actual Maps address is unavailable, so cannot decide.
#     """

#     expected_province = get_address_province(
#         input_address,
#     )

#     actual_province = get_address_province(
#         actual_address,
#     )

#     # Input does not provide province information.
#     if not expected_province:
#         return True

#     # Google Maps address unavailable.
#     if not actual_province:
#         return None

#     return _administrative_locations_equivalent(
#         expected_province,
#         actual_province,
#     )


# # ============================================================
# # CANDIDATE VERIFICATION HELPERS
# # ============================================================


# def _candidate_location_evidence(
#     candidate,
#     input_address,
# ):
#     if not candidate or not _normalize_text(input_address):
#         return False

#     texts = candidate.get("texts", []) or []

#     if not texts:
#         return False

#     combined = " | ".join(
#         text for text in _unique_texts(texts) if _normalize_text(text)
#     )

#     if not combined:
#         return False

#     expected_province = get_address_province(input_address)
#     actual_province = get_address_province(combined)

#     if (
#         expected_province
#         and actual_province
#         and not _administrative_locations_equivalent(
#             expected_province,
#             actual_province,
#         )
#     ):
#         return False

#     info = address_match_score(
#         input_address,
#         combined,
#     )

#     return bool(
#         info.get("location_matches", 0) >= 1
#         and (
#             info.get("location_score", 0.0) >= 0.25
#             or info.get("tail_location_matches", 0) >= 1
#         )
#     )


# def _extract_candidate_address_with_retry(
#     page,
#     candidate,
#     input_address,
#     title_score,
# ):
#     """
#     SAFE ADDRESS EXTRACTION.

#     SOURCE OF TRUTH:
#         ONLY official Google Maps address DOM.

#     NEVER use:
#         - candidate card text
#         - reviews
#         - descriptions
#         - main panel arbitrary text
#         - combined candidate text

#     This prevents review snippets such as:
#         "... ở 11 An Dương Vương QN ..."
#     from becoming actual_address.
#     """

#     if page is None:
#         return ""

#     # ========================================================
#     # 1. OFFICIAL GOOGLE MAPS ADDRESS ELEMENT ONLY
#     # ========================================================
#     try:
#         actual_address = extract_selected_place_address(
#             page,
#             expected_address=input_address,
#         )
#     except Exception:
#         actual_address = ""

#     if actual_address and _is_usable_address_text(actual_address):
#         return actual_address

#     # ========================================================
#     # 2. ONLY RETRY FOR STRONG TITLE
#     # ========================================================
#     if title_score < STRONG_TITLE_FOR_ADDRESS_RETRY:
#         return ""

#     for _ in range(ADDRESS_RENDER_RETRIES):
#         try:
#             page.wait_for_timeout(ADDRESS_RENDER_RETRY_DELAY_MS)
#         except Exception:
#             time.sleep(ADDRESS_RENDER_RETRY_DELAY_MS / 1000.0)

#         try:
#             actual_address = extract_selected_place_address(
#                 page,
#                 expected_address=input_address,
#             )
#         except Exception:
#             actual_address = ""

#         if actual_address and _is_usable_address_text(actual_address):
#             return actual_address

#     return ""


# def _is_nearly_exact_title_match(
#     input_title,
#     actual_title,
#     actual_url,
# ):
#     """
#     Conservative identity fallback used only when address UI is absent.

#     Requires:
#         - Place URL
#         - title >= 0.99
#         - URL title itself also strongly matches the requested title
#     """
#     if not _is_google_maps_place_url(actual_url):
#         return False

#     direct_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     url_score = title_match_score(
#         input_title,
#         url_title,
#     )

#     return direct_score >= 0.99 and url_score >= 0.95


# # ============================================================
# # CANDIDATE VERIFY
# # ============================================================


# def verify_candidate_address(
#     page,
#     context,
#     candidate,
#     input_title,
#     input_address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Verify a Google Maps Place candidate.

#     Rules:
#         - /maps/place/ is required but never sufficient by itself.
#         - We NEVER infer address/province from reviews/main text.
#         - If official address exists, verify it normally.
#         - If the SOURCE row has no address, a strong title match plus a real
#           structured Maps address may recover the missing address and accept
#           the candidate without running a second address search.
#         - If official address is absent, only a nearly-exact title match
#           may survive without an address, and only in title/address search
#           mode. Existing-link reuse remains stricter.
#     """
#     result = {
#         "success": False,
#         "url": None,
#         "google_maps_url": None,
#         "title": "",
#         "address": "",
#         "score": 0.0,
#         "address_score": 0.0,
#         "title_score": 0.0,
#         "matched_tokens": [],
#         "coordinates": None,
#         "reason": "",
#         "page": page,
#         "attempts": 0,
#         "search_mode": search_mode,
#         "location_score": 0.0,
#         "location_matches": 0,
#         "tail_location_matches": 0,
#         "tail_location_score": 0.0,
#     }

#     if page is None:
#         result["reason"] = "PAGE_NONE"
#         return result

#     if not candidate:
#         result["reason"] = "CANDIDATE_NONE"
#         return result

#     candidate_url = candidate.get("url") or candidate.get("google_maps_url")

#     if not _is_google_maps_place_url(candidate_url):
#         result["reason"] = "INVALID_CANDIDATE_URL"
#         return result

#     page, success, attempts = click_candidate(
#         page,
#         candidate,
#         context=context,
#         logger=logger,
#     )

#     result["page"] = page
#     result["attempts"] = attempts

#     if not success or page is None:
#         result["reason"] = "NAVIGATION_FAILED"
#         return result

#     try:
#         page.wait_for_timeout(150)
#     except Exception:
#         pass

#     close_google_popups(page)

#     actual_url = get_current_google_maps_url(page) or _clean_place_url(candidate_url)

#     if not actual_url:
#         result["reason"] = "NO_VALID_MAPS_PLACE_URL"
#         return result

#     coordinates = _extract_coordinates_from_text(
#         actual_url,
#     )

#     # --------------------------------------------------------
#     # Title
#     # --------------------------------------------------------
#     url_title = extract_place_title_from_url(
#         actual_url,
#     )

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     actual_title = url_title or candidate_title or ""

#     title_score = title_match_score(
#         input_title,
#         actual_title,
#     )

#     if title_score < 0.90:
#         try:
#             dom_title = extract_selected_place_title(
#                 page,
#                 expected_title=input_title,
#             )
#         except Exception:
#             dom_title = ""

#         if dom_title:
#             dom_score = title_match_score(
#                 input_title,
#                 dom_title,
#             )

#             if dom_score > title_score:
#                 actual_title = dom_title
#                 title_score = dom_score

#     # --------------------------------------------------------
#     # Official/structured address only
#     # --------------------------------------------------------
#     actual_address = _extract_candidate_address_with_retry(
#         page,
#         candidate,
#         input_address,
#         title_score,
#     )

#     address_available = bool(_is_usable_address_text(actual_address))

#     if address_available:
#         info = address_match_score(
#             input_address,
#             actual_address,
#         )
#     else:
#         info = {
#             "score": 0.0,
#             "location_score": 0.0,
#             "location_matches": 0,
#             "tail_location_matches": 0,
#             "tail_location_score": 0.0,
#             "matched_tokens": [],
#         }

#     address_score = info.get(
#         "score",
#         0.0,
#     )

#     location_score = info.get(
#         "location_score",
#         0.0,
#     )

#     province_result = (
#         _province_matches(
#             input_address,
#             actual_address,
#         )
#         if address_available
#         else None
#     )

#     exact_identity = _is_nearly_exact_title_match(
#         input_title,
#         actual_title,
#         actual_url,
#     )

#     result.update(
#         {
#             "url": actual_url,
#             "google_maps_url": actual_url,
#             "title": actual_title,
#             "address": actual_address,
#             "title_score": title_score,
#             "address_score": address_score,
#             "score": address_score,
#             "location_score": location_score,
#             "location_matches": info.get(
#                 "location_matches",
#                 0,
#             ),
#             "tail_location_matches": info.get(
#                 "tail_location_matches",
#                 0,
#             ),
#             "tail_location_score": info.get(
#                 "tail_location_score",
#                 0.0,
#             ),
#             "matched_tokens": info.get(
#                 "matched_tokens",
#                 [],
#             ),
#             "coordinates": coordinates,
#         }
#     )

#     # --------------------------------------------------------
#     # Hard province contradiction only when we have a real address
#     # --------------------------------------------------------
#     if province_result is False:
#         result["reason"] = "PROVINCE_MISMATCH"
#         accepted = False

#     else:
#         input_has_address = bool(_normalize_text(input_address))

#         address_ok = not input_has_address or (
#             address_available
#             and result_matches_address(
#                 input_address,
#                 actual_address,
#             )
#         )

#         # ====================================================
#         # ADDRESS SEARCH MODE
#         # ====================================================
#         if search_mode == "address":
#             if title_score < ADDRESS_TITLE_MIN_SCORE:
#                 result["reason"] = "ADDRESS_TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "ADDRESS_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "ADDRESS_MISMATCH"
#                     accepted = False

#             elif exact_identity and not input_has_address:
#                 # Exact-title-only acceptance is allowed ONLY when the
#                 # source record itself has no address to validate.
#                 result["reason"] = "ADDRESS_EXACT_TITLE_ACCEPTED"
#                 accepted = True

#             else:
#                 # Controlled fallback:
#                 # Strong title + explicit expected location hint in the
#                 # Place title/URL/candidate metadata may survive a temporary
#                 # Maps address-DOM failure.
#                 has_location_hint = _has_expected_location_hint(
#                     input_address,
#                     actual_title=actual_title,
#                     actual_url=actual_url,
#                     candidate=candidate,
#                 )

#                 if (
#                     input_has_address
#                     and title_score >= STRONG_TITLE_LOCATION_HINT_MIN_SCORE
#                     and has_location_hint
#                 ):
#                     result["reason"] = (
#                         "ADDRESS_UNAVAILABLE_STRONG_TITLE_LOCATION_ACCEPTED"
#                     )
#                     accepted = True

#                 else:
#                     result["reason"] = "ADDRESS_UNAVAILABLE"
#                     accepted = False

#         # ====================================================
#         # TITLE FALLBACK MODE
#         # ====================================================
#         elif search_mode == "title":
#             # ====================================================
#             # TITLE-ONLY SOURCE ROW (input address is empty)
#             # ====================================================
#             if not input_has_address:
#                 # For address-less source rows, Maps itself becomes the
#                 # source of the missing address. We still require:
#                 #   - a real /maps/place/ URL
#                 #   - a sufficiently strong title match
#                 #   - a real structured Maps address when available
#                 #
#                 # IMPORTANT:
#                 # We do NOT run a second search from the recovered address.
#                 # The current Place URL is already the candidate we verified.
#                 if title_score >= TITLE_ONLY_NO_ADDRESS_MIN_SCORE:
#                     if address_available:
#                         result["reason"] = "TITLE_ONLY_ADDRESS_RECOVERED"
#                         accepted = True

#                     elif exact_identity:
#                         # Very conservative fallback when Maps temporarily
#                         # fails to render the structured address panel.
#                         result["reason"] = "TITLE_EXACT_MATCH_ACCEPTED"
#                         accepted = True

#                     else:
#                         result["reason"] = "ADDRESS_UNAVAILABLE"
#                         accepted = False

#                 else:
#                     result["reason"] = "TITLE_MISMATCH"
#                     accepted = False

#             # ====================================================
#             # NORMAL TITLE FALLBACK (input address exists)
#             # ====================================================
#             elif title_score < TITLE_SEARCH_MIN_SCORE:
#                 result["reason"] = "TITLE_MISMATCH"
#                 accepted = False

#             elif address_available:
#                 if address_ok:
#                     result["reason"] = "TITLE_SEARCH_ACCEPTED"
#                     accepted = True
#                 else:
#                     result["reason"] = "TITLE_LOCATION_MISMATCH"
#                     accepted = False

#             else:
#                 # Same controlled fallback for TITLE phase.
#                 has_location_hint = _has_expected_location_hint(
#                     input_address,
#                     actual_title=actual_title,
#                     actual_url=actual_url,
#                     candidate=candidate,
#                 )

#                 if (
#                     title_score >= STRONG_TITLE_LOCATION_HINT_MIN_SCORE
#                     and has_location_hint
#                 ):
#                     result["reason"] = "TITLE_STRONG_LOCATION_HINT_ACCEPTED"
#                     accepted = True

#                 else:
#                     result["reason"] = "ADDRESS_UNAVAILABLE"
#                     accepted = False

#         # ====================================================
#         # EXISTING/CURRENT PLACE MODE
#         # ====================================================
#         else:
#             if title_score < EXISTING_PLACE_MIN_TITLE_SCORE:
#                 result["reason"] = "EXISTING_TITLE_MISMATCH"
#                 accepted = False

#             elif not address_available:
#                 # Existing-link reuse remains conservative because an old
#                 # wrong link can otherwise silently survive forever.
#                 result["reason"] = "ADDRESS_UNAVAILABLE"
#                 accepted = False

#             elif not address_ok:
#                 result["reason"] = "EXISTING_LOCATION_MISMATCH"
#                 accepted = False

#             else:
#                 result["reason"] = "EXISTING_PLACE_ACCEPTED"
#                 accepted = True

#     result["success"] = bool(accepted)

#     if logger:
#         logger.info(
#             "VERIFY %s | "
#             "input_title=%r | actual_title=%r | "
#             "input_address=%r | actual_address=%r | "
#             "title_score=%.3f | address_score=%.3f | "
#             "location_score=%.3f | exact_identity=%s | "
#             "input_has_address=%s | success=%s | reason=%s | url=%r",
#             search_mode.upper(),
#             input_title,
#             actual_title,
#             input_address,
#             actual_address,
#             title_score,
#             address_score,
#             location_score,
#             exact_identity,
#             bool(_normalize_text(input_address)),
#             result["success"],
#             result["reason"],
#             actual_url,
#         )

#     return result


# # ============================================================
# # RECOVER COORDINATES
# # ============================================================


# def recover_candidate_coordinates(
#     page,
#     context,
#     candidate,
#     logger=None,
# ):
#     """
#     Only called after success when URL did not contain
#     coordinates.
#     """

#     for _ in range(SELECTED_PLACE_RETRIES):
#         try:
#             page, success, _ = click_candidate(
#                 page,
#                 candidate,
#                 context=context,
#                 logger=logger,
#             )

#             if not success:
#                 continue

#             try:
#                 page.wait_for_timeout(100)
#             except Exception:
#                 pass

#             coordinates = extract_coordinates_from_page(page)

#             if coordinates:
#                 return coordinates

#         except Exception:
#             pass

#         time.sleep(SELECTED_PLACE_RETRY_DELAY)

#     return None


# # ============================================================
# # SEARCH URL
# # ============================================================


# def _safe_build_search_url(
#     query,
# ):
#     query = safe_text(query)

#     if not query:
#         return ""

#     try:
#         url = build_google_maps_search_url(query)

#         if url:
#             return url

#     except Exception:
#         pass

#     return "https://www.google.com/maps/search/?api=1&query=" + quote(
#         query,
#         safe="",
#     )


# # ============================================================
# # SEARCH VARIANTS
# # ============================================================


# def build_search_variants(title, address):
#     """ADDRESS PHASE ONLY. Title-only has its own fallback phase."""
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()
#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if title and address:
#         add(f"{address}, {title}")
#         add(f"{title}, {address}")
#     if address:
#         add(address)
#     return variants[:MAX_SEARCH_VARIANTS]


# _TITLE_SEARCH_NOISE_TOKENS = {
#     "hotel",
#     "khach",
#     "san",
#     "khachsan",
#     "homestay",
#     "home",
#     "stay",
#     "resort",
#     "motel",
#     "lodge",
#     "hostel",
# }


# def _build_relaxed_title_for_search(
#     title,
#     address="",
# ):
#     """
#     Build a lighter business-name query for discovery only.

#     Examples:
#         "HALA HOTEL" -> "hala"
#         "HALE HOMESTAY" -> "hale"
#         "Khách sạn THANH PHƯỚC (Hotel) Tây Ninh"
#             -> "thanh phuoc"

#     IMPORTANT:
#         This relaxed title is ONLY used to discover candidates.
#         Candidate verification still uses the ORIGINAL Excel title/address.
#     """
#     normalized_title = _normalize_title(title)

#     if not normalized_title:
#         return ""

#     # Remove generic business/category tokens.
#     tokens = [
#         token
#         for token in normalized_title.split()
#         if token not in _TITLE_SEARCH_NOISE_TOKENS
#     ]

#     # Remove location tokens from the tail of the Excel address so they do
#     # not dominate the business-name query.
#     location_tokens = set()

#     parts = _canonical_location_parts(address)

#     for part in parts[-3:]:
#         for token in _normalize_title(part).split():
#             if len(token) >= 3:
#                 location_tokens.add(token)

#     if location_tokens:
#         tokens = [token for token in tokens if token not in location_tokens]

#     relaxed = " ".join(tokens).strip()

#     # Avoid unusably short generic queries.
#     if len(relaxed) < 3:
#         return ""

#     return relaxed


# def _expected_location_hint_tokens(input_address):
#     """
#     Return safe location hints for an address.

#     Includes old/new province equivalents, e.g.
#         Tay Ninh <-> Long An
#         Binh Dinh <-> Gia Lai
#     """
#     result = set()

#     parts = _canonical_location_parts(input_address)

#     if not parts:
#         return result

#     # Province.
#     province = parts[-1]

#     if province:
#         result.add(province)

#         for group in ADMINISTRATIVE_LOCATION_EQUIVALENT_GROUPS:
#             if province in group:
#                 result.update(group)

#     # One or two locality components before province.
#     for part in parts[-3:-1]:
#         if part and len(part) >= 4:
#             result.add(part)

#     return result


# def _has_expected_location_hint(
#     input_address,
#     actual_title="",
#     actual_url="",
#     candidate=None,
# ):
#     """
#     Controlled fallback evidence when Maps address DOM is unavailable.

#     We accept a location hint only if the Place title / URL title /
#     candidate metadata contains the expected province/locality.

#     This prevents:
#         Mai Vy Hotel (Ho Chi Minh City)
#     from being accepted for:
#         Mai Vy Hotel, Trang Bang, Tay Ninh

#     while allowing:
#         Mai Vy Hotel Tay Ninh
#     when Maps temporarily fails to render its address button.
#     """
#     expected = _expected_location_hint_tokens(
#         input_address,
#     )

#     if not expected:
#         return False

#     values = [
#         actual_title,
#         extract_place_title_from_url(
#             actual_url,
#         ),
#     ]

#     if candidate:
#         values.append(
#             candidate.get(
#                 "title",
#                 "",
#             )
#         )

#         values.extend(
#             candidate.get(
#                 "texts",
#                 [],
#             )
#             or []
#         )

#     haystack = _normalize_address(
#         " | ".join(_normalize_text(value) for value in values if _normalize_text(value))
#     )

#     if not haystack:
#         return False

#     for token in expected:
#         token = _canonical_address_token(
#             token,
#         )

#         if not token or len(token) < 3:
#             continue

#         pattern = r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])"

#         if re.search(
#             pattern,
#             haystack,
#             re.IGNORECASE,
#         ):
#             return True

#     return False


# def build_title_search_variants(title, address):
#     """
#     TITLE FALLBACK with controlled relaxed discovery.

#     The ORIGINAL Excel title/address are still used for verification.

#     Typical order:
#         1. Original title
#         2. Original title + location tail
#         3. Relaxed business name + province
#         4. Relaxed business name

#     This helps cases such as:
#         HALA HOTEL
#         HALE HOMESTAY
#         Khách sạn THANH PHƯỚC (Hotel) Tây Ninh
#     without lowering verification safety.
#     """
#     title = safe_text(title)
#     address = safe_text(address)
#     variants, seen = [], set()

#     def add(query):
#         query = safe_text(query)
#         key = _normalize_text(query).casefold()

#         if query and key not in seen:
#             seen.add(key)
#             variants.append(query)

#     if not title:
#         return variants

#     add(title)

#     parts = _extract_location_parts(address)

#     if len(parts) >= 2:
#         add(f"{title}, {', '.join(parts[-2:])}")

#     elif parts:
#         add(f"{title}, {parts[-1]}")

#     relaxed = _build_relaxed_title_for_search(
#         title,
#         address,
#     )

#     if relaxed:
#         province = get_address_province(
#             address,
#         )

#         if province:
#             add(f"{relaxed}, {province}")

#         add(relaxed)

#     # For address-less records, exact quoted title remains useful.
#     if not _normalize_text(address):
#         add(f'"{title}"')

#     return variants[:MAX_TITLE_SEARCH_VARIANTS]


# # ============================================================
# # LOG
# # ============================================================


# def _log_candidate_summary(
#     logger,
#     index,
#     total,
#     verified,
# ):
#     if not logger:
#         return

#     try:
#         logger.debug(
#             "Candidate %s/%s | "
#             "title=%r | "
#             "address=%r | "
#             "title_score=%.3f | "
#             "address_score=%.3f | "
#             "location_score=%.3f | "
#             "success=%s | "
#             "reason=%s",
#             index,
#             total,
#             verified.get(
#                 "title",
#                 "",
#             ),
#             verified.get(
#                 "address",
#                 "",
#             ),
#             verified.get(
#                 "title_score",
#                 0.0,
#             ),
#             verified.get(
#                 "address_score",
#                 0.0,
#             ),
#             verified.get(
#                 "location_score",
#                 0.0,
#             ),
#             verified.get(
#                 "success",
#                 False,
#             ),
#             verified.get(
#                 "reason",
#                 "",
#             ),
#         )

#     except Exception:
#         pass


# def _candidate_prefilter_title_score(
#     candidate,
#     input_title,
# ):
#     """
#     Compute a cheap title score from search-result text/URL
#     before opening the candidate Place.
#     """
#     if not candidate:
#         return 0.0

#     candidate_title = _normalize_text(
#         candidate.get(
#             "title",
#             "",
#         )
#     )

#     url_title = extract_place_title_from_url(
#         candidate.get("url") or candidate.get("google_maps_url") or ""
#     )

#     texts = " ".join(
#         candidate.get(
#             "texts",
#             [],
#         )
#         or []
#     )

#     return max(
#         title_match_score(
#             input_title,
#             candidate_title,
#             texts,
#         ),
#         title_match_score(
#             input_title,
#             url_title,
#             texts,
#         ),
#     )


# # ============================================================
# # PROCESS SEARCH PAGE
# # ============================================================


# def _process_search_page(
#     page,
#     context,
#     title,
#     address,
#     search_mode="address",
#     logger=None,
# ):
#     """
#     Process one Google Maps search page.

#     Critical rules:
#         - Direct /maps/place/ redirect -> VERIFY then RETURN.
#         - Do not scan result cards after we are already on a Place page.
#         - Search DOM may hydrate late, so retry candidate extraction a
#           few short times before declaring NO_CANDIDATES.
#         - Candidate card/review text is used only for ranking, never as
#           the authoritative address.
#     """
#     if page is None:
#         return None

#     close_google_popups(page)

#     wait_for_search_results(
#         page,
#         PAGE_TIMEOUT,
#     )

#     # ========================================================
#     # 1. DIRECT PLACE REDIRECT
#     # ========================================================
#     current_url = None

#     for direct_attempt in range(4):
#         current_url = get_current_google_maps_place_url_robust(
#             page,
#         )

#         if current_url:
#             if logger:
#                 logger.info(
#                     "DIRECT PLACE DETECTED | attempt=%s/4 | "
#                     "mode=%s | title=%r | url=%r",
#                     direct_attempt + 1,
#                     search_mode,
#                     title,
#                     current_url,
#                 )

#             break

#         if direct_attempt < 3:
#             try:
#                 page.wait_for_timeout(200)
#             except Exception:
#                 time.sleep(0.20)

#     if current_url:
#         verified = verify_candidate_address(
#             page,
#             context,
#             {
#                 "url": current_url,
#                 "google_maps_url": current_url,
#                 "title": extract_place_title_from_url(current_url),
#                 "texts": [],
#             },
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         return verified

#     # ========================================================
#     # 2. SEARCH RESULTS PAGE - HYDRATION RETRIES
#     # ========================================================
#     candidates = []

#     for hydration_attempt in range(SEARCH_RESULT_HYDRATION_RETRIES + 1):
#         candidates = extract_result_candidates(page)

#         if candidates:
#             break

#         if hydration_attempt >= SEARCH_RESULT_HYDRATION_RETRIES:
#             break

#         try:
#             page.wait_for_timeout(SEARCH_RESULT_HYDRATION_DELAY_MS)
#         except Exception:
#             time.sleep(SEARCH_RESULT_HYDRATION_DELAY_MS / 1000.0)

#         current_url = get_current_google_maps_place_url_robust(page)

#         if current_url:
#             return verify_candidate_address(
#                 page,
#                 context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": extract_place_title_from_url(current_url),
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode=search_mode,
#                 logger=logger,
#             )

#     if not candidates:
#         current_url = get_current_google_maps_place_url_robust(
#             page,
#         )

#         if current_url:
#             if logger:
#                 logger.info(
#                     "DIRECT PLACE RECOVERED BEFORE NO_CANDIDATES | "
#                     "mode=%s | title=%r | url=%r",
#                     search_mode,
#                     title,
#                     current_url,
#                 )

#             return verify_candidate_address(
#                 page,
#                 context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": extract_place_title_from_url(
#                         current_url,
#                     ),
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode=search_mode,
#                 logger=logger,
#             )

#         if logger:
#             logger.info(
#                 "NO_CANDIDATES | mode=%s | title=%r | address=%r | page_url=%r",
#                 search_mode,
#                 title,
#                 address,
#                 getattr(page, "url", ""),
#             )

#         return None

#     # ========================================================
#     # 3. RANK + CHEAP PREFILTER
#     # ========================================================
#     for candidate in candidates:
#         prefilter_title_score = _candidate_prefilter_title_score(
#             candidate,
#             title,
#         )

#         candidate["_prefilter_title_score"] = prefilter_title_score

#         if search_mode == "title":
#             candidate["_rank_score"] = prefilter_title_score
#         else:
#             candidate["_rank_score"] = _candidate_text_score(
#                 candidate,
#                 title,
#                 address,
#             )

#     candidates.sort(
#         key=lambda item: item.get(
#             "_rank_score",
#             0.0,
#         ),
#         reverse=True,
#     )

#     limit = (
#         MAX_TITLE_CANDIDATES_TO_VERIFY
#         if search_mode == "title"
#         else MAX_CANDIDATES_TO_VERIFY
#     )

#     min_pre_title = (
#         TITLE_CANDIDATE_PREFILTER_TITLE_MIN
#         if search_mode == "title"
#         else ADDRESS_CANDIDATE_PREFILTER_TITLE_MIN
#     )

#     # ========================================================
#     # 4. VERIFY PLAUSIBLE CANDIDATES
#     # ========================================================
#     best_failed = None
#     opened = 0
#     had_candidate_after_prefilter = False

#     for candidate in candidates:
#         pre_title = candidate.get(
#             "_prefilter_title_score",
#             0.0,
#         )

#         if pre_title < min_pre_title:
#             if logger:
#                 logger.debug(
#                     "SKIP CANDIDATE PREFILTER | "
#                     "mode=%s | title_score=%.3f | "
#                     "candidate_title=%r | url=%r",
#                     search_mode,
#                     pre_title,
#                     candidate.get("title", ""),
#                     candidate.get("url"),
#                 )
#             continue

#         had_candidate_after_prefilter = True
#         opened += 1

#         verified = verify_candidate_address(
#             page,
#             context,
#             candidate,
#             title,
#             address,
#             search_mode=search_mode,
#             logger=logger,
#         )

#         _log_candidate_summary(
#             logger,
#             opened,
#             limit,
#             verified,
#         )

#         if verified.get("page") is not None:
#             page = verified["page"]

#         if verified.get("success"):
#             return verified

#         if search_mode == "title":
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.80
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.20
#             )
#         else:
#             failure_score = (
#                 verified.get(
#                     "title_score",
#                     0.0,
#                 )
#                 * 0.55
#                 + verified.get(
#                     "address_score",
#                     0.0,
#                 )
#                 * 0.45
#             )

#         previous_score = (
#             best_failed.get(
#                 "_page_failure_score",
#                 -1.0,
#             )
#             if best_failed
#             else -1.0
#         )

#         if failure_score > previous_score:
#             verified["_page_failure_score"] = failure_score
#             best_failed = verified

#         if opened >= limit:
#             break

#     # ========================================================
#     # 5. FINAL RESULT FOR THIS QUERY
#     # ========================================================
#     if best_failed is None and not had_candidate_after_prefilter:
#         return {
#             "success": False,
#             "url": None,
#             "google_maps_url": None,
#             "title": "",
#             "address": "",
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": (
#                 "TITLE_PREFILTER_REJECTED"
#                 if search_mode == "title"
#                 else "ADDRESS_PREFILTER_REJECTED"
#             ),
#             "page": page,
#             "search_mode": search_mode,
#         }

#     if best_failed is not None:
#         return best_failed

#     return None


# # ============================================================
# # PUBLIC RESULT STANDARDIZATION
# # ============================================================


# def _standardize_search_result(
#     result,
#     success=None,
#     detail_reason=None,
# ):
#     """
#     Normalize the FINAL result returned by GoogleMapsSearchEngine.search().

#     Public contract:
#         success=True
#             status = FOUND
#             reason = VERIFIED

#         success=False
#             status = MISSING
#             reason = NOT_VERIFIED

#     The technical/internal reason is preserved in:
#         detail_reason
#     """
#     result = dict(result or {})

#     if success is None:
#         success = bool(
#             result.get(
#                 "success",
#                 False,
#             )
#         )

#     internal_reason = (
#         detail_reason
#         or result.get("detail_reason")
#         or result.get("reason")
#         or ("VERIFIED_CANDIDATE" if success else "NOT_FOUND")
#     )

#     result["success"] = bool(success)
#     result["status"] = "FOUND" if success else "MISSING"
#     result["reason"] = "VERIFIED" if success else "NOT_VERIFIED"
#     result["detail_reason"] = internal_reason

#     if success:
#         result["google_maps_url"] = result.get("google_maps_url") or result.get("url")
#         result["url"] = result.get("url") or result.get("google_maps_url")
#     else:
#         # A failed public result must never expose a candidate as FOUND.
#         result["google_maps_url"] = None
#         result["url"] = None

#     return result


# def _failure_priority(reason):
#     """
#     Higher value = more useful final diagnostic reason.

#     Prevents a later weak failure such as TITLE_PREFILTER_REJECTED
#     from overwriting a stronger earlier failure such as
#     ADDRESS_UNAVAILABLE or TITLE_LOCATION_MISMATCH.
#     """
#     priority = {
#         "PROVINCE_MISMATCH": 100,
#         "TITLE_LOCATION_MISMATCH": 95,
#         "ADDRESS_MISMATCH": 90,
#         "EXISTING_LOCATION_MISMATCH": 90,
#         "ADDRESS_UNAVAILABLE": 85,
#         "TITLE_MISMATCH": 80,
#         "ADDRESS_TITLE_MISMATCH": 75,
#         "EXISTING_TITLE_MISMATCH": 75,
#         "TITLE_NAVIGATION_FAILED": 60,
#         "NAVIGATION_FAILED": 60,
#         "TITLE_NO_CANDIDATES": 40,
#         "ADDRESS_NO_CANDIDATES": 40,
#         "TITLE_PREFILTER_REJECTED": 30,
#         "ADDRESS_PREFILTER_REJECTED": 30,
#         "PAGE_NONE": 20,
#         "TITLE_EMPTY": 20,
#         "NOT_FOUND": 10,
#     }

#     return priority.get(
#         safe_text(reason),
#         50,
#     )


# # ============================================================
# # SEARCH ENGINE
# # ============================================================


# class GoogleMapsSearchEngine:
#     def __init__(
#         self,
#         page,
#         context=None,
#         logger=None,
#     ):
#         self.page = page
#         self.context = context
#         self.logger = logger

#     # ========================================================
#     # RECOVER PAGE
#     # ========================================================

#     def _recover_page(self):
#         if self.context is not None:
#             try:
#                 for page in reversed(self.context.pages):
#                     if not page.is_closed():
#                         self.page = page
#                         return page

#             except Exception:
#                 pass

#         return self.page

#     # ========================================================
#     # UPDATE PAGE
#     # ========================================================

#     def update_page(
#         self,
#         page,
#     ):
#         if page is None:
#             return

#         self.page = page

#         try:
#             if self.context is None:
#                 self.context = page.context
#         except Exception:
#             pass

#     # ========================================================
#     # LOG
#     # ========================================================

#     def _log_info(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.info(message)
#             except Exception:
#                 pass

#     def _log_warning(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.warning(message)
#             except Exception:
#                 pass

#     def _log_debug(
#         self,
#         message,
#     ):
#         if self.logger:
#             try:
#                 self.logger.debug(message)
#             except Exception:
#                 pass

#     # ========================================================
#     # CURRENT PLACE PAGE
#     # ========================================================

#     def _try_current_place_page(self, title, address):
#         """
#         Reuse current Place only if its URL title is already plausibly
#         related to the requested title. This avoids spending DOM work on
#         the previous record's Place page.
#         """
#         page = self.page

#         if page is None:
#             return None

#         try:
#             current_url = get_current_google_maps_url(page)

#             if not current_url:
#                 return None

#             current_title = extract_place_title_from_url(current_url)

#             if (
#                 title_match_score(
#                     title,
#                     current_title,
#                 )
#                 < 0.88
#             ):
#                 return None

#             return verify_candidate_address(
#                 page,
#                 self.context,
#                 {
#                     "url": current_url,
#                     "google_maps_url": current_url,
#                     "title": current_title,
#                     "texts": [],
#                 },
#                 title,
#                 address,
#                 search_mode="existing",
#                 logger=self.logger,
#             )

#         except Exception as error:
#             self._log_debug("Current place page check failed: " + str(error))

#         return None

#     # ========================================================
#     # SEARCH
#     # ========================================================

#     def search(self, title, address, timeout=None):
#         """
#         FINAL PUBLIC SEARCH CONTRACT

#         SUCCESS:
#             {
#                 "success": True,
#                 "status": "FOUND",
#                 "reason": "VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": "...",
#                 ...
#             }

#         FAILURE:
#             {
#                 "success": False,
#                 "status": "MISSING",
#                 "reason": "NOT_VERIFIED",
#                 "detail_reason": "<technical reason>",
#                 "google_maps_url": None,
#                 ...
#             }

#         Internal verification functions still keep their detailed reasons.
#         """
#         timeout = timeout or PAGE_TIMEOUT
#         title = safe_text(title)
#         address = safe_text(address)

#         base = {
#             "success": False,
#             "status": "MISSING",
#             "url": None,
#             "google_maps_url": None,
#             "title": title,
#             "address": address,
#             "score": 0.0,
#             "title_score": 0.0,
#             "address_score": 0.0,
#             "location_score": 0.0,
#             "coordinates": None,
#             "matched_tokens": [],
#             "reason": "",
#             "detail_reason": "",
#             "attempts": 0,
#             "page": self.page,
#             "search_mode": "",
#         }

#         # ====================================================
#         # INPUT GUARDS
#         # ====================================================
#         if not title:
#             base["detail_reason"] = "TITLE_EMPTY"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="TITLE_EMPTY",
#             )

#         self._recover_page()

#         if self.page is None:
#             base["detail_reason"] = "PAGE_NONE"

#             return _standardize_search_result(
#                 base,
#                 success=False,
#                 detail_reason="PAGE_NONE",
#             )

#         if self.context is None:
#             try:
#                 self.context = self.page.context
#             except Exception:
#                 pass

#         # ====================================================
#         # CURRENT PLACE REUSE
#         # ====================================================
#         current_result = self._try_current_place_page(
#             title,
#             address,
#         )

#         if current_result and current_result.get("success"):
#             current_result["search_mode"] = "existing"

#             return _standardize_search_result(
#                 current_result,
#                 success=True,
#                 detail_reason=current_result.get(
#                     "reason",
#                     "EXISTING_PLACE_ACCEPTED",
#                 ),
#             )

#         best_failed = None
#         best_failed_priority = -1
#         best_failed_score = -1.0

#         def remember_failure(result, fallback_reason):
#             nonlocal best_failed
#             nonlocal best_failed_priority
#             nonlocal best_failed_score

#             if result is None:
#                 detail = fallback_reason
#                 priority = _failure_priority(detail)
#                 score = 0.0

#                 candidate = {
#                     "success": False,
#                     "page": self.page,
#                     "title_score": 0.0,
#                     "address_score": 0.0,
#                     "location_score": 0.0,
#                     "coordinates": None,
#                     "matched_tokens": [],
#                     "reason": detail,
#                 }

#             else:
#                 detail = result.get("reason") or fallback_reason

#                 priority = _failure_priority(detail)

#                 score = (
#                     result.get(
#                         "title_score",
#                         0.0,
#                     )
#                     * 0.65
#                     + result.get(
#                         "address_score",
#                         0.0,
#                     )
#                     * 0.25
#                     + result.get(
#                         "location_score",
#                         0.0,
#                     )
#                     * 0.10
#                 )

#                 candidate = result

#             # Technical reason quality wins first.
#             # Score breaks ties.
#             if priority > best_failed_priority or (
#                 priority == best_failed_priority and score > best_failed_score
#             ):
#                 best_failed = candidate
#                 best_failed_priority = priority
#                 best_failed_score = score

#         # ====================================================
#         # PHASE 1: ADDRESS
#         # ====================================================
#         address_variants = build_search_variants(
#             title,
#             address,
#         )

#         for idx, query in enumerate(
#             address_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps ADDRESS search {idx}/{len(address_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps ADDRESS navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="address",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "ADDRESS_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "address"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "ADDRESS_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "ADDRESS_NO_CANDIDATES",
#             )

#         # ====================================================
#         # PHASE 2: TITLE FALLBACK
#         # ====================================================
#         title_variants = build_title_search_variants(
#             title,
#             address,
#         )

#         self._log_info(f"ADDRESS PHASE FAILED -> TITLE FALLBACK | {title} | {address}")

#         for idx, query in enumerate(
#             title_variants,
#             1,
#         ):
#             self._recover_page()

#             search_url = _safe_build_search_url(query)

#             if not search_url:
#                 continue

#             self._log_info(
#                 f"Google Maps TITLE fallback {idx}/{len(title_variants)}: {query}"
#             )

#             try:
#                 new_page, success, attempts = safe_goto(
#                     self.page,
#                     self.context,
#                     search_url,
#                     logger=self.logger,
#                     timeout=timeout,
#                 )

#                 if new_page is not None:
#                     self.update_page(new_page)

#             except Exception as error:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )

#                 self._log_warning(
#                     "Google Maps TITLE navigation error: " + str(error)[:250]
#                 )
#                 continue

#             base["attempts"] += attempts

#             if not success:
#                 remember_failure(
#                     None,
#                     "TITLE_NAVIGATION_FAILED",
#                 )
#                 continue

#             result = _process_search_page(
#                 self.page,
#                 self.context,
#                 title,
#                 address,
#                 search_mode="title",
#                 logger=self.logger,
#             )

#             if result is None:
#                 remember_failure(
#                     None,
#                     "TITLE_NO_CANDIDATES",
#                 )
#                 continue

#             if result.get("success"):
#                 result["search_mode"] = "title"

#                 return _standardize_search_result(
#                     result,
#                     success=True,
#                     detail_reason=result.get(
#                         "reason",
#                         "TITLE_SEARCH_ACCEPTED",
#                     ),
#                 )

#             remember_failure(
#                 result,
#                 "TITLE_NO_CANDIDATES",
#             )

#         # ====================================================
#         # FINAL FAILURE
#         # ====================================================
#         base["page"] = self.page
#         base["search_mode"] = "title_fallback_exhausted"

#         detail_reason = "NOT_FOUND"

#         if best_failed:
#             detail_reason = best_failed.get("reason") or "NOT_FOUND"

#             base["title_score"] = best_failed.get(
#                 "title_score",
#                 0.0,
#             )

#             base["address_score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["location_score"] = best_failed.get(
#                 "location_score",
#                 0.0,
#             )

#             base["score"] = best_failed.get(
#                 "address_score",
#                 0.0,
#             )

#             base["coordinates"] = best_failed.get("coordinates")

#             base["matched_tokens"] = best_failed.get(
#                 "matched_tokens",
#                 [],
#             )

#         base["detail_reason"] = detail_reason

#         self._log_warning(
#             "Google Maps NOT_VERIFIED after ADDRESS + TITLE fallback | "
#             f"title={title!r} | "
#             f"address={address!r} | "
#             f"detail_reason={detail_reason}"
#         )

#         return _standardize_search_result(
#             base,
#             success=False,
#             detail_reason=detail_reason,
#         )


# # ============================================================
# # PUBLIC FUNCTION
# # ============================================================


# def search_google_maps(
#     page,
#     title,
#     address,
#     context=None,
#     logger=None,
#     timeout=None,
# ):
#     return GoogleMapsSearchEngine(
#         page,
#         context=context,
#         logger=logger,
#     ).search(
#         title,
#         address,
#         timeout=timeout,
#     )
# ============================================================
# app/search.py
# Address-first Google Maps search
# ACCEPT ANY VALID GOOGLE MAPS PLACE URL
# ============================================================

import re
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote, unquote, urlparse

from config import (
    SEARCH_POLL_INTERVAL,
    SEARCH_POLL_COUNT,
    FINAL_SEARCH_CHECK_DELAY,
    PAGE_TIMEOUT,
)

from .recovery import safe_goto
from .utils import (
    safe_text,
    clean_google_maps_url,
    build_google_maps_search_url,
)


# ============================================================
# CONFIG
# ============================================================

SELECTED_PLACE_RETRIES = 2

SELECTED_PLACE_RETRY_DELAY = min(
    max(float(SEARCH_POLL_INTERVAL or 0.15), 0.15),
    0.6,
)

COORDINATE_SCAN_MAX_LINKS = 100
COORDINATE_SCAN_MAX_DATA_ELEMENTS = 100

PLACE_URL_SCAN_MAX_LINKS = 300
PLACE_URL_SCAN_MAX_DATA_ELEMENTS = 300

RESULT_CARD_MAX = 60
CARD_LINK_MAX = 30

MAX_CANDIDATES_TO_VERIFY = 40
MAX_SEARCH_VARIANTS = 14


# ============================================================
# LEGACY VALIDATION CONFIG
#
# These values are kept because other code may import them.
# IMPORTANT:
#
# A valid /maps/place/ URL is now enough for SUCCESS.
#
# Title/address/location scores are still calculated for
# logging/debugging, but they DO NOT block a valid Place URL.
# ============================================================

ADDRESS_MATCH_MIN_SCORE = 0.55
MIN_VALID_PLACE_ADDRESS_SCORE = 0.55
MIN_VALID_PLACE_LOCATION_SCORE = 0.50
MIN_LOCATION_MATCHES = 1

# Kept for backward compatibility.
# No longer blocks a valid Google Maps Place URL.
REQUIRE_LOCATION_FOR_PLACE = False


# ============================================================
# GENERIC ADDRESS TOKENS
# ============================================================

GENERIC_ADDRESS_TOKENS = {
    "vietnam",
    "viet nam",
    "vn",
    "street",
    "road",
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
    "p",
    "q",
}


ADMIN_PREFIX_RE = re.compile(
    r"^(?:phuong|xa|thi\s+tran|quan|huyen|thi\s+xa|"
    r"thanh\s+pho|tp|tinh)\s+",
    re.IGNORECASE,
)


COORDINATE_PATTERNS = (
    re.compile(
        r"!3d(-?\d+(?:\.\d+?)?)!4d(-?\d+(?:\.\d+?)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"@(-?\d+(?:\.\d+?)?),(-?\d+(?:\.\d+?)?)",
        re.IGNORECASE,
    ),
)


# ============================================================
# TEXT NORMALIZATION
# ============================================================


def _normalize_text(value):
    if value is None:
        return ""

    try:
        value = safe_text(value)
    except Exception:
        try:
            value = str(value)
        except Exception:
            return ""

    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\xa0", " ").strip(),
    )


def _remove_accents(value):
    value = _normalize_text(value).lower()

    if not value:
        return ""

    try:
        value = unicodedata.normalize("NFKD", value)
        value = "".join(c for c in value if not unicodedata.combining(c))
    except Exception:
        pass

    return value


def _normalize_address(value):
    value = _remove_accents(value)

    if not value:
        return ""

    value = value.replace("&", " va ")

    value = re.sub(r"[/|;]+", ",", value)
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"[()\[\]{}]+", " ", value)
    value = re.sub(r"\s*,\s*", ",", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r",+", ",", value)

    return value.strip(" ,")


def _normalize_title(value):
    value = _remove_accents(value)

    if not value:
        return ""

    value = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()



# ============================================================
# FINAL IDENTITY / ADMINISTRATIVE ALIASES
# ============================================================

# Same administrative area before/after merger.
# Keep groups instead of one-way aliases so old and new Excel data
# can be compared in either direction.
ADMINISTRATIVE_LOCATION_GROUPS = (
    frozenset({"tay ninh", "long an"}),
    frozenset({"gia lai", "binh dinh"}),
    frozenset({"ninh binh", "ha nam", "nam dinh"}),
    frozenset({"dak lak", "phu yen"}), 
)


def _canonical_admin_token(value):
    value = _normalize_address(value)

    if not value:
        return ""

    value = ADMIN_PREFIX_RE.sub("", value).strip()

    for group in ADMINISTRATIVE_LOCATION_GROUPS:
        if value in group:
            # Stable canonical key, not dependent on merger direction.
            return "admin:" + "|".join(sorted(group))

    return value


TITLE_GENERIC_TOKENS = {
    "hotel", "hotels", "motel", "motels", "homestay", "homestays",
    "hostel", "hostels", "resort", "resorts", "villa", "villas",
    "apartment", "apartments", "lodge", "lodges", "guesthouse",
    "guesthouses", "guest", "house", "inn", "bnb",
    "khach", "san", "nha", "nghi",
    "at", "in", "on", "by", "of", "the", "and", "with", "near",
    "from", "to", "s",
}


def _title_identity_tokens(value):
    return {
        token
        for token in _normalize_title(value).split()
        if token and token not in TITLE_GENERIC_TOKENS
    }


def title_identity_guard(input_title, actual_title):
    """
    Structural identity check.

    Allows:
        Thao Nghi Hotel <-> Khach san Thao Nghi
        Nhung Trang hotel <-> HOTEL TRANG NHUNG

    Rejects unrelated businesses which only share generic words.
    """
    a = _title_identity_tokens(input_title)
    b = _title_identity_tokens(actual_title)

    if not a or not b:
        # If stripping generic words removed everything, fall back to
        # conservative normalized equality.
        return (
            bool(_normalize_title(input_title))
            and _normalize_title(input_title) == _normalize_title(actual_title)
        )

    if a == b:
        return True

    common = a & b
    if not common:
        return False

    smaller_overlap = len(common) / max(1, min(len(a), len(b)))
    jaccard = len(common) / max(1, len(a | b))

    if a <= b or b <= a:
        return smaller_overlap >= 0.80

    return smaller_overlap >= 0.67 and jaccard >= 0.50


def _strong_title_match(input_title, actual_title):
    score = title_match_score(input_title, actual_title)
    return score >= 0.72 and title_identity_guard(input_title, actual_title)


def _strong_address_match(input_address, actual_address):
    if not _normalize_text(input_address):
        return False
    if not _normalize_text(actual_address):
        return False

    info = address_match_score(input_address, actual_address)
    return bool(info.get("strong_match"))


def _extract_lines(value):
    """
    Preserve real line breaks from Playwright text.
    """

    if value is None:
        return []

    try:
        text = safe_text(value)
    except Exception:
        try:
            text = str(value)
        except Exception:
            return []

    if not text:
        return []

    text = str(text).replace("\r", "\n").replace("\xa0", " ")

    lines = []

    for line in text.split("\n"):
        line = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if line:
            lines.append(line)

    return lines


def _unique_texts(values):
    result = []
    seen = set()

    for value in values:
        text = _normalize_text(value)
        key = text.casefold()

        if text and key not in seen:
            seen.add(key)
            result.append(text)

    return result


# ============================================================
# ADDRESS HELPERS
# ============================================================


def _clean_address_token(token):
    return _normalize_address(token).strip(" ,.-")


def _address_tokens(address):
    normalized = _normalize_address(address)

    if not normalized:
        return []

    result = []

    for token in normalized.split(","):
        token = _clean_address_token(token)

        if not token or len(token) < 2 or token in GENERIC_ADDRESS_TOKENS:
            continue

        result.append(token)

    return result


def _canonical_address_token(token):
    token = _normalize_address(token)

    if not token:
        return ""

    previous = None

    while token != previous:
        previous = token

        token = ADMIN_PREFIX_RE.sub(
            "",
            token,
            count=1,
        ).strip()

    return _canonical_admin_token(token)


def _expand_address_token(token):
    token = _normalize_address(token)

    if not token:
        return []

    variants = [token]
    current = token

    while True:
        stripped = ADMIN_PREFIX_RE.sub(
            "",
            current,
            count=1,
        ).strip()

        if not stripped or stripped == current:
            break

        variants.append(stripped)
        current = stripped

    return _unique_texts(variants)


def _token_matches_address(token, actual_address):
    actual = _normalize_address(actual_address)

    if not actual:
        return False

    canonical = _canonical_address_token(token)

    if not canonical:
        return False

    variants = _unique_texts(_expand_address_token(token) + [canonical])

    actual_parts = [p.strip() for p in actual.split(",") if p.strip()]

    for variant in variants:
        if len(variant) < 4:
            continue

        pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"

        if re.search(
            pattern,
            actual,
        ):
            return True

        for part in actual_parts:
            if part == variant:
                return True

            words = variant.split()

            if len(words) >= 2 and all(
                re.search(
                    r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])",
                    part,
                )
                for word in words
                if len(word) >= 3
            ):
                return True

            if (
                SequenceMatcher(
                    None,
                    variant,
                    part,
                ).ratio()
                >= 0.88
            ):
                return True

    return False


def _extract_location_parts(address):
    result = []

    for part in _normalize_address(address).split(","):
        part = part.strip()

        if not part:
            continue

        if part in {
            "vietnam",
            "viet nam",
            "vn",
        }:
            continue

        result.append(part)

    return result


def _canonical_location_parts(address):
    result = []

    for part in _extract_location_parts(address):
        value = _canonical_address_token(part)

        if value and value not in GENERIC_ADDRESS_TOKENS:
            result.append(value)

    return result


def _location_similarity(
    input_location,
    actual_location,
):
    a = _normalize_address(input_location)
    b = _normalize_address(actual_location)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.90

    return SequenceMatcher(
        None,
        a,
        b,
    ).ratio()


def _location_match_info(
    input_address,
    actual_address,
):
    inputs = _canonical_location_parts(input_address)

    actuals = _canonical_location_parts(actual_address)

    if not inputs or not actuals:
        return {
            "matches": [],
            "count": 0,
            "score": 0.0,
            "tail_matches": 0,
            "tail_score": 0.0,
        }

    matches = []
    used = set()

    for item in inputs:
        best_score = 0.0
        best_actual = ""
        best_index = -1

        for idx, candidate in enumerate(actuals):
            if idx in used:
                continue

            score = _location_similarity(
                item,
                candidate,
            )

            if score > best_score:
                (
                    best_score,
                    best_actual,
                    best_index,
                ) = (
                    score,
                    candidate,
                    idx,
                )

        if best_score >= 0.72:
            used.add(best_index)

            matches.append(
                {
                    "input": item,
                    "actual": best_actual,
                    "score": round(
                        best_score,
                        4,
                    ),
                }
            )

    tail = inputs[-2:] if len(inputs) >= 2 else inputs

    tail_results = []

    for item in tail:
        best = max(
            (
                _location_similarity(
                    item,
                    candidate,
                )
                for candidate in actuals
            ),
            default=0.0,
        )

        tail_results.append(best)

    return {
        "matches": matches,
        "count": len(matches),
        "score": round(
            len(matches) / len(inputs),
            4,
        ),
        "tail_matches": sum(1 for score in tail_results if score >= 0.75),
        "tail_score": round(
            sum(tail_results) / len(tail_results),
            4,
        )
        if tail_results
        else 0.0,
    }


# ============================================================
# ADDRESS SCORE
# ============================================================


def address_match_score(
    input_address,
    actual_address,
):
    a = _normalize_address(input_address)
    b = _normalize_address(actual_address)

    if not a or not b:
        return {
            "score": 0.0,
            "matched_tokens": [],
            "total_tokens": 0,
            "location_matches": 0,
            "location_score": 0.0,
            "tail_location_matches": 0,
            "tail_location_score": 0.0,
            "strong_match": False,
        }

    if a == b:
        tokens = _address_tokens(input_address)

        location_parts = _canonical_location_parts(input_address)

        return {
            "score": 1.0,
            "matched_tokens": tokens,
            "total_tokens": len(tokens),
            "location_matches": len(location_parts),
            "location_score": 1.0,
            "tail_location_matches": len(location_parts[-2:]),
            "tail_location_score": 1.0,
            "strong_match": True,
        }

    tokens = _address_tokens(input_address)

    matched = [
        token
        for token in tokens
        if _token_matches_address(
            token,
            actual_address,
        )
    ]

    token_score = len(matched) / len(tokens) if tokens else 0.0

    location = _location_match_info(
        input_address,
        actual_address,
    )

    location_score = location["score"]

    substring_bonus = 0.15 if len(a) >= 10 and a in b else 0.0

    score = min(
        1.0,
        token_score * 0.60 + location_score * 0.40 + substring_bonus,
    )

    strong = (
        score >= 0.80
        or (
            score >= ADDRESS_MATCH_MIN_SCORE
            and location["count"] >= MIN_LOCATION_MATCHES
        )
        or (len(tokens) <= 2 and matched and score >= 0.40)
    )

    return {
        "score": round(
            score,
            4,
        ),
        "matched_tokens": matched,
        "total_tokens": len(tokens),
        "location_matches": location["count"],
        "location_score": round(
            location_score,
            4,
        ),
        "tail_location_matches": location.get(
            "tail_matches",
            0,
        ),
        "tail_location_score": location.get(
            "tail_score",
            0.0,
        ),
        "strong_match": bool(strong),
    }


def address_is_related(
    input_address,
    actual_address,
):
    return bool(
        address_match_score(
            input_address,
            actual_address,
        ).get("strong_match")
    )


def _result_matches_address(
    input_address,
    result_address,
):
    """
    Return True when the selected place does not contradict the input address.

    Google sometimes renders the address as an icon or leaves it empty while
    loading. In that case there is not enough information to reject the URL.
    """
    actual_address = _normalize_text(result_address)

    if (
        not actual_address
        or not re.search(r"[a-z0-9]", actual_address, re.IGNORECASE)
        or not _address_tokens(actual_address)
    ):
        return True

    info = address_match_score(
        input_address,
        actual_address,
    )

    input_tokens = _address_tokens(input_address)
    matched_tokens = info.get("matched_tokens", [])

    if not info.get("strong_match"):
        return False

    # Shared city/district tokens are not enough to identify a place.
    # Require the first (most specific) input address segment as an anchor.
    if len(input_tokens) >= 2:
        first_token = input_tokens[0]

        if not any(
            _normalize_address(token) == first_token
            for token in matched_tokens
        ):
            return False

    return True


def result_matches_address(
    input_address,
    result_address,
):
    actual_address = _normalize_text(result_address)

    if (
        not actual_address
        or not re.search(r"[a-z0-9]", actual_address, re.IGNORECASE)
        or not _address_tokens(actual_address)
    ):
        return False

    return _result_matches_address(
        input_address,
        actual_address,
    )


def _result_matches_title(
    input_title,
    result_title,
):
    actual_title = _normalize_title(result_title)

    if not actual_title:
        return False

    return _strong_title_match(
        input_title,
        result_title,
    )


def result_matches_title(
    input_title,
    result_title,
):
    return _result_matches_title(
        input_title,
        result_title,
    )


# ============================================================
# TITLE SCORE
# ============================================================


def title_match_score(
    input_title,
    actual_title,
    actual_text="",
):
    a = _normalize_title(input_title)
    b = _normalize_title(actual_title)
    combined = _normalize_title(actual_text)

    if not a:
        return 0.0

    if a == b:
        return 1.0

    if b and a in b:
        return 0.95

    if b and b in a:
        return 0.90

    sequence = (
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()
        if b
        else 0.0
    )

    wanted = {x for x in a.split() if len(x) >= 3}

    got = {x for x in combined.split() if len(x) >= 3}

    overlap = len(wanted & got) / len(wanted) if wanted else 0.0

    return round(
        max(
            sequence,
            overlap,
        ),
        4,
    )


# ============================================================
# COORDINATES
# ============================================================


def _valid_lat_lng(lat, lng):
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return False

    return -90 <= lat <= 90 and -180 <= lng <= 180


def _extract_coordinates_from_text(text):
    if not text:
        return None

    try:
        text = str(text)
    except Exception:
        return None

    for pattern in COORDINATE_PATTERNS:
        match = pattern.search(text)

        if match and _valid_lat_lng(
            match.group(1),
            match.group(2),
        ):
            return (
                float(match.group(1)),
                float(match.group(2)),
            )

    return None


def extract_coordinates_from_url(url):
    coordinates = _extract_coordinates_from_text(url)

    return coordinates if coordinates else (None, None)


def get_current_page_coordinates(page):
    try:
        coordinates = _extract_coordinates_from_text(page.url or "")

        return coordinates if coordinates else (None, None)
    except Exception:
        return None, None


def extract_coordinates_from_page(page):
    if page is None:
        return None

    # --------------------------------------------------------
    # 1. Current URL
    # --------------------------------------------------------

    try:
        value = _extract_coordinates_from_text(page.url or "")

        if value:
            return value
    except Exception:
        pass

    # --------------------------------------------------------
    # 2. Links
    # --------------------------------------------------------

    try:
        links = page.locator("a[href]")

        count = min(
            links.count(),
            COORDINATE_SCAN_MAX_LINKS,
        )

        for i in range(count):
            value = _extract_coordinates_from_text(links.nth(i).get_attribute("href"))

            if value:
                return value

    except Exception:
        pass

    # --------------------------------------------------------
    # 3. data-url / data-href
    # --------------------------------------------------------

    try:
        elements = page.locator("[data-url], [data-href]")

        count = min(
            elements.count(),
            COORDINATE_SCAN_MAX_DATA_ELEMENTS,
        )

        for i in range(count):
            element = elements.nth(i)

            for attr in (
                "data-url",
                "data-href",
            ):
                value = _extract_coordinates_from_text(element.get_attribute(attr))

                if value:
                    return value

    except Exception:
        pass

    return None


# ============================================================
# GOOGLE MAPS URL HELPERS
# ============================================================


def _is_google_maps_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(str(url).strip())
    except (TypeError, ValueError):
        return False

    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {
            "google.com",
            "www.google.com",
            "maps.google.com",
        }
        and parsed.path.lower().startswith("/maps")
    )


def is_google_maps_url(url):
    return _is_google_maps_url(url)


def _is_google_maps_place_url(url):
    """
    ONLY accept real Google Maps Place URLs.

    Accepted:
        /maps/place/Hotel+Name/...

    Rejected:
        /maps/search/...
        random google URL
        generic Google Maps URL
    """

    if not _is_google_maps_url(url):
        return False

    try:
        value = str(url).strip().lower()
    except Exception:
        return False

    if "/maps/search" in value:
        return False

    return "/maps/place/" in value or "/maps/place?" in value


def is_google_maps_place_url(url):
    return _is_google_maps_place_url(url)


def _is_usable_google_maps_place_url(url):
    return _is_google_maps_place_url(url)


def _clean_place_url(url):
    if not url:
        return None

    try:
        value = str(url).strip()
    except Exception:
        return None

    try:
        cleaned = clean_google_maps_url(value)

        if cleaned and _is_google_maps_place_url(cleaned):
            return cleaned

    except Exception:
        pass

    if _is_google_maps_place_url(value):
        return value

    return None


def get_current_google_maps_url(page):
    try:
        return _clean_place_url(page.url or "")
    except Exception:
        return None


def extract_google_maps_url_from_href(href):
    if not href:
        return None

    try:
        value = str(href).strip().replace("&amp;", "&")

        value = (
            value.replace("\\/", "/").replace("\\u003d", "=").replace("\\u0026", "&")
        )

        if value.startswith("/maps/"):
            value = "https://www.google.com" + value

        decoded = unquote(value)

        if _is_google_maps_place_url(decoded):
            value = decoded

        cleaned = clean_google_maps_url(value)

        if cleaned and _is_google_maps_place_url(cleaned):
            return cleaned

    except Exception:
        pass

    return _clean_place_url(href)


# ============================================================
# POPUPS
# ============================================================


def close_google_popups(page):
    if page is None:
        return

    selectors = [
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
        'button:has-text("Đồng ý")',
        'button:has-text("Chấp nhận tất cả")',
        '[aria-label="Accept all"]',
        '[aria-label="I agree"]',
        '[aria-label="Chấp nhận tất cả"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            if locator.count() and locator.first.is_visible(timeout=300):
                locator.first.click(timeout=1200)

                time.sleep(0.15)
                break

        except Exception:
            continue


# ============================================================
# WAIT
# ============================================================


def wait_for_search_results(
    page,
    timeout=None,
):
    if page is None:
        return False

    timeout = timeout or PAGE_TIMEOUT

    deadline = time.time() + timeout / 1000

    interval = max(
        float(SEARCH_POLL_INTERVAL or 0.15),
        0.1,
    )

    selectors = [
        '[role="main"]',
        'div[role="feed"]',
        "div.Nv2PK",
        "h1",
        'a[href*="/maps/place/"]',
    ]

    while time.time() < deadline:
        try:
            if page.is_closed():
                return False
        except Exception:
            return False

        for selector in selectors:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                pass

        time.sleep(interval)

    return False


# ============================================================
# PLAYWRIGHT TEXT
# ============================================================


def _read_locator_text(locator):
    if locator is None:
        return ""

    for method in (
        "inner_text",
        "text_content",
    ):
        try:
            text = getattr(
                locator,
                method,
            )(timeout=1500)

            if text:
                return safe_text(text)

        except Exception:
            pass

    return ""


# ============================================================
# PLACE TITLE
# ============================================================


def extract_selected_place_title(
    page,
    expected_title=None,
):
    if page is None:
        return ""

    values = []

    for selector in (
        '[role="main"] h1',
        "h1",
        '[data-item-id="title"]',
    ):
        try:
            locator = page.locator(selector)

            count = min(
                locator.count(),
                5,
            )

            for i in range(count):
                text = _read_locator_text(locator.nth(i))

                if text:
                    values.append(text)

        except Exception:
            pass

    values = _unique_texts(values)

    if not values:
        return ""

    if expected_title:
        return max(
            values,
            key=lambda x: title_match_score(
                expected_title,
                x,
            ),
        )

    return values[0]


# ============================================================
# PLACE ADDRESS
# ============================================================


def extract_selected_place_address(
    page,
    expected_address="",
):
    """
    Try to extract real address.

    IMPORTANT:
    This value is ONLY used for scoring/logging.
    It is NOT required for accepting a valid
    Google Maps Place URL.
    """

    candidates = []

    selectors = [
        '[role="main"] [data-item-id="address"]',
        '[data-item-id="address"]',
        '[role="main"] [data-item-id*="address"]',
        '[data-item-id*="address"]',
        '[role="main"] [aria-label*="Địa chỉ"]',
        '[role="main"] [aria-label*="Address"]',
        '[role="main"] button[aria-label*="Địa chỉ"]',
        '[role="main"] button[aria-label*="Address"]',
        '[role="main"] a[aria-label*="Địa chỉ"]',
        '[role="main"] a[aria-label*="Address"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = min(
                locator.count(),
                20,
            )

            for i in range(count):
                element = locator.nth(i)

                text = _read_locator_text(element)

                if text:
                    candidates.append(text)

                # aria-label can contain the real
                # address even when inner_text is icon-only.
                try:
                    aria = element.get_attribute("aria-label")

                    if aria:
                        candidates.append(aria)
                except Exception:
                    pass

        except Exception:
            pass

    candidates = _unique_texts(candidates)

    expected = _normalize_text(expected_address)

    if candidates and expected:
        scored = []

        for candidate in candidates:
            info = address_match_score(
                expected,
                candidate,
            )

            scored.append(
                (
                    info["score"],
                    info["location_score"],
                    candidate,
                )
            )

        scored.sort(
            key=lambda x: (
                x[0],
                x[1],
            ),
            reverse=True,
        )

        best_score = scored[0][0]
        best_location_score = scored[0][1]
        best_address = scored[0][2]

        if (
            best_score >= ADDRESS_MATCH_MIN_SCORE
            or best_location_score >= MIN_VALID_PLACE_LOCATION_SCORE
        ):
            return best_address

    if candidates and not expected:
        return candidates[0]

    # --------------------------------------------------------
    # Fallback main text
    # --------------------------------------------------------

    try:
        main = page.locator('[role="main"]').first

        if main.count():
            lines = _extract_lines(main.inner_text(timeout=2500))

            if expected and lines:
                scored = []

                for line in lines:
                    info = address_match_score(
                        expected,
                        line,
                    )

                    scored.append(
                        (
                            info["score"],
                            line,
                        )
                    )

                scored = [item for item in scored if item[0] >= 0.20]

                if scored:
                    scored.sort(
                        key=lambda x: x[0],
                        reverse=True,
                    )

                    return scored[0][1]

            if lines:
                return lines[0]

    except Exception:
        pass

    return ""


# ============================================================
# ADDRESS FALLBACK
# ============================================================


def _find_best_address_from_main_text(
    page,
    expected_address="",
):
    """
    Final fallback.

    Read visible [role="main"] text.
    Never invent an address.
    """

    if page is None:
        return ""

    try:
        main = page.locator('[role="main"]').first

        if not main.count():
            return ""

        text = main.inner_text(timeout=2500)

        lines = _extract_lines(text)

        if not lines:
            return ""

        expected_address = _normalize_text(expected_address)

        if expected_address:
            scored = []

            for line in lines:
                line = _normalize_text(line)

                if not line:
                    continue

                info = address_match_score(
                    expected_address,
                    line,
                )

                scored.append(
                    (
                        info.get(
                            "score",
                            0.0,
                        ),
                        info.get(
                            "location_score",
                            0.0,
                        ),
                        line,
                    )
                )

            if scored:
                scored.sort(
                    key=lambda item: (
                        item[0],
                        item[1],
                    ),
                    reverse=True,
                )

                (
                    best_score,
                    best_location_score,
                    best_line,
                ) = scored[0]

                if best_score >= 0.20 or best_location_score >= 0.25:
                    return best_line

        # ----------------------------------------------------
        # Address-like lines
        # ----------------------------------------------------

        address_like = []

        for line in lines:
            normalized = _normalize_address(line)

            if not normalized:
                continue

            has_number = bool(
                re.search(
                    r"\d",
                    normalized,
                )
            )

            has_comma = "," in normalized

            has_location_word = any(
                token in normalized
                for token in (
                    "vietnam",
                    "viet nam",
                    "street",
                    "road",
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
                    "thi tran",
                    "thi xa",
                )
            )

            if has_number and (has_comma or has_location_word):
                address_like.append(line)

        if address_like:
            return address_like[0]

    except Exception:
        pass

    return ""


# ============================================================
# PLACE URL EXTRACTION
# ============================================================


def extract_place_url_from_page(page):
    if page is None:
        return None

    # --------------------------------------------------------
    # 1. Current page URL
    # --------------------------------------------------------

    current = get_current_google_maps_url(page)

    if current:
        return current

    # --------------------------------------------------------
    # 2. Links
    # --------------------------------------------------------

    try:
        links = page.locator("a[href]")

        count = min(
            links.count(),
            PLACE_URL_SCAN_MAX_LINKS,
        )

        for i in range(count):
            url = extract_google_maps_url_from_href(links.nth(i).get_attribute("href"))

            if url:
                return url

    except Exception:
        pass

    # --------------------------------------------------------
    # 3. data-url / data-href
    # --------------------------------------------------------

    try:
        elements = page.locator("[data-url], [data-href]")

        count = min(
            elements.count(),
            PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
        )

        for i in range(count):
            element = elements.nth(i)

            for attr in (
                "data-url",
                "data-href",
            ):
                url = extract_google_maps_url_from_href(element.get_attribute(attr))

                if url:
                    return url

    except Exception:
        pass

    # --------------------------------------------------------
    # 4. Raw HTML
    # --------------------------------------------------------

    try:
        html = page.locator("body").inner_html(timeout=3000)

        patterns = (
            r'https?://(?:www\.)?google\.com/maps/place/[^"\'>\s]+',
            r'/maps/place/[^"\'>\s]+',
        )

        for pattern in patterns:
            for match in re.findall(
                pattern,
                html,
                re.IGNORECASE,
            ):
                if match.startswith("/"):
                    match = "https://www.google.com" + match

                url = extract_google_maps_url_from_href(match)

                if url:
                    return url

    except Exception:
        pass

    return None


# ============================================================
# RESULT CARD
# ============================================================


def _extract_card_texts(card):
    values = []

    try:
        values.extend(_extract_lines(card.inner_text(timeout=1200)))
    except Exception:
        pass

    try:
        aria = card.get_attribute("aria-label")

        if aria:
            values.append(aria)

    except Exception:
        pass

    return _unique_texts(values)


def _extract_title_from_card_texts(
    texts,
):
    if texts:
        lines = _extract_lines(texts[0])

        if lines:
            return lines[0]

        return texts[0]

    return ""


def _candidate_text_score(
    candidate,
    input_title,
    input_address,
):
    texts = candidate.get(
        "texts",
        [],
    )

    combined = " ".join(texts)

    title_score = title_match_score(
        input_title,
        candidate.get(
            "title",
            "",
        ),
        combined,
    )

    address_info = address_match_score(
        input_address,
        combined,
    )

    return (
        title_score * 0.45
        + address_info["score"] * 0.40
        + address_info["location_score"] * 0.15
    )


# ============================================================
# EXTRACT SEARCH CANDIDATES
# ============================================================


def extract_result_candidates(page):
    if page is None:
        return []

    # --------------------------------------------------------
    # Give Maps time to render.
    # --------------------------------------------------------

    try:
        page.mouse.wheel(
            0,
            1800,
        )

        page.wait_for_timeout(500)

        page.mouse.wheel(
            0,
            1800,
        )

        page.wait_for_timeout(500)

    except Exception:
        pass

    candidates = []
    seen = set()

    cards = []

    # --------------------------------------------------------
    # Search cards
    # --------------------------------------------------------

    for selector in (
        "div.Nv2PK",
        "div.bfdHYd",
        '[role="article"]',
    ):
        try:
            locator = page.locator(selector)

            count = min(
                locator.count(),
                RESULT_CARD_MAX,
            )

            if count:
                cards = [locator.nth(i) for i in range(count)]

                break

        except Exception:
            pass

    # --------------------------------------------------------
    # Parse cards
    # --------------------------------------------------------

    for card in cards:
        try:
            texts = _extract_card_texts(card)

            url = None

            links = card.locator("a[href]")

            count = min(
                links.count(),
                CARD_LINK_MAX,
            )

            for i in range(count):
                url = extract_google_maps_url_from_href(
                    links.nth(i).get_attribute("href")
                )

                if url:
                    break

            if not url or url in seen:
                continue

            seen.add(url)

            candidates.append(
                {
                    "url": url,
                    "google_maps_url": url,
                    "title": (_extract_title_from_card_texts(texts)),
                    "texts": texts,
                }
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # Direct place links
    # --------------------------------------------------------

    if len(candidates) < RESULT_CARD_MAX:
        try:
            links = page.locator('a[href*="/maps/place/"]')

            count = min(
                links.count(),
                PLACE_URL_SCAN_MAX_LINKS,
            )

            for i in range(count):
                try:
                    link = links.nth(i)

                    url = extract_google_maps_url_from_href(link.get_attribute("href"))

                    if not url or url in seen:
                        continue

                    texts = []

                    parent = link.locator("xpath=..")

                    parent_text = _read_locator_text(parent)

                    if parent_text:
                        texts.append(parent_text)

                    link_text = _read_locator_text(link)

                    if link_text:
                        texts.append(link_text)

                    texts = _unique_texts(texts)

                    seen.add(url)

                    candidates.append(
                        {
                            "url": url,
                            "google_maps_url": url,
                            "title": (_extract_title_from_card_texts(texts)),
                            "texts": texts,
                        }
                    )

                    if len(candidates) >= RESULT_CARD_MAX:
                        break

                except Exception:
                    continue

        except Exception:
            pass

    return candidates


# ============================================================
# CLICK / NAVIGATE CANDIDATE
# ============================================================


def click_candidate(
    page,
    candidate,
    context=None,
    logger=None,
):
    if page is None or not candidate:
        return page, False, 0

    url = candidate.get("url") or candidate.get("google_maps_url")

    if not url:
        return page, False, 0

    if context is None:
        try:
            context = page.context
        except Exception:
            pass

    try:
        if context is not None:
            return safe_goto(
                page,
                context,
                url,
                logger=logger,
                timeout=PAGE_TIMEOUT,
            )

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        return page, True, 1

    except Exception as error:
        if logger:
            logger.warning("Candidate navigation error: " + str(error)[:250])

        return page, False, 1


# ============================================================
# DECISION
# ============================================================


def _decide_candidate(
    input_title,
    input_address,
    actual_title,
    actual_address,
    actual_url,
):
    """
    IMPORTANT:

    NEW ACCEPTANCE RULE:

        Valid Google Maps Place URL
                    +
                 navigation
                    =
                 SUCCESS

    Title/address/location are only metadata.

    This intentionally avoids rejecting a valid Place URL
    because Google Maps DOM extraction returned:

        actual_title=''
        actual_address='\\ue52e'

    """

    address_info = address_match_score(
        input_address,
        actual_address,
    )

    title_score = title_match_score(
        input_title,
        actual_title,
        actual_address,
    )

    valid_url = _is_usable_google_maps_place_url(actual_url)

    location_matches = address_info.get(
        "location_matches",
        0,
    )

    location_score = address_info.get(
        "location_score",
        0.0,
    )

    tail_location_matches = address_info.get(
        "tail_location_matches",
        0,
    )

    tail_location_score = address_info.get(
        "tail_location_score",
        0.0,
    )

    address_score = address_info.get(
        "score",
        0.0,
    )

    base = {
        "success": False,
        "title_score": title_score,
        "address_score": address_score,
        "location_matches": location_matches,
        "location_score": location_score,
        "tail_location_matches": tail_location_matches,
        "tail_location_score": tail_location_score,
        "strong_address": address_info.get(
            "strong_match",
            False,
        ),
    }

    # --------------------------------------------------------
    # ONLY HARD REQUIREMENT
    # --------------------------------------------------------

    if not valid_url:
        base["reason"] = "INVALID_PLACE_URL"
        return base

    # --------------------------------------------------------
    # VALID PLACE URL = SUCCESS
    # --------------------------------------------------------

    base["success"] = True
    base["reason"] = "MAPS_PLACE_URL_ACCEPTED"

    return base


# ============================================================
# VERIFY CANDIDATE
# ============================================================


def verify_candidate_address(
    page,
    context,
    candidate,
    input_title,
    input_address,
    logger=None,
):
    """
    Verify candidate.

    NEW BEHAVIOR:

    If navigation reaches a valid Google Maps
    /maps/place/ URL -> ACCEPT IMMEDIATELY.

    We DO NOT require:
        - title
        - address
        - location
        - score
        - coordinates

    Coordinates are collected when possible.
    """

    result = {
        "success": False,
        "url": None,
        "google_maps_url": None,
        "title": "",
        "address": "",
        "score": 0.0,
        "address_score": 0.0,
        "title_score": 0.0,
        "matched_tokens": [],
        "coordinates": None,
        "reason": "",
    }

    if page is None:
        result["reason"] = "PAGE_NONE"
        return result

    if not candidate:
        result["reason"] = "CANDIDATE_NONE"
        return result

    # --------------------------------------------------------
    # Navigate
    # --------------------------------------------------------

    page, success, attempts = click_candidate(
        page,
        candidate,
        context=context,
        logger=logger,
    )

    result["attempts"] = attempts

    if not success or page is None:
        result["reason"] = "NAVIGATION_FAILED"
        return result

    # --------------------------------------------------------
    # Wait
    # --------------------------------------------------------

    wait_for_search_results(
        page,
        PAGE_TIMEOUT,
    )

    close_google_popups(page)

    if FINAL_SEARCH_CHECK_DELAY:
        time.sleep(FINAL_SEARCH_CHECK_DELAY)

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    actual_url = extract_place_url_from_page(page)

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # If DOM extraction failed but the candidate itself
    # was already a valid /maps/place/ URL, keep it.
    # --------------------------------------------------------

    if not actual_url:
        actual_url = get_current_google_maps_url(page)

    if not actual_url:
        candidate_url = candidate.get("url") or candidate.get("google_maps_url")

        if _is_google_maps_place_url(candidate_url):
            actual_url = _clean_place_url(candidate_url)

    # --------------------------------------------------------
    # Extract metadata
    #
    # These NEVER decide acceptance.
    # --------------------------------------------------------

    actual_title = extract_selected_place_title(
        page,
        expected_title=input_title,
    )

    actual_address = extract_selected_place_address(
        page,
        expected_address=input_address,
    )

    if not actual_address:
        actual_address = _find_best_address_from_main_text(
            page,
            input_address,
        )

    coordinates = extract_coordinates_from_page(page)

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    if logger:
        logger.info(
            "VERIFY CANDIDATE | "
            "input_title=%r | "
            "actual_title=%r | "
            "input_address=%r | "
            "actual_address=%r | "
            "url=%r | "
            "coordinates=%r",
            input_title,
            actual_title,
            input_address,
            actual_address,
            actual_url,
            coordinates,
        )

    # --------------------------------------------------------
    # FINAL EVIDENCE-BASED ACCEPTANCE
    # --------------------------------------------------------

    if not actual_url or not _is_google_maps_place_url(actual_url):
        result.update(
            {
                "reason": "NO_VALID_MAPS_PLACE_URL",
                "title": actual_title,
                "address": actual_address,
                "coordinates": coordinates,
                "page": page,
            }
        )
        return result

    info = address_match_score(
        input_address,
        actual_address,
    )

    title_score = title_match_score(
        input_title,
        actual_title,
        actual_address,
    )

    title_ok = _strong_title_match(
        input_title,
        actual_title,
    )

    address_ok = _strong_address_match(
        input_address,
        actual_address,
    )

    input_has_address = bool(
        _normalize_text(input_address)
    )

    actual_has_address = bool(
        _normalize_text(actual_address)
        and _address_tokens(actual_address)
    )

    # RULE:
    # 1. Strong TITLE can anchor the Place. If Maps exposes an address,
    #    that address becomes canonical output.
    # 2. Strong ADDRESS can anchor the Place even when Excel title is stale;
    #    Maps title becomes canonical output.
    # 3. If address is unavailable, title must be structurally strong.
    # 4. A random /maps/place/ URL alone is NEVER enough.
    accepted = False
    reason = ""

    if title_ok and address_ok:
        accepted = True
        reason = "TITLE_AND_ADDRESS_VERIFIED"

    elif title_ok and not input_has_address:
        accepted = True
        reason = "TITLE_VERIFIED_ADDRESS_DISCOVERED"

    elif title_ok and input_has_address and not actual_has_address:
        accepted = True
        reason = "TITLE_VERIFIED_ADDRESS_UNAVAILABLE"

    elif address_ok:
        accepted = True
        reason = "ADDRESS_VERIFIED_TITLE_REPAIRED"

    elif title_ok and actual_has_address:
        # Title is a strong identity anchor. Preserve the Maps address for
        # Excel repair even if the old Excel address is stale.
        accepted = True
        reason = "TITLE_VERIFIED_ADDRESS_REPAIRED"

    else:
        if not actual_title:
            reason = "TITLE_UNAVAILABLE"
        elif not title_ok:
            reason = "TITLE_MISMATCH"
        elif input_has_address and not actual_has_address:
            reason = "ADDRESS_UNAVAILABLE"
        else:
            reason = "NOT_VERIFIED"

    result.update(
        {
            "success": accepted,
            "url": actual_url,
            "google_maps_url": actual_url,
            "title": actual_title,
            "address": actual_address,
            "score": info.get("score", 0.0),
            "address_score": info.get("score", 0.0),
            "title_score": title_score,
            "matched_tokens": info.get("matched_tokens", []),
            "coordinates": coordinates,
            "reason": reason,
            "location_matches": info.get("location_matches", 0),
            "location_score": info.get("location_score", 0.0),
            "tail_location_matches": info.get("tail_location_matches", 0),
            "tail_location_score": info.get("tail_location_score", 0.0),
            "page": page,
            "title_verified": bool(title_ok),
            "address_verified": bool(address_ok),
        }
    )

    if logger:
        logger.info(
            "VERIFY FINAL | success=%s | reason=%s | "
            "title_ok=%s | address_ok=%s | "
            "input_title=%r | maps_title=%r | "
            "input_address=%r | maps_address=%r | url=%r",
            accepted,
            reason,
            title_ok,
            address_ok,
            input_title,
            actual_title,
            input_address,
            actual_address,
            actual_url,
        )

    return result


# ============================================================
# RECOVER COORDINATES
# ============================================================


def recover_candidate_coordinates(
    page,
    context,
    candidate,
    logger=None,
):
    for _ in range(SELECTED_PLACE_RETRIES):
        try:
            page, success, _ = click_candidate(
                page,
                candidate,
                context=context,
                logger=logger,
            )

            if success:
                wait_for_search_results(
                    page,
                    PAGE_TIMEOUT,
                )

                coordinates = extract_coordinates_from_page(page)

                if coordinates:
                    return coordinates

        except Exception:
            pass

        time.sleep(SELECTED_PLACE_RETRY_DELAY)

    return None


# ============================================================
# SEARCH URL
# ============================================================


def _safe_build_search_url(query):
    query = safe_text(query)

    if not query:
        return ""

    try:
        url = build_google_maps_search_url(query)

        if url:
            return url

    except Exception:
        pass

    return "https://www.google.com/maps/search/?api=1&query=" + quote(
        query,
        safe="",
    )


# ============================================================
# SEARCH VARIANTS
# ============================================================


def build_search_variants(
    title,
    address,
):
    """
    Build broad address-driven Maps queries.
    """

    title = safe_text(title)
    address = safe_text(address)

    variants = []
    seen = set()

    parts = _extract_location_parts(address)

    def add(query):
        query = safe_text(query)

        key = _normalize_text(query).casefold()

        if query and key not in seen:
            seen.add(key)
            variants.append(query)

    # --------------------------------------------------------
    # Most reliable first
    # --------------------------------------------------------

    if address:
        add(address)

    if address and title:
        add(f"{address}, {title}")

        add(f"{title}, {address}")

    if address and len(parts) >= 3:
        add(", ".join(parts[-3:]))

    if address and len(parts) >= 2:
        add(", ".join(parts[-2:]))

    if title and len(parts) >= 3:
        add(f"{title}, {', '.join(parts[-3:])}")

    if title and len(parts) >= 2:
        add(f"{title}, {', '.join(parts[-2:])}")

    if title and parts:
        for part in parts[-3:]:
            add(f"{title}, {part}")

    if title:
        add(title)

    return variants[:MAX_SEARCH_VARIANTS]


# ============================================================
# LOG CANDIDATE
# ============================================================


def _log_candidate_summary(
    logger,
    index,
    total,
    verified,
):
    if not logger:
        return

    try:
        logger.debug(
            "Candidate %s/%s | "
            "title=%r | "
            "address=%r | "
            "title_score=%.3f | "
            "address_score=%.3f | "
            "location_score=%.3f | "
            "success=%s | "
            "reason=%s",
            index,
            total,
            verified.get(
                "title",
                "",
            ),
            verified.get(
                "address",
                "",
            ),
            verified.get(
                "title_score",
                0.0,
            ),
            verified.get(
                "address_score",
                0.0,
            ),
            verified.get(
                "location_score",
                0.0,
            ),
            verified.get(
                "success",
                False,
            ),
            verified.get(
                "reason",
                "",
            ),
        )

    except Exception:
        pass


# ============================================================
# PROCESS SEARCH PAGE
# ============================================================


def _process_search_page(
    page,
    context,
    title,
    address,
    search_mode="address",
    logger=None,
):
    if page is None:
        return None

    close_google_popups(page)

    wait_for_search_results(
        page,
        PAGE_TIMEOUT,
    )

    if FINAL_SEARCH_CHECK_DELAY:
        time.sleep(FINAL_SEARCH_CHECK_DELAY)

    # --------------------------------------------------------
    # If current page is already a Place page:
    #
    # ACCEPT immediately.
    # --------------------------------------------------------

    current_url = get_current_google_maps_url(page)

    if current_url:
        verified = verify_candidate_address(
            page,
            context,
            {
                "url": current_url,
                "google_maps_url": current_url,
            },
            title,
            address,
            logger=logger,
        )

        if (
            verified.get("success")
            and (
                (
                    search_mode == "address"
                    and _result_matches_address(
                        address,
                        verified.get("address", ""),
                    )
                    and _result_matches_title(
                        title,
                        verified.get("title", ""),
                    )
                )
                or (
                    search_mode == "title"
                    and _result_matches_title(
                        title,
                        verified.get("title", ""),
                    )
                )
            )
        ):
            return verified

        if verified.get("success") and logger:
            logger.warning(
                "SEARCH RESULT MISMATCH | mode=%s | input_title=%r | "
                "actual_title=%r | input_address=%r | actual_address=%r | url=%r",
                search_mode,
                title,
                verified.get("title", ""),
                address,
                verified.get("address", ""),
                verified.get("google_maps_url") or verified.get("url"),
            )

    # --------------------------------------------------------
    # Search result cards
    # --------------------------------------------------------

    candidates = extract_result_candidates(page)

    if not candidates:
        return None

    # --------------------------------------------------------
    # Rank candidates only to decide order.
    #
    # Ranking does NOT decide acceptance.
    # --------------------------------------------------------

    for candidate in candidates:
        candidate["_rank_score"] = _candidate_text_score(
            candidate,
            title,
            address if search_mode == "address" else "",
        )

    candidates.sort(
        key=lambda x: x.get(
            "_rank_score",
            0.0,
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # Verify candidates.
    #
    # First valid Place URL = SUCCESS.
    # --------------------------------------------------------

    best_failed = None

    for index, candidate in enumerate(
        candidates[:MAX_CANDIDATES_TO_VERIFY],
        1,
    ):
        verified = verify_candidate_address(
            page,
            context,
            candidate,
            title,
            address,
            logger=logger,
        )

        _log_candidate_summary(
            logger,
            index,
            min(
                len(candidates),
                MAX_CANDIDATES_TO_VERIFY,
            ),
            verified,
        )

        if verified.get("page") is not None:
            page = verified["page"]

        # ----------------------------------------------------
        # IMPORTANT:
        # Valid Place URL immediately returns.
        # ----------------------------------------------------

        if (
            verified.get("success")
            and (
                (
                    search_mode == "address"
                    and _result_matches_address(
                        address,
                        verified.get("address", ""),
                    )
                    and _result_matches_title(
                        title,
                        verified.get("title", ""),
                    )
                )
                or (
                    search_mode == "title"
                    and _result_matches_title(
                        title,
                        verified.get("title", ""),
                    )
                )
            )
        ):
            return verified

        if verified.get("success") and logger:
            logger.warning(
                "SEARCH RESULT MISMATCH | mode=%s | input_title=%r | "
                "actual_title=%r | input_address=%r | actual_address=%r | url=%r",
                search_mode,
                title,
                verified.get("title", ""),
                address,
                verified.get("address", ""),
                verified.get("google_maps_url") or verified.get("url"),
            )

        score = (
            verified.get(
                "address_score",
                0.0,
            )
            * 0.70
            + verified.get(
                "title_score",
                0.0,
            )
            * 0.30
        )

        if best_failed is None or score > best_failed.get(
            "_failure_score",
            -1,
        ):
            verified["_failure_score"] = score
            best_failed = verified

    return None


# ============================================================
# GOOGLE MAPS SEARCH ENGINE
# ============================================================


class GoogleMapsSearchEngine:
    def __init__(
        self,
        page,
        context=None,
        logger=None,
    ):
        self.page = page
        self.context = context
        self.logger = logger

    # ========================================================
    # CURRENT PLACE PAGE
    # ========================================================

    def _try_current_place_page(
        self,
        title,
        address,
    ):
        """
        If current browser page is already a Google Maps
        Place URL, ACCEPT IT immediately.

        No search.
        No click.
        No title requirement.
        No address requirement.
        """

        page = self.page

        if page is None:
            return None

        try:
            current_url = get_current_google_maps_url(page)

            if not current_url:
                return None

            if not is_google_maps_place_url(current_url):
                return None

            current_url = clean_google_maps_url(current_url) or current_url

            # ------------------------------------------------
            # Extract optional metadata.
            # ------------------------------------------------

            coordinates = extract_coordinates_from_page(page)

            actual_title = extract_selected_place_title(page) or ""

            actual_address = (
                extract_selected_place_address(
                    page,
                    expected_address=address,
                )
                or ""
            )

            if not actual_address:
                actual_address = (
                    _find_best_address_from_main_text(
                        page,
                        expected_address=address,
                    )
                    or ""
                )

            address_info = address_match_score(
                address,
                actual_address,
            )

            title_score = title_match_score(
                title,
                actual_title,
                actual_address,
            )

            if (
                not _result_matches_address(address, actual_address)
                or not _result_matches_title(title, actual_title)
            ):
                if self.logger:
                    self.logger.warning(
                        "CURRENT MAPS PLACE TITLE/ADDRESS MISMATCH | "
                        "input_title=%r | actual_title=%r | "
                        "input_address=%r | actual_address=%r | url=%r",
                        title,
                        actual_title,
                        address,
                        actual_address,
                        current_url,
                    )

                return None

            # ------------------------------------------------
            # HARD ACCEPT
            # ------------------------------------------------

            if self.logger:
                self.logger.info(
                    "CURRENT MAPS PLACE ACCEPTED | "
                    "title=%r | "
                    "address=%r | "
                    "url=%r | "
                    "coordinates=%r",
                    actual_title,
                    actual_address,
                    current_url,
                    coordinates,
                )

            return {
                "success": True,
                "url": current_url,
                "google_maps_url": current_url,
                "title": actual_title,
                "address": actual_address,
                "title_score": title_score,
                "address_score": address_info.get(
                    "score",
                    0.0,
                ),
                "location_score": address_info.get(
                    "location_score",
                    0.0,
                ),
                "location_matches": address_info.get(
                    "location_matches",
                    0,
                ),
                "tail_location_matches": address_info.get(
                    "tail_location_matches",
                    0,
                ),
                "tail_location_score": address_info.get(
                    "tail_location_score",
                    0.0,
                ),
                "reason": ("CURRENT_MAPS_PLACE_ACCEPTED"),
                "page": page,
                "coordinates": coordinates,
                "matched_tokens": address_info.get(
                    "matched_tokens",
                    [],
                ),
                "score": address_info.get(
                    "score",
                    0.0,
                ),
            }

        except Exception as error:
            if self.logger:
                self.logger.debug(
                    "Current place page check failed: %s",
                    error,
                )

        return None

    # ========================================================
    # UPDATE PAGE
    # ========================================================

    def update_page(self, page):
        if page is not None:
            self.page = page

            try:
                if self.context is None:
                    self.context = page.context
            except Exception:
                pass

    # ========================================================
    # LOG
    # ========================================================

    def _log_info(self, message):
        if self.logger:
            try:
                self.logger.info(message)
            except Exception:
                pass

    def _log_warning(self, message):
        if self.logger:
            try:
                self.logger.warning(message)
            except Exception:
                pass

    def _log_debug(self, message):
        if self.logger:
            try:
                self.logger.debug(message)
            except Exception:
                pass

    # ========================================================
    # RECOVER PAGE
    # ========================================================

    def _recover_page(self):

        if self.context is not None:
            try:
                for page in reversed(self.context.pages):
                    if not page.is_closed():
                        self.page = page

                        return page

            except Exception:
                pass

        return self.page

    # ========================================================
    # SEARCH
    # ========================================================

    def search(
        self,
        title,
        address,
        timeout=None,
    ):
        timeout = timeout or PAGE_TIMEOUT

        title = safe_text(title)
        address = safe_text(address)

        base = {
            "success": False,
            "url": None,
            "google_maps_url": None,
            "title": title,
            "address": address,
            "score": 0.0,
            "title_score": 0.0,
            "address_score": 0.0,
            "coordinates": None,
            "matched_tokens": [],
            "reason": "",
            "attempts": 0,
            "page": self.page,
        }

        # ----------------------------------------------------
        # Input validation
        # ----------------------------------------------------

        if not title:
            base["reason"] = "TITLE_EMPTY"
            return base

        # ----------------------------------------------------
        # Recover page
        # ----------------------------------------------------

        self._recover_page()

        if self.page is None:
            base["reason"] = "PAGE_NONE"
            return base

        if self.context is None:
            try:
                self.context = self.page.context
            except Exception:
                pass

        # ----------------------------------------------------
        # CURRENT PLACE PAGE
        #
        # IMPORTANT:
        # If already a Place URL, ACCEPT immediately.
        # ----------------------------------------------------

        current_result = self._try_current_place_page(
            title,
            address,
        )

        if current_result:
            if self.logger:
                self.logger.info(
                    "CURRENT GOOGLE MAPS PLACE -> FOUND WITHOUT SEARCH | %s",
                    current_result.get(
                        "google_maps_url",
                        "",
                    ),
                )

            return current_result

        # ----------------------------------------------------
        # Build variants
        # ----------------------------------------------------

        variants = build_search_variants(
            title,
            address,
        )

        if not variants:
            base["reason"] = "NO_SEARCH_VARIANTS"
            return base

        self._log_debug("Google Maps search variants: " + " | ".join(variants))

        title_query = _normalize_text(title).casefold()
        address_variants = [
            query
            for query in variants
            if _normalize_text(query).casefold() != title_query
        ]
        # TITLE FIRST:
        # - strong title -> lock the correct Place and learn Maps address
        # ADDRESS RECOVERY SECOND:
        # - strong address -> repair stale/wrong Excel title from Maps
        search_phases = [
            ("title", [title] if title else []),
            ("address", address_variants),
        ]
        best = None

        # ====================================================
        # SEARCH VARIANTS
        # ====================================================

        variant_index = 0

        for search_mode, phase_variants in search_phases:
            for query in phase_variants:
                variant_index += 1
                self._recover_page()

                search_url = _safe_build_search_url(query)

                if not search_url:
                    continue

                self._log_info(
                    f"Google Maps {search_mode} search "
                    f"variant {variant_index}/{len(variants) + 1}: {query}"
                )

                # ------------------------------------------------
                # Navigate
                # ------------------------------------------------

                try:
                    (
                        new_page,
                        success,
                        attempts,
                    ) = safe_goto(
                        self.page,
                        self.context,
                        search_url,
                        logger=self.logger,
                        timeout=timeout,
                    )

                    if new_page is not None:
                        self.update_page(new_page)

                except Exception as error:
                    self._log_warning(
                        "Google Maps navigation error: " + str(error)[:250]
                    )

                    continue

                if not success:
                    self._log_warning(
                        "Google Maps navigation failed "
                        f"variant={variant_index}/"
                        f"{len(variants) + 1} "
                        f"attempts={attempts}"
                    )

                    continue

                # ------------------------------------------------
                # Process page
                # ------------------------------------------------

                try:
                    result = _process_search_page(
                        self.page,
                        self.context,
                        title,
                        address,
                        search_mode=search_mode,
                        logger=self.logger,
                    )

                    if not result:
                        continue

                    result["attempts"] = (
                        result.get(
                            "attempts",
                            0,
                        )
                        + attempts
                    )

                    result["page"] = result.get(
                        "page",
                        self.page,
                    )

                    # ============================================
                    # SUCCESS -> RETURN IMMEDIATELY
                    # ============================================

                    if result.get("success"):
                        # ----------------------------------------
                        # Ensure URL fields are ALWAYS synchronized
                        # ----------------------------------------

                        result["google_maps_url"] = result.get(
                            "google_maps_url"
                        ) or result.get("url")

                        result["url"] = result.get("url") or result.get("google_maps_url")

                        result["search_mode"] = search_mode
                        if search_mode == "title":
                            result["search_reason"] = "TITLE_PRIMARY_SEARCH"

                        self._log_info(
                            "Google Maps SUCCESS: "
                            f"{title} | "
                            f"{result.get('address', '')} | "
                            f"url={result.get('url')} | "
                            f"reason={result.get('reason')}"
                        )

                        # ----------------------------------------
                        # Coordinates are optional.
                        # Try to get them if missing.
                        # ----------------------------------------

                        if not result.get("coordinates"):
                            result["coordinates"] = recover_candidate_coordinates(
                                self.page,
                                self.context,
                                {"url": result.get("url")},
                                logger=self.logger,
                            )

                        return result

                    # ============================================
                    # Failed candidate scoring
                    # ============================================

                    result_address = result.get(
                        "address",
                        "",
                    )

                    result_title = result.get(
                        "title",
                        "",
                    )

                    info = address_match_score(
                        address,
                        result_address,
                    )

                    result["address_score"] = info["score"]

                    result["score"] = info["score"]

                    result["title_score"] = title_match_score(
                        title,
                        result_title,
                        result_address,
                    )

                    result["matched_tokens"] = info["matched_tokens"]

                    combined = info["score"] * 0.75 + result["title_score"] * 0.25

                    if best is None or combined > best.get(
                        "_final_score",
                        -1,
                    ):
                        result["_final_score"] = combined

                        best = result

                except Exception as error:
                    self._log_warning(
                        "Google Maps page processing error: " + str(error)[:250]
                    )

                    continue

        # ====================================================
        # NO SUCCESS
        # ====================================================

        base["page"] = self.page

        if best:
            base.update(
                {
                    "score": best.get(
                        "address_score",
                        0.0,
                    ),
                    "title_score": best.get(
                        "title_score",
                        0.0,
                    ),
                    "address_score": best.get(
                        "address_score",
                        0.0,
                    ),
                    "coordinates": best.get("coordinates"),
                    "matched_tokens": best.get(
                        "matched_tokens",
                        [],
                    ),
                }
            )

        base["reason"] = "NOT_FOUND"

        self._log_warning(
            f"Google Maps NOT_FOUND / NO_VALID_PLACE_URL: {title} | {address}"
        )

        return base


# ============================================================
# PUBLIC FUNCTION
# ============================================================


def search_google_maps(
    page,
    title,
    address,
    context=None,
    logger=None,
    timeout=None,
):
    return GoogleMapsSearchEngine(
        page,
        context=context,
        logger=logger,
    ).search(
        title,
        address,
        timeout=timeout,
    )
