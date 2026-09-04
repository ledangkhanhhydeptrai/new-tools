# ============================================================
# app/matcher.py
# ============================================================

from .utils import (
    normalize_name,
    normalize_address,
    token_similarity,
    sequence_similarity,
)
from .validator import (
    calculate_name_score,
    calculate_address_score,
)


# ============================================================
# SCORE
# ============================================================


def calculate_match_score(
    input_title,
    input_address,
    item,
):
    """
    Score giữa input và record cache.
    """

    name_score = calculate_name_score(
        input_title,
        item.get("title", ""),
    )

    address_score = calculate_address_score(
        input_address,
        item.get("address", ""),
    )

    # Cả title và address đều có
    if input_title and input_address:
        return round(
            name_score * 0.60 + address_score * 0.40,
            2,
        )

    if input_title:
        return name_score

    if input_address:
        return address_score

    return 0.0


# ============================================================
# EXACT LOOKUP
# ============================================================


def find_exact_match(
    title,
    address,
    lookup,
):
    """
    Exact:

    1. title + address
    2. title
    3. address
    """

    n_title = normalize_name(title)
    n_address = normalize_address(address)

    combined = f"{n_title}|{n_address}"

    # --------------------------------------------------------
    # Combined
    # --------------------------------------------------------

    if combined != "|":
        url = lookup.get(
            "combined",
            {},
        ).get(combined)

        if url:
            return {
                "url": url,
                "method": "EXACT_COMBINED",
                "score": 100,
            }

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    if n_title:
        title_matches = lookup.get(
            "title",
            {},
        ).get(n_title)

        if title_matches:
            # title exact nhưng có thể nhiều address.
            # Nếu chỉ có 1 thì có thể dùng.
            if isinstance(
                title_matches,
                str,
            ):
                return {
                    "url": title_matches,
                    "method": "EXACT_TITLE",
                    "score": 100,
                }

    # --------------------------------------------------------
    # Address
    # --------------------------------------------------------

    if n_address:
        url = lookup.get(
            "address",
            {},
        ).get(n_address)

        if url:
            return {
                "url": url,
                "method": "EXACT_ADDRESS",
                "score": 100,
            }

    return None


# ============================================================
# FUZZY
# ============================================================


def find_fuzzy_match(
    title,
    address,
    lookup,
    minimum_score=88,
):
    """
    Fuzzy matching.

    Quan trọng:
    Không tự động lấy chỉ vì title giống.
    """

    items = lookup.get(
        "items",
        [],
    )

    if not items:
        return None

    best = None

    best_score = 0

    for item in items:
        item_title = item.get(
            "title",
            "",
        )

        item_address = item.get(
            "address",
            "",
        )

        name_score = calculate_name_score(
            title,
            item_title,
        )

        address_score = calculate_address_score(
            address,
            item_address,
        )

        # ----------------------------------------------------
        # SAFETY RULE
        # ----------------------------------------------------

        # Có cả title + address:
        # phải có title rất tốt và address ít nhất khá.
        if title and address:
            if name_score < 88 or address_score < 55:
                continue

            score = name_score * 0.60 + address_score * 0.40

        elif title:
            # Title-only fuzzy phải cực kỳ chặt.
            if name_score < 95:
                continue

            score = name_score

        elif address:
            if address_score < 90:
                continue

            score = address_score

        else:
            continue

        if score > best_score:
            best_score = score

            best = {
                "url": item.get(
                    "url",
                    "",
                ),
                "method": "FUZZY",
                "score": round(
                    score,
                    2,
                ),
                "name_score": round(
                    name_score,
                    2,
                ),
                "address_score": round(
                    address_score,
                    2,
                ),
                "matched_title": item_title,
                "matched_address": item_address,
            }

    if best and best_score >= minimum_score:
        return best

    return None


# ============================================================
# MAIN MATCH
# ============================================================


def find_best_match(
    title,
    address,
    lookup,
    minimum_score=88,
):
    """
    Exact trước.
    Fuzzy sau.
    """

    exact = find_exact_match(
        title,
        address,
        lookup,
    )

    if exact:
        return exact

    return find_fuzzy_match(
        title,
        address,
        lookup,
        minimum_score,
    )
