# ============================================================
# app/search.py
# Address-first Google Maps search
# ACCEPT ANY VALID GOOGLE MAPS PLACE URL
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

    return token


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
        value = str(url).strip().lower()
    except Exception:
        return False

    return "google.com/maps" in value or "maps.google.com" in value


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
    # HARD ACCEPT
    #
    # This is the key fix.
    # --------------------------------------------------------

    if actual_url and _is_google_maps_place_url(actual_url):
        info = address_match_score(
            input_address,
            actual_address,
        )

        title_score = title_match_score(
            input_title,
            actual_title,
            actual_address,
        )

        result.update(
            {
                "success": True,
                "url": actual_url,
                "google_maps_url": actual_url,
                "title": actual_title,
                "address": actual_address,
                "score": info.get(
                    "score",
                    0.0,
                ),
                "address_score": info.get(
                    "score",
                    0.0,
                ),
                "title_score": title_score,
                "matched_tokens": info.get(
                    "matched_tokens",
                    [],
                ),
                "coordinates": coordinates,
                "reason": ("MAPS_PLACE_ACCEPTED"),
                "location_matches": info.get(
                    "location_matches",
                    0,
                ),
                "location_score": info.get(
                    "location_score",
                    0.0,
                ),
                "tail_location_matches": info.get(
                    "tail_location_matches",
                    0,
                ),
                "tail_location_score": info.get(
                    "tail_location_score",
                    0.0,
                ),
                "page": page,
            }
        )

        if logger:
            logger.info(
                "MAPS PLACE ACCEPTED | title=%r | address=%r | url=%r | coordinates=%r",
                actual_title,
                actual_address,
                actual_url,
                coordinates,
            )

        return result

    # --------------------------------------------------------
    # No valid place URL
    # --------------------------------------------------------

    result["reason"] = "NO_VALID_MAPS_PLACE_URL"

    result["title"] = actual_title
    result["address"] = actual_address
    result["coordinates"] = coordinates

    if logger:
        logger.warning(
            "MAPS PLACE REJECTED | No valid /maps/place/ URL | url=%r",
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

        if verified.get("success"):
            return verified

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
            address,
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

        if verified.get("success"):
            return verified

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

        if not address:
            base["reason"] = "ADDRESS_EMPTY"
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

        best = None

        # ====================================================
        # SEARCH VARIANTS
        # ====================================================

        for variant_index, query in enumerate(
            variants,
            1,
        ):
            self._recover_page()

            search_url = _safe_build_search_url(query)

            if not search_url:
                continue

            self._log_info(
                f"Google Maps search variant {variant_index}/{len(variants)}: {query}"
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
                self._log_warning("Google Maps navigation error: " + str(error)[:250])

                continue

            if not success:
                self._log_warning(
                    "Google Maps navigation failed "
                    f"variant={variant_index}/"
                    f"{len(variants)} "
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
