# ============================================================
# app/search.py
# ============================================================

import re
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote, unquote

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

# Verify nhiều hơn một chút để giảm bỏ sót candidate đúng.
MAX_CANDIDATES_TO_VERIFY = 8

# Address.
ADDRESS_MATCH_MIN_SCORE = 0.55
ADDRESS_SOFT_SCORE = 0.30
ADDRESS_GOOD_SCORE = 0.50

# Location.
MIN_LOCATION_MATCHES = 1

# Search variants.
MAX_SEARCH_VARIANTS = 10

# Title.
TITLE_STRONG_SCORE = 0.82
TITLE_VERY_STRONG_SCORE = 0.90
TITLE_MEDIUM_SCORE = 0.68

# Tổng score.
FINAL_SOFT_ACCEPT_SCORE = 0.60


# ============================================================
# GENERIC TOKENS
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


# ============================================================
# ADMIN PREFIXES
# ============================================================

ADMIN_PREFIXES = (
    "phuong ",
    "xa ",
    "thi tran ",
    "quan ",
    "huyen ",
    "thi xa ",
    "thanh pho ",
    "tp ",
    "tinh ",
)


# ============================================================
# COORDINATE REGEX
# ============================================================

COORDINATE_PATTERNS = (
    re.compile(
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
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
        value = str(value)
    except Exception:
        return ""

    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)

    return value


def _remove_accents(value):
    value = _normalize_text(value)

    if not value:
        return ""

    try:
        value = unicodedata.normalize(
            "NFKD",
            value,
        )

        value = "".join(char for char in value if not unicodedata.combining(char))

    except Exception:
        pass

    return value


def _normalize_address(value):
    value = _remove_accents(value)

    if not value:
        return ""

    value = value.replace("&", " va ")

    value = re.sub(
        r"[/|;]+",
        ",",
        value,
    )

    value = re.sub(
        r"[-_]+",
        " ",
        value,
    )

    value = re.sub(
        r"[()\[\]{}]+",
        " ",
        value,
    )

    value = re.sub(
        r"\s*,\s*",
        ",",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = re.sub(
        r",+",
        ",",
        value,
    )

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

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


# ============================================================
# TEXT HELPERS
# ============================================================


def _extract_lines(value):
    if value is None:
        return []

    try:
        text = str(value)
    except Exception:
        return []

    result = []

    for line in text.splitlines():
        line = line.strip()

        if line:
            result.append(line)

    return result


def _unique_texts(values):
    result = []
    seen = set()

    for value in values:
        text = safe_text(value)

        if not text:
            continue

        normalized = _normalize_text(text)

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(text)

    return result


# ============================================================
# ADDRESS TOKENIZATION
# ============================================================


def _clean_address_token(token):
    token = _normalize_address(token)

    if not token:
        return ""

    return token.strip(" ,.-")


def _address_tokens(address):
    normalized = _normalize_address(address)

    if not normalized:
        return []

    raw_tokens = normalized.split(",")

    result = []

    for token in raw_tokens:
        token = _clean_address_token(token)

        if not token:
            continue

        if token in GENERIC_ADDRESS_TOKENS:
            continue

        if len(token) < 2:
            continue

        result.append(token)

    return result


def _expand_address_token(token):
    token = _normalize_address(token)

    if not token:
        return []

    variants = [token]

    changed = True

    while changed:
        changed = False

        for current in list(variants):
            for prefix in ADMIN_PREFIXES:
                if current.startswith(prefix):
                    stripped = current[len(prefix) :].strip()

                    if stripped and stripped not in variants:
                        variants.append(stripped)
                        changed = True

    return variants


# ============================================================
# ADDRESS SYNONYMS
# ============================================================

ADDRESS_SYNONYMS = {
    "bai": "bai",
    "bai dai": "bai dai",
    "bai dai beach": "bai dai",
    "beach": "beach",
    "quy nhon": "quy nhon",
    "quy nhon city": "quy nhon",
    "tp quy nhon": "quy nhon",
    "thanh pho quy nhon": "quy nhon",
    "quy nhon nam": "quy nhon",
    "quy nhon south": "quy nhon",
    "gia lai": "gia lai",
    "binh dinh": "binh dinh",
    "da nang": "da nang",
    "ho chi minh": "ho chi minh",
    "ha noi": "ha noi",
}


def _canonical_address_token(token):
    token = _normalize_address(token)

    if not token:
        return ""

    for prefix in ADMIN_PREFIXES:
        if token.startswith(prefix):
            token = token[len(prefix) :].strip()

    return ADDRESS_SYNONYMS.get(
        token,
        token,
    )


# ============================================================
# TOKEN MATCH
# ============================================================


def _token_matches_address(
    token,
    actual_address,
):
    actual = _normalize_address(actual_address)

    if not actual:
        return False

    variants = _expand_address_token(token)

    canonical = _canonical_address_token(token)

    if canonical:
        variants.append(canonical)

    # Exact substring.
    for variant in variants:
        if not variant:
            continue

        if variant in actual:
            return True

    # Word-level matching.
    canonical_words = [word for word in canonical.split() if len(word) >= 3]

    if canonical_words:
        matched_words = sum(1 for word in canonical_words if word in actual)

        if matched_words >= len(canonical_words):
            return True

        # Nếu token nhiều từ, chỉ cần phần lớn match.
        if len(canonical_words) >= 2 and matched_words / len(canonical_words) >= 0.5:
            return True

    return False


# ============================================================
# LOCATION EXTRACTION
# ============================================================


def _extract_location_parts(address):
    try:
        address = str(address)
    except Exception:
        return []

    parts = [part.strip() for part in address.split(",") if part.strip()]

    result = []

    for part in parts:
        normalized = _normalize_address(part)

        if not normalized:
            continue

        if normalized in {
            "vietnam",
            "viet nam",
            "vn",
        }:
            continue

        result.append(part)

    return result


def _canonical_location_parts(address):
    """
    Trả về các location canonical.

    Ví dụ:

        Bãi Dài,
        Quy Nhơn Nam,
        Gia Lai,
        Vietnam

    ->

        bai dai
        quy nhon
        gia lai
    """

    parts = _extract_location_parts(address)

    result = []

    for part in parts:
        canonical = _canonical_address_token(part)

        if canonical:
            result.append(canonical)

    return result


# ============================================================
# LOCATION SIMILARITY
# ============================================================


def _location_match_info(
    input_address,
    actual_address,
):
    input_locations = _canonical_location_parts(input_address)

    actual_locations = _canonical_location_parts(actual_address)

    if not input_locations or not actual_locations:
        return {
            "matches": [],
            "count": 0,
            "score": 0.0,
        }

    matches = []

    for input_location in input_locations:
        best = 0.0

        for actual_location in actual_locations:
            if input_location == actual_location:
                best = 1.0
                break

            if input_location in actual_location or actual_location in input_location:
                best = max(best, 0.85)
                continue

            sequence = SequenceMatcher(
                None,
                input_location,
                actual_location,
            ).ratio()

            if sequence >= 0.75:
                best = max(best, sequence)

        if best >= 0.70:
            matches.append(
                (
                    input_location,
                    round(best, 4),
                )
            )

    count = len(matches)

    score = count / len(input_locations) if input_locations else 0.0

    return {
        "matches": matches,
        "count": count,
        "score": round(score, 4),
    }


# ============================================================
# ADDRESS MATCH SCORE
# ============================================================


def address_match_score(
    input_address,
    actual_address,
):
    input_normalized = _normalize_address(input_address)

    actual_normalized = _normalize_address(actual_address)

    if not input_normalized or not actual_normalized:
        return {
            "score": 0.0,
            "matched_tokens": [],
            "total_tokens": 0,
            "location_matches": 0,
            "location_score": 0.0,
            "strong_match": False,
        }

    # Exact.
    if input_normalized == actual_normalized:
        tokens = _address_tokens(input_address)

        return {
            "score": 1.0,
            "matched_tokens": tokens,
            "total_tokens": len(tokens),
            "location_matches": len(tokens),
            "location_score": 1.0,
            "strong_match": True,
        }

    tokens = _address_tokens(input_address)

    if not tokens:
        return {
            "score": 0.0,
            "matched_tokens": [],
            "total_tokens": 0,
            "location_matches": 0,
            "location_score": 0.0,
            "strong_match": False,
        }

    matched_tokens = []

    for token in tokens:
        if _token_matches_address(
            token,
            actual_address,
        ):
            matched_tokens.append(token)

    token_score = len(matched_tokens) / len(tokens) if tokens else 0.0

    location_info = _location_match_info(
        input_address,
        actual_address,
    )

    location_matches = location_info.get(
        "count",
        0,
    )

    location_score = location_info.get(
        "score",
        0.0,
    )

    substring_bonus = 0.0

    if len(input_normalized) >= 10 and input_normalized in actual_normalized:
        substring_bonus = 0.15

    # Location là signal quan trọng hơn token generic.
    final_score = token_score * 0.60 + location_score * 0.40

    final_score += substring_bonus

    final_score = min(
        1.0,
        final_score,
    )

    strong_match = False

    # Address gần như giống hoàn toàn.
    if final_score >= 0.80:
        strong_match = True

    # Đủ address + location.
    elif (
        final_score >= ADDRESS_MATCH_MIN_SCORE
        and location_matches >= MIN_LOCATION_MATCHES
    ):
        strong_match = True

    # Address ngắn.
    elif (
        len(tokens) <= 2
        and len(matched_tokens) >= 1
        and final_score >= ADDRESS_SOFT_SCORE
    ):
        strong_match = True

    # Location rất mạnh.
    elif location_score >= 0.75 and len(matched_tokens) >= 1:
        strong_match = True

    return {
        "score": round(
            final_score,
            4,
        ),
        "matched_tokens": matched_tokens,
        "total_tokens": len(tokens),
        "location_matches": location_matches,
        "location_score": round(
            location_score,
            4,
        ),
        "strong_match": strong_match,
    }


def address_is_related(
    input_address,
    actual_address,
):
    result = address_match_score(
        input_address,
        actual_address,
    )

    return bool(
        result.get(
            "strong_match",
            False,
        )
    )


# ============================================================
# TITLE MATCH SCORE
# ============================================================


def title_match_score(
    input_title,
    actual_title,
    actual_text="",
):
    input_norm = _normalize_title(input_title)

    actual_norm = _normalize_title(actual_title)

    combined_norm = _normalize_title(actual_text)

    if not input_norm:
        return 0.0

    if actual_norm and input_norm == actual_norm:
        return 1.0

    if actual_norm and input_norm in actual_norm:
        return 0.95

    if actual_norm and actual_norm in input_norm:
        return 0.90

    sequence_score = 0.0

    if actual_norm:
        sequence_score = SequenceMatcher(
            None,
            input_norm,
            actual_norm,
        ).ratio()

    input_tokens = {token for token in input_norm.split() if len(token) >= 3}

    actual_tokens = {token for token in combined_norm.split() if len(token) >= 3}

    token_score = 0.0

    if input_tokens:
        token_score = len(input_tokens & actual_tokens) / len(input_tokens)

    return round(
        max(
            sequence_score,
            token_score,
        ),
        4,
    )


# ============================================================
# COORDINATE HELPERS
# ============================================================


def _valid_lat_lng(
    lat,
    lng,
):
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

        if not match:
            continue

        lat = match.group(1)
        lng = match.group(2)

        if _valid_lat_lng(lat, lng):
            return (
                float(lat),
                float(lng),
            )

    return None


def extract_coordinates_from_page(page):
    if page is None:
        return None

    # Current URL.
    try:
        current_url = page.url or ""

        coordinates = _extract_coordinates_from_text(current_url)

        if coordinates:
            return coordinates

    except Exception:
        pass

    # Links.
    try:
        links = page.locator("a[href]")

        count = min(
            links.count(),
            COORDINATE_SCAN_MAX_LINKS,
        )

        for index in range(count):
            try:
                href = links.nth(index).get_attribute("href")

                coordinates = _extract_coordinates_from_text(href)

                if coordinates:
                    return coordinates

            except Exception:
                continue

    except Exception:
        pass

    # Data attributes.
    try:
        elements = page.locator("[data-url], [data-href]")

        count = min(
            elements.count(),
            COORDINATE_SCAN_MAX_DATA_ELEMENTS,
        )

        for index in range(count):
            try:
                element = elements.nth(index)

                for attr in (
                    "data-url",
                    "data-href",
                ):
                    value = element.get_attribute(attr)

                    coordinates = _extract_coordinates_from_text(value)

                    if coordinates:
                        return coordinates

            except Exception:
                continue

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
        value = str(url).strip().lower()
    except Exception:
        return False

    return "google.com/maps" in value or "maps.google.com" in value


def _is_google_maps_place_url(url):
    if not url:
        return False

    try:
        value = str(url).strip().lower()
    except Exception:
        return False

    if not _is_google_maps_url(value):
        return False

    return "/maps/place/" in value or "/maps/place?" in value


def _is_usable_google_maps_place_url(url):
    """
    Chỉ cần URL là Google Maps place URL hợp lệ
    thì có thể sử dụng làm kết quả.

    Không yêu cầu title/address match.
    """

    if not url:
        return False

    try:
        url = str(url).strip()
    except Exception:
        return False

    if not url:
        return False

    return _is_google_maps_place_url(url)


def _clean_place_url(url):
    if not url:
        return None

    try:
        url = str(url).strip()
    except Exception:
        return None

    if not url:
        return None

    try:
        cleaned = clean_google_maps_url(url)

        if cleaned and _is_google_maps_place_url(cleaned):
            return cleaned

    except Exception:
        pass

    if _is_google_maps_place_url(url):
        return url

    return None


def get_current_google_maps_url(page):
    if page is None:
        return None

    try:
        url = page.url or ""

        return _clean_place_url(url)

    except Exception:
        return None


def extract_google_maps_url_from_href(href):
    if not href:
        return None

    try:
        href = str(href).strip()
    except Exception:
        return None

    if not href:
        return None

    href = href.replace("&amp;", "&").replace("\\u003d", "=").replace("\\u0026", "&")

    if href.startswith("/maps/"):
        href = "https://www.google.com" + href

    if "\\/" in href:
        href = href.replace(
            "\\/",
            "/",
        )

    # Decode once nếu URL bị encode.
    try:
        decoded = unquote(href)

        if _is_google_maps_place_url(decoded) and len(decoded) < len(href) + 500:
            href = decoded

    except Exception:
        pass

    try:
        cleaned = clean_google_maps_url(href)

        if cleaned and _is_google_maps_place_url(cleaned):
            return cleaned

    except Exception:
        pass

    return _clean_place_url(href)


# ============================================================
# POPUP HANDLING
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
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = locator.count()

            if count <= 0:
                continue

            button = locator.first

            if button.is_visible(timeout=500):
                button.click(timeout=1500)

                time.sleep(0.2)

                break

        except Exception:
            continue


# ============================================================
# SEARCH RESULT WAITING
# ============================================================


def wait_for_search_results(
    page,
    timeout=None,
):
    if page is None:
        return False

    timeout = timeout or PAGE_TIMEOUT

    deadline = time.time() + timeout / 1000.0

    selectors = [
        '[role="main"]',
        'div[role="feed"]',
        "div.Nv2PK",
        "div.bfdHYd",
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
                locator = page.locator(selector)

                if locator.count() > 0:
                    return True

            except Exception:
                continue

        time.sleep(
            max(
                float(SEARCH_POLL_INTERVAL or 0.15),
                0.1,
            )
        )

    return False


# ============================================================
# LOCATOR TEXT
# ============================================================


def _read_locator_text(locator):
    if locator is None:
        return ""

    try:
        text = locator.inner_text(timeout=1500)

        return safe_text(text)

    except Exception:
        pass

    try:
        text = locator.text_content(timeout=1500)

        return safe_text(text)

    except Exception:
        return ""


# ============================================================
# SELECTED PLACE TITLE
# ============================================================


def extract_selected_place_title(
    page,
    expected_title=None,
):
    if page is None:
        return ""

    selectors = [
        "h1",
        '[data-item-id="title"]',
        '[role="main"] h1',
    ]

    values = []

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = locator.count()

            if count <= 0:
                continue

            for index in range(min(count, 5)):
                text = _read_locator_text(locator.nth(index))

                if text:
                    values.append(text)

        except Exception:
            continue

    values = _unique_texts(values)

    if not values:
        return ""

    if expected_title:
        return max(
            values,
            key=lambda value: title_match_score(
                expected_title,
                value,
            ),
        )

    return values[0]


# ============================================================
# SELECTED PLACE ADDRESS
# ============================================================


def extract_selected_place_address(page):
    if page is None:
        return ""

    selectors = [
        '[data-item-id="address"]',
        '[aria-label^="Address:"]',
        '[aria-label*="Địa chỉ"]',
        '[data-tooltip*="address"]',
        '[data-item-id*="address"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = locator.count()

            if count <= 0:
                continue

            for index in range(min(count, 5)):
                element = locator.nth(index)

                text = _read_locator_text(element)

                if text:
                    return text

                try:
                    aria = safe_text(element.get_attribute("aria-label"))

                    if aria:
                        aria = re.sub(
                            r"^(Address|Địa chỉ)\s*:\s*",
                            "",
                            aria,
                            flags=re.IGNORECASE,
                        )

                        if aria:
                            return aria

                except Exception:
                    pass

        except Exception:
            continue

    # Fallback aria.
    try:
        main = page.locator('[role="main"]').first

        if main.count() > 0:
            elements = main.locator("[aria-label]")

            count = min(
                elements.count(),
                250,
            )

            for index in range(count):
                try:
                    aria = safe_text(elements.nth(index).get_attribute("aria-label"))

                    if not aria:
                        continue

                    if re.search(
                        r"^(Address|Địa chỉ)\s*:",
                        aria,
                        flags=re.IGNORECASE,
                    ):
                        return re.sub(
                            r"^(Address|Địa chỉ)\s*:\s*",
                            "",
                            aria,
                            flags=re.IGNORECASE,
                        )

                except Exception:
                    continue

    except Exception:
        pass

    return ""


# ============================================================
# PLACE URL EXTRACTION
# ============================================================


def extract_place_url_from_page(page):
    if page is None:
        return None

    # 1. Current URL.
    current = get_current_google_maps_url(page)

    if current:
        return current

    # 2. href.
    try:
        links = page.locator("a[href]")

        count = min(
            links.count(),
            PLACE_URL_SCAN_MAX_LINKS,
        )

        for index in range(count):
            try:
                href = links.nth(index).get_attribute("href")

                url = extract_google_maps_url_from_href(href)

                if url:
                    return url

            except Exception:
                continue

    except Exception:
        pass

    # 3. data attributes.
    try:
        elements = page.locator("[data-url], [data-href]")

        count = min(
            elements.count(),
            PLACE_URL_SCAN_MAX_DATA_ELEMENTS,
        )

        for index in range(count):
            try:
                element = elements.nth(index)

                for attr in (
                    "data-url",
                    "data-href",
                ):
                    value = element.get_attribute(attr)

                    url = extract_google_maps_url_from_href(value)

                    if url:
                        return url

            except Exception:
                continue

    except Exception:
        pass

    # 4. HTML.
    try:
        html = page.locator("body").inner_html(timeout=3000)

        if html:
            patterns = (
                r'https?://www\.google\.com/maps/place/[^"\'>\s]+',
                r'https?://google\.com/maps/place/[^"\'>\s]+',
                r'/maps/place/[^"\'>\s]+',
            )

            for pattern in patterns:
                matches = re.findall(
                    pattern,
                    html,
                    flags=re.IGNORECASE,
                )

                for match in matches:
                    if match.startswith("/"):
                        match = "https://www.google.com" + match

                    match = (
                        match.replace(
                            "&amp;",
                            "&",
                        )
                        .replace(
                            "\\u003d",
                            "=",
                        )
                        .replace(
                            "\\u0026",
                            "&",
                        )
                    )

                    url = extract_google_maps_url_from_href(match)

                    if url:
                        return url

    except Exception:
        pass

    return None


# ============================================================
# RESULT CARD TEXT
# ============================================================


def _extract_card_texts(card):
    values = []

    try:
        text = card.inner_text(timeout=1500)

        if text:
            values.append(text)

    except Exception:
        pass

    try:
        aria = card.get_attribute("aria-label")

        if aria:
            values.append(aria)

    except Exception:
        pass

    return _unique_texts(values)


def _extract_title_from_card_texts(texts):
    if not texts:
        return ""

    lines = _extract_lines(texts[0])

    if lines:
        return lines[0]

    return texts[0]


# ============================================================
# RESULT CANDIDATE TEXT SCORE
# ============================================================


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

    title = candidate.get(
        "title",
        "",
    )

    title_score = title_match_score(
        input_title,
        title,
        combined,
    )

    address_info = address_match_score(
        input_address,
        combined,
    )

    address_score = address_info.get(
        "score",
        0.0,
    )

    location_score = address_info.get(
        "location_score",
        0.0,
    )

    # Title ưu tiên hơn address.
    #
    # title 55%
    # address 30%
    # location 15%
    final_score = title_score * 55 + address_score * 30 + location_score * 15

    # Nếu title cực mạnh thì cộng thêm.
    if title_score >= 0.90:
        final_score += 10

    return final_score


# ============================================================
# RESULT CARD EXTRACTION
# ============================================================


def extract_result_candidates(page):
    if page is None:
        return []

    candidates = []
    seen_urls = set()

    selectors = [
        "div.Nv2PK",
        "div.bfdHYd",
        '[role="article"]',
    ]

    cards = []

    for selector in selectors:
        try:
            locator = page.locator(selector)

            count = min(
                locator.count(),
                RESULT_CARD_MAX,
            )

            if count <= 0:
                continue

            for index in range(count):
                try:
                    cards.append(locator.nth(index))
                except Exception:
                    continue

            if cards:
                break

        except Exception:
            continue

    # Card extraction.
    for card in cards:
        try:
            texts = _extract_card_texts(card)

            links = card.locator("a[href]")

            link_count = min(
                links.count(),
                CARD_LINK_MAX,
            )

            url = None

            for index in range(link_count):
                try:
                    href = links.nth(index).get_attribute("href")

                    candidate_url = extract_google_maps_url_from_href(href)

                    if candidate_url:
                        url = candidate_url
                        break

                except Exception:
                    continue

            if not url:
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            title = _extract_title_from_card_texts(texts)

            candidates.append(
                {
                    "url": url,
                    "title": title,
                    "texts": texts,
                }
            )

        except Exception:
            continue

    # ========================================================
    # FALLBACK: ALL PLACE LINKS
    # ========================================================

    if len(candidates) < RESULT_CARD_MAX:
        try:
            links = page.locator('a[href*="/maps/place/"]')

            count = min(
                links.count(),
                PLACE_URL_SCAN_MAX_LINKS,
            )

            for index in range(count):
                try:
                    link = links.nth(index)

                    href = link.get_attribute("href")

                    url = extract_google_maps_url_from_href(href)

                    if not url:
                        continue

                    if url in seen_urls:
                        continue

                    text_values = []

                    try:
                        parent = link.locator("xpath=..")

                        parent_text = _read_locator_text(parent)

                        if parent_text:
                            text_values.append(parent_text)

                    except Exception:
                        pass

                    try:
                        link_text = _read_locator_text(link)

                        if link_text:
                            text_values.append(link_text)

                    except Exception:
                        pass

                    texts = _unique_texts(text_values)

                    title = _extract_title_from_card_texts(texts)

                    seen_urls.add(url)

                    candidates.append(
                        {
                            "url": url,
                            "title": title,
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
# CANDIDATE OPENING
# ============================================================


def click_candidate(
    page,
    candidate,
    context=None,
    logger=None,
):
    if page is None:
        return page, False, 0

    if not candidate:
        return page, False, 0

    url = candidate.get("url")

    if not url:
        return page, False, 0

    if context is None:
        try:
            context = page.context
        except Exception:
            context = None

    # No context.
    if context is None:
        try:
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

    try:
        result = safe_goto(
            page,
            context,
            url,
            logger=logger,
            timeout=PAGE_TIMEOUT,
        )

        new_page, success, attempt = result

        return (
            new_page,
            success,
            attempt,
        )

    except Exception as error:
        if logger:
            logger.warning("safe_goto candidate error: " + str(error)[:250])

        return (
            page,
            False,
            1,
        )


# ============================================================
# FALLBACK ADDRESS SCAN
# ============================================================


def _find_best_address_from_main_text(
    page,
    input_address,
):
    if page is None:
        return ""

    try:
        main = page.locator('[role="main"]').first

        if main.count() <= 0:
            return ""

        main_text = _read_locator_text(main)

        if not main_text:
            return ""

        lines = _extract_lines(main_text)

        best_line = ""
        best_score = 0.0

        for line in lines:
            line = safe_text(line)

            if len(line) < 5:
                continue

            normalized_line = _normalize_address(line)

            if not normalized_line:
                continue

            if normalized_line.startswith(
                (
                    "website",
                    "phone",
                    "dien thoai",
                    "hours",
                    "gio mo cua",
                    "reviews",
                    "rating",
                    "photos",
                    "website:",
                    "phone:",
                )
            ):
                continue

            score_info = address_match_score(
                input_address,
                line,
            )

            score = score_info.get(
                "score",
                0.0,
            )

            if score > best_score:
                best_score = score
                best_line = line

        if best_line and best_score >= 0.25:
            return best_line

    except Exception:
        pass

    return ""


# ============================================================
# CANDIDATE DECISION
# ============================================================


def _decide_candidate(
    input_title,
    input_address,
    actual_title,
    actual_address,
    actual_url,
):
    """
    Quyết định Google Maps candidate.

    PRINCIPLE:

    Nếu đã có Google Maps /maps/place/ URL
    thì coi là usable.

    Title/address chỉ dùng để logging / scoring,
    KHÔNG được phép làm candidate bị fail
    nếu URL Maps hợp lệ.
    """

    address_info = address_match_score(
        input_address,
        actual_address,
    )

    address_score = address_info.get(
        "score",
        0.0,
    )

    location_matches = address_info.get(
        "location_matches",
        0,
    )

    location_score = address_info.get(
        "location_score",
        0.0,
    )

    strong_address = address_info.get(
        "strong_match",
        False,
    )

    title_score = title_match_score(
        input_title,
        actual_title,
        actual_address,
    )

    valid_place_url = _is_usable_google_maps_place_url(actual_url)

    # ========================================================
    # MAIN RULE
    # ========================================================
    #
    # Có Google Maps place URL
    # => SUCCESS
    #
    # Không bắt title/address nữa.
    # ========================================================

    if valid_place_url:
        return {
            "success": True,
            "reason": "GOOGLE_MAPS_PLACE_URL",
            "title_score": title_score,
            "address_score": address_score,
            "location_matches": location_matches,
            "location_score": location_score,
            "strong_address": strong_address,
        }

    # ========================================================
    # FAIL
    # ========================================================

    return {
        "success": False,
        "reason": "INVALID_PLACE_URL",
        "title_score": title_score,
        "address_score": address_score,
        "location_matches": location_matches,
        "location_score": location_score,
        "strong_address": strong_address,
    }


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
    result_base = {
        "success": False,
        "url": None,
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
        result_base["reason"] = "PAGE_NONE"
        return result_base

    if not candidate:
        result_base["reason"] = "CANDIDATE_NONE"
        return result_base

    candidate_url = candidate.get("url")

    if not candidate_url:
        result_base["reason"] = "CANDIDATE_URL_EMPTY"
        return result_base

    (
        current_page,
        success,
        attempt,
    ) = click_candidate(
        page,
        candidate,
        context=context,
        logger=logger,
    )

    if current_page is None:
        result_base["reason"] = "PAGE_NONE_AFTER_NAVIGATION"
        return result_base

    if not success:
        result_base["reason"] = "NAVIGATION_FAILED"
        return result_base

    # Wait.
    wait_for_search_results(
        current_page,
        timeout=PAGE_TIMEOUT,
    )

    close_google_popups(current_page)

    if FINAL_SEARCH_CHECK_DELAY:
        time.sleep(FINAL_SEARCH_CHECK_DELAY)

    # ========================================================
    # URL
    # ========================================================

    actual_url = extract_place_url_from_page(current_page)

    if not actual_url:
        for retry in range(
            1,
            SELECTED_PLACE_RETRIES + 1,
        ):
            try:
                time.sleep(SELECTED_PLACE_RETRY_DELAY)

                actual_url = extract_place_url_from_page(current_page)

                if actual_url:
                    break

            except Exception:
                continue

    # ========================================================
    # TITLE
    # ========================================================

    selected_title = extract_selected_place_title(
        current_page,
        expected_title=input_title,
    )

    # ========================================================
    # ADDRESS
    # ========================================================

    selected_address = extract_selected_place_address(current_page)

    if not selected_address:
        selected_address = _find_best_address_from_main_text(
            current_page,
            input_address,
        )

    # ========================================================
    # SCORE
    # ========================================================

    address_info = address_match_score(
        input_address,
        selected_address,
    )

    address_score = address_info.get(
        "score",
        0.0,
    )

    matched_tokens = address_info.get(
        "matched_tokens",
        [],
    )

    coordinates = extract_coordinates_from_page(current_page)

    # ========================================================
    # DECISION
    # ========================================================

    decision = _decide_candidate(
        input_title=input_title,
        input_address=input_address,
        actual_title=selected_title,
        actual_address=selected_address,
        actual_url=actual_url,
    )

    title_score = decision.get(
        "title_score",
        0.0,
    )

    success_decision = decision.get(
        "success",
        False,
    )

    reason = decision.get(
        "reason",
        "",
    )

    # ========================================================
    # DEBUG
    # ========================================================

    if logger:
        try:
            logger.debug(
                "Google Maps candidate result | "
                f"title={selected_title!r} | "
                f"address={selected_address!r} | "
                f"title_score={title_score:.3f} | "
                f"address_score={address_score:.3f} | "
                f"location_matches="
                f"{address_info.get('location_matches', 0)} | "
                f"location_score="
                f"{address_info.get('location_score', 0.0):.3f} | "
                f"url={actual_url!r} | "
                f"decision={success_decision} | "
                f"reason={reason}"
            )

        except Exception:
            pass

    result = {
        "success": bool(success_decision),
        "url": actual_url,
        "title": selected_title,
        "address": selected_address,
        "score": address_score,
        "address_score": address_score,
        "title_score": title_score,
        "matched_tokens": matched_tokens,
        "coordinates": coordinates,
        "reason": reason,
    }

    return result


# ============================================================
# COORDINATE RECOVERY
# ============================================================


def recover_candidate_coordinates(
    page,
    context,
    candidate,
    logger=None,
):
    if page is None:
        return None

    if not candidate:
        return None

    for attempt in range(
        1,
        SELECTED_PLACE_RETRIES + 1,
    ):
        try:
            (
                current_page,
                success,
                _,
            ) = click_candidate(
                page,
                candidate,
                context=context,
                logger=logger,
            )

            page = current_page

            if not success:
                continue

            wait_for_search_results(
                page,
                timeout=PAGE_TIMEOUT,
            )

            coordinates = extract_coordinates_from_page(page)

            if coordinates:
                return coordinates

        except Exception as error:
            if logger:
                logger.warning(
                    "Coordinate recovery error "
                    f"attempt={attempt}/"
                    f"{SELECTED_PLACE_RETRIES}: "
                    f"{str(error)[:200]}"
                )

        if attempt < SELECTED_PLACE_RETRIES:
            time.sleep(SELECTED_PLACE_RETRY_DELAY)

    return None


# ============================================================
# BUILD SEARCH URL
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
# IMPORTANT LOCATION PARTS
# ============================================================


def _get_important_location_parts(address):
    parts = _extract_location_parts(address)

    if not parts:
        return []

    values = []

    # Full location parts.
    for part in parts:
        normalized = _normalize_address(part)

        if normalized:
            values.append(part)

    # Last 1.
    if len(parts) >= 1:
        values.append(", ".join(parts[-1:]))

    # Last 2.
    if len(parts) >= 2:
        values.append(", ".join(parts[-2:]))

    # Last 3.
    if len(parts) >= 3:
        values.append(", ".join(parts[-3:]))

    # First + last.
    if len(parts) >= 3:
        values.append(f"{parts[0]}, {parts[-1]}")

    # First + last 2.
    if len(parts) >= 3:
        values.append(f"{parts[0]}, {parts[-2]}, {parts[-1]}")

    return _unique_texts(values)


# ============================================================
# SEARCH VARIANTS
# ============================================================


def build_search_variants(
    title,
    address,
):
    """
    Search nhiều tầng.

    Ưu tiên:

    1. title + full address
    2. title + last 2 location
    3. title + last 3 location
    4. title + first/last location
    5. title + important location
    6. address + title
    7. title
    8. address
    9. title + city
    10. title + province
    """

    title = safe_text(title)
    address = safe_text(address)

    variants = []

    def add(query):
        query = safe_text(query)

        if not query:
            return

        normalized = _normalize_text(query)

        if not normalized:
            return

        if normalized not in {_normalize_text(item) for item in variants}:
            variants.append(query)

    location_parts = _extract_location_parts(address)

    # --------------------------------------------------------
    # 1. Full.
    # --------------------------------------------------------

    if title and address:
        add(f"{title}, {address}")

    # --------------------------------------------------------
    # 2. Title + last 2.
    # --------------------------------------------------------

    if title and len(location_parts) >= 2:
        add(f"{title}, {', '.join(location_parts[-2:])}")

    # --------------------------------------------------------
    # 3. Title + last 3.
    # --------------------------------------------------------

    if title and len(location_parts) >= 3:
        add(f"{title}, {', '.join(location_parts[-3:])}")

    # --------------------------------------------------------
    # 4. Title + first + last.
    # --------------------------------------------------------

    if title and len(location_parts) >= 3:
        add(f"{title}, {location_parts[0]}, {location_parts[-1]}")

    # --------------------------------------------------------
    # 5. Title + each important location.
    # --------------------------------------------------------

    if title:
        for location in _get_important_location_parts(address):
            if location:
                add(f"{title}, {location}")

    # --------------------------------------------------------
    # 6. Address + title.
    # --------------------------------------------------------

    if title and address:
        add(f"{address}, {title}")

    # --------------------------------------------------------
    # 7. Title only.
    # --------------------------------------------------------

    if title:
        add(title)

    # --------------------------------------------------------
    # 8. Address only.
    # --------------------------------------------------------

    if address:
        add(address)

    # --------------------------------------------------------
    # 9. Title + city/location.
    # --------------------------------------------------------

    if title:
        canonical_locations = _canonical_location_parts(address)

        for location in canonical_locations:
            if location in {
                "vietnam",
                "viet nam",
                "vn",
            }:
                continue

            add(f"{title}, {location}")

    # --------------------------------------------------------
    # Return.
    # --------------------------------------------------------

    return variants[:MAX_SEARCH_VARIANTS]


# ============================================================
# LOG CANDIDATES
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
        logger.debug("==================================================")

        logger.debug(f"Google Maps Candidate {index}/{total}")

        logger.debug(f"Title       : {verified.get('title', '')}")

        logger.debug(f"Address     : {verified.get('address', '')}")

        logger.debug(f"Title score : {verified.get('title_score', 0.0):.3f}")

        logger.debug(f"Addr score  : {verified.get('address_score', 0.0):.3f}")

        logger.debug(f"URL         : {verified.get('url', '')}")

        logger.debug(f"Decision    : {verified.get('success', False)}")

        logger.debug(f"Reason      : {verified.get('reason', '')}")

        logger.debug("==================================================")

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
    logger=None,
):
    if page is None:
        return None

    close_google_popups(page)

    wait_for_search_results(
        page,
        timeout=PAGE_TIMEOUT,
    )

    if FINAL_SEARCH_CHECK_DELAY:
        time.sleep(FINAL_SEARCH_CHECK_DELAY)

    # ========================================================
    # Redirect trực tiếp vào place.
    # ========================================================

    current_url = get_current_google_maps_url(page)

    if current_url:
        current_candidate = {
            "url": current_url,
            "title": "",
            "texts": [],
        }

        verified = verify_candidate_address(
            page,
            context,
            current_candidate,
            title,
            address,
            logger=logger,
        )

        if verified.get(
            "success",
            False,
        ):
            return verified

    # ========================================================
    # Extract candidates.
    # ========================================================

    candidates = extract_result_candidates(page)

    if logger:
        try:
            logger.debug(f"Google Maps extracted {len(candidates)} candidates")
        except Exception:
            pass

    # ========================================================
    # No cards.
    # ========================================================

    if not candidates:
        fallback_url = extract_place_url_from_page(page)

        if fallback_url:
            fallback_candidate = {
                "url": fallback_url,
                "title": "",
                "texts": [],
            }

            verified = verify_candidate_address(
                page,
                context,
                fallback_candidate,
                title,
                address,
                logger=logger,
            )

            if verified.get(
                "success",
                False,
            ):
                return verified

        return None

    # ========================================================
    # Rank.
    # ========================================================

    try:
        for candidate in candidates:
            candidate["_rank_score"] = _candidate_text_score(
                candidate,
                title,
                address,
            )

        candidates.sort(
            key=lambda item: item.get(
                "_rank_score",
                0.0,
            ),
            reverse=True,
        )

    except Exception:
        pass

    # ========================================================
    # Verify top candidates.
    # ========================================================

    candidates = candidates[:MAX_CANDIDATES_TO_VERIFY]

    if logger:
        for index, candidate in enumerate(
            candidates,
            start=1,
        ):
            try:
                logger.debug(
                    "Ranked candidate "
                    f"{index}/{len(candidates)} | "
                    f"text_score="
                    f"{candidate.get('_rank_score', 0):.2f} | "
                    f"title="
                    f"{candidate.get('title', '')!r} | "
                    f"url="
                    f"{candidate.get('url', '')}"
                )

            except Exception:
                continue

    # ========================================================
    # Verify.
    # ========================================================

    best_failed = None

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):
        try:
            if logger:
                logger.debug(
                    "Verify Google Maps candidate "
                    f"{index}/{len(candidates)} | "
                    f"{candidate.get('title', '')!r} | "
                    f"{candidate.get('url', '')}"
                )

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
                len(candidates),
                verified,
            )

            # =================================================
            # Recover latest page.
            # =================================================

            try:
                if context is not None:
                    pages = context.pages

                    if pages:
                        for possible_page in reversed(pages):
                            try:
                                if not possible_page.is_closed():
                                    page = possible_page
                                    break

                            except Exception:
                                continue

            except Exception:
                pass

            # =================================================
            # Success.
            # =================================================

            if verified.get(
                "success",
                False,
            ):
                return verified

            # =================================================
            # Best failed.
            # =================================================

            verified_score = (
                verified.get(
                    "title_score",
                    0.0,
                )
                * 0.60
                + verified.get(
                    "address_score",
                    0.0,
                )
                * 0.40
            )

            if best_failed is None:
                best_failed = verified
                best_failed["_failure_score"] = verified_score

            elif verified_score > best_failed.get(
                "_failure_score",
                0.0,
            ):
                best_failed = verified
                best_failed["_failure_score"] = verified_score

        except Exception as error:
            if logger:
                logger.warning(
                    "Candidate verification error "
                    f"{index}/{len(candidates)}: "
                    f"{str(error)[:250]}"
                )

    # ========================================================
    # Debug best failed.
    # ========================================================

    if logger and best_failed:
        logger.debug(
            "Best failed Google Maps candidate: "
            f"title="
            f"{best_failed.get('title', '')!r} "
            f"address="
            f"{best_failed.get('address', '')!r} "
            f"title_score="
            f"{best_failed.get('title_score', 0.0):.3f} "
            f"address_score="
            f"{best_failed.get('address_score', 0.0):.3f} "
            f"url="
            f"{best_failed.get('url', '')!r} "
            f"reason="
            f"{best_failed.get('reason', '')}"
        )

    return None


# ============================================================
# GOOGLE MAPS SEARCH ENGINE
# ============================================================


class GoogleMapsSearchEngine:
    """
    Google Maps search engine.

    Contract:

        search(title, address)
            ->
        dict result
    """

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
    # UPDATE PAGE
    # ========================================================

    def update_page(
        self,
        page,
    ):
        if page is None:
            return

        self.page = page

        try:
            if self.context is None:
                self.context = page.context
        except Exception:
            pass

    # ========================================================
    # LOGGER
    # ========================================================

    def _log_info(
        self,
        message,
    ):
        if self.logger:
            try:
                self.logger.info(message)
            except Exception:
                pass

    def _log_warning(
        self,
        message,
    ):
        if self.logger:
            try:
                self.logger.warning(message)
            except Exception:
                pass

    def _log_debug(
        self,
        message,
    ):
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
                pages = self.context.pages

                if pages:
                    for candidate_page in reversed(pages):
                        try:
                            if not candidate_page.is_closed():
                                self.page = candidate_page
                                return self.page

                        except Exception:
                            continue

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

        # ====================================================
        # Validate
        # ====================================================

        if not title:
            return {
                "success": False,
                "url": None,
                "title": "",
                "address": address,
                "score": 0.0,
                "title_score": 0.0,
                "address_score": 0.0,
                "coordinates": None,
                "matched_tokens": [],
                "reason": "TITLE_EMPTY",
            }

        if not address:
            return {
                "success": False,
                "url": None,
                "title": title,
                "address": "",
                "score": 0.0,
                "title_score": 0.0,
                "address_score": 0.0,
                "coordinates": None,
                "matched_tokens": [],
                "reason": "ADDRESS_EMPTY",
            }

        # ====================================================
        # Recover page
        # ====================================================

        self._recover_page()

        if self.page is None:
            return {
                "success": False,
                "url": None,
                "title": title,
                "address": address,
                "score": 0.0,
                "title_score": 0.0,
                "address_score": 0.0,
                "coordinates": None,
                "matched_tokens": [],
                "reason": "PAGE_NONE",
            }

        # ====================================================
        # Context
        # ====================================================

        if self.context is None:
            try:
                self.context = self.page.context
            except Exception:
                self.context = None

        # ====================================================
        # Build variants
        # ====================================================

        variants = build_search_variants(
            title,
            address,
        )

        if not variants:
            return {
                "success": False,
                "url": None,
                "title": title,
                "address": address,
                "score": 0.0,
                "title_score": 0.0,
                "address_score": 0.0,
                "coordinates": None,
                "matched_tokens": [],
                "reason": "NO_SEARCH_VARIANTS",
            }

        self._log_debug("Google Maps search variants: " + " | ".join(variants))

        best_result = None
        best_result_score = -1.0

        # ====================================================
        # SEARCH EACH VARIANT
        # ====================================================

        for variant_index, query in enumerate(
            variants,
            start=1,
        ):
            self._recover_page()

            if self.page is None:
                continue

            search_url = _safe_build_search_url(query)

            if not search_url:
                self._log_warning(f"Cannot build Google Maps search URL: {query}")
                continue

            self._log_info(
                f"Google Maps search variant {variant_index}/{len(variants)}: {query}"
            )

            self._log_debug(f"Google Maps URL: {search_url}")

            # =================================================
            # Navigation
            # =================================================

            try:
                (
                    new_page,
                    success,
                    attempt,
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
                    "Google Maps navigation wrapper error: " + str(error)[:250]
                )

                self._recover_page()
                continue

            # =================================================
            # Navigation failed
            # =================================================

            if not success:
                self._log_warning(
                    "Google Maps navigation failed "
                    f"variant={variant_index}/"
                    f"{len(variants)} "
                    f"attempts={attempt}"
                )

                self._recover_page()
                continue

            # =================================================
            # Process search page
            # =================================================

            try:
                result = _process_search_page(
                    self.page,
                    self.context,
                    title,
                    address,
                    logger=self.logger,
                )

                # ------------------------------------------------
                # No result
                # ------------------------------------------------

                if not result:
                    self._log_debug(f"Google Maps variant returned no result: {query}")
                    continue

                # ------------------------------------------------
                # Extract result information
                # ------------------------------------------------

                result_url = result.get("url")
                result_address = result.get("address", "")
                result_title = result.get("title", "")

                score_info = address_match_score(
                    address,
                    result_address,
                )

                final_address_score = score_info.get(
                    "score",
                    0.0,
                )

                final_title_score = title_match_score(
                    title,
                    result_title,
                    result_address,
                )

                is_place_url = _is_usable_google_maps_place_url(result_url)

                # ------------------------------------------------
                # Calculate score
                # ------------------------------------------------

                combined_score = final_title_score * 0.60 + final_address_score * 0.40

                result["_final_score"] = combined_score

                result["title_score"] = final_title_score
                result["address_score"] = final_address_score
                result["score"] = final_address_score
                result["matched_tokens"] = score_info.get(
                    "matched_tokens",
                    [],
                )

                # ------------------------------------------------
                # Keep best result
                # ------------------------------------------------

                if combined_score > best_result_score:
                    best_result = result
                    best_result_score = combined_score

                # =================================================
                # MAIN SUCCESS RULE
                # =================================================
                #
                # Google Maps place URL tồn tại
                # => SUCCESS
                #
                # Không yêu cầu title/address score.
                # =================================================

                if is_place_url:
                    result["success"] = True
                    result["reason"] = "GOOGLE_MAPS_PLACE_URL"

                    # ------------------------------------------------
                    # Coordinate recovery
                    # ------------------------------------------------

                    if not result.get("coordinates"):
                        try:
                            candidate = {
                                "url": result_url,
                                "title": result_title,
                                "texts": [],
                            }

                            coordinates = recover_candidate_coordinates(
                                self.page,
                                self.context,
                                candidate,
                                logger=self.logger,
                            )

                            if coordinates:
                                result["coordinates"] = coordinates

                        except Exception as error:
                            self._log_debug(
                                "Coordinate recovery after success failed: "
                                + str(error)[:200]
                            )

                    # ------------------------------------------------
                    # SUCCESS LOG
                    # ------------------------------------------------

                    self._log_info(
                        "Google Maps SUCCESS: "
                        f"{title} | "
                        f"{result_address} | "
                        f"title_score="
                        f"{final_title_score:.3f} | "
                        f"address_score="
                        f"{final_address_score:.3f} | "
                        f"url={result_url}"
                    )

                    return result

                # ------------------------------------------------
                # Not a usable place URL
                # ------------------------------------------------

                self._log_debug(
                    "Google Maps result rejected: "
                    f"title={result_title!r} "
                    f"address={result_address!r} "
                    f"title_score={final_title_score:.3f} "
                    f"address_score={final_address_score:.3f} "
                    f"url={result_url!r}"
                )

            except Exception as error:
                self._log_warning(
                    "Google Maps page processing error: " + str(error)[:250]
                )

                self._recover_page()
                continue

        # ====================================================
        # FALLBACK
        # ====================================================

        if best_result:
            best_url = best_result.get("url")

            if _is_usable_google_maps_place_url(best_url):
                self._log_info(
                    f"Google Maps FALLBACK SUCCESS: {title} | url={best_url}"
                )

                return {
                    "success": True,
                    "url": best_url,
                    "title": best_result.get(
                        "title",
                        title,
                    ),
                    "address": best_result.get(
                        "address",
                        address,
                    ),
                    "score": best_result.get(
                        "address_score",
                        0.0,
                    ),
                    "title_score": best_result.get(
                        "title_score",
                        0.0,
                    ),
                    "address_score": best_result.get(
                        "address_score",
                        0.0,
                    ),
                    "coordinates": best_result.get("coordinates"),
                    "matched_tokens": best_result.get(
                        "matched_tokens",
                        [],
                    ),
                    "reason": "GOOGLE_MAPS_PLACE_URL_FALLBACK",
                }

        # ====================================================
        # NOT FOUND
        # ====================================================

        self._log_warning(
            f"Google Maps NOT_FOUND / NO_VALID_CANDIDATE: {title} | {address}"
        )

        return {
            "success": False,
            "url": None,
            "title": title,
            "address": address,
            "score": (
                best_result.get(
                    "address_score",
                    0.0,
                )
                if best_result
                else 0.0
            ),
            "title_score": (
                best_result.get(
                    "title_score",
                    0.0,
                )
                if best_result
                else 0.0
            ),
            "address_score": (
                best_result.get(
                    "address_score",
                    0.0,
                )
                if best_result
                else 0.0
            ),
            "coordinates": (best_result.get("coordinates") if best_result else None),
            "matched_tokens": (
                best_result.get(
                    "matched_tokens",
                    [],
                )
                if best_result
                else []
            ),
            "reason": "NOT_FOUND",
        }


# ============================================================
# SIMPLE HELPER
# ============================================================


def search_google_maps(
    page,
    title,
    address,
    context=None,
    logger=None,
    timeout=None,
):
    engine = GoogleMapsSearchEngine(
        page=page,
        context=context,
        logger=logger,
    )

    return engine.search(
        title=title,
        address=address,
        timeout=timeout,
    )
