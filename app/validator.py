# ============================================================
# app/validator.py
# ============================================================

from .utils import (
    clean_google_maps_url,
    is_google_maps_place_url,
    normalize_name,
    normalize_address,
    token_similarity,
    sequence_similarity,
)


# ============================================================
# URL VALIDATION
# ============================================================


def validate_google_maps_url(url):
    """
    Return True nếu URL là Google Maps PLACE URL.
    """

    return bool(is_google_maps_place_url(url))


# ============================================================
# TEXT SCORES
# ============================================================


def calculate_name_score(
    input_title,
    result_title,
):
    input_title = normalize_name(input_title)

    result_title = normalize_name(result_title)

    if not input_title or not result_title:
        return 0.0

    token_score = token_similarity(
        input_title,
        result_title,
    )

    sequence_score = sequence_similarity(
        input_title,
        result_title,
    )

    return round(
        max(
            token_score,
            sequence_score,
        ),
        2,
    )


def calculate_address_score(
    input_address,
    result_address,
):
    input_address = normalize_address(input_address)

    result_address = normalize_address(result_address)

    if not input_address or not result_address:
        return 0.0

    token_score = token_similarity(
        input_address,
        result_address,
    )

    sequence_score = sequence_similarity(
        input_address,
        result_address,
    )

    return round(
        max(
            token_score,
            sequence_score,
        ),
        2,
    )


# ============================================================
# CONFIDENCE
# ============================================================


def calculate_confidence(
    name_score,
    address_score,
):
    """
    HIGH:
        title tốt + address tốt

    MEDIUM:
        có dấu hiệu đúng nhưng chưa đủ chắc

    LOW:
        không nên tự động chấp nhận
    """

    if name_score >= 92 and address_score >= 80:
        return "HIGH"

    if name_score >= 88 and address_score >= 65:
        return "MEDIUM"

    if name_score >= 75 and address_score >= 50:
        return "LOW"

    return "VERY_LOW"


def is_safe_match(
    name_score,
    address_score,
):
    """
    Chỉ HIGH mới tự động accept.
    """

    return name_score >= 92 and address_score >= 80


# ============================================================
# FULL VALIDATION
# ============================================================


def validate_result(
    input_title,
    input_address,
    result_title,
    result_address,
    maps_url,
):
    """
    Validate toàn bộ kết quả.

    Return dict.
    """

    maps_url = clean_google_maps_url(maps_url)

    if not maps_url:
        return {
            "valid_url": False,
            "safe": False,
            "confidence": "VERY_LOW",
            "name_score": 0,
            "address_score": 0,
            "reason": "Invalid Google Maps PLACE URL",
        }

    name_score = calculate_name_score(
        input_title,
        result_title,
    )

    address_score = calculate_address_score(
        input_address,
        result_address,
    )

    confidence = calculate_confidence(
        name_score,
        address_score,
    )

    safe = is_safe_match(
        name_score,
        address_score,
    )

    return {
        "valid_url": True,
        "safe": safe,
        "confidence": confidence,
        "name_score": name_score,
        "address_score": address_score,
        "reason": ("High confidence" if safe else "Match is not strong enough"),
    }
