# ============================================================
# app/excel.py
# Google Maps Excel processor - standardized FOUND/MISSING output
#
# FLOW:
#   1. Existing Google Maps URL -> HARD FAST PATH -> FOUND
#   2. Cache -> validate
#   3. JSON -> validate
#   4. Real Google Maps Search
#
# IMPORTANT:
#   - Existing Excel Maps URL is trusted immediately.
#   - Existing Excel URL is NEVER navigated or re-validated.
#   - Cache / JSON URL MUST be validated.
#   - Successful search result is NOT validated twice.
#   - FOUND is NEVER overwritten by MISSING.
# ============================================================

import json
import os
import time

import pandas as pd

from config import (
    TITLE_COLUMNS,
    ADDRESS_COLUMNS,
    URL_COLUMNS,
    GOOGLE_MAPS_COLUMNS,
    CACHE_DIR,
    CACHE_FILE_NAME,
    RESULT_SUFFIX,
    MISSING_SUFFIX,
    REPORT_SUFFIX,
    CHECKPOINT_EVERY_ROWS,
    RESET_PAGE_EVERY_SEARCHES,
    SEARCH_DELAY_MIN,
    SEARCH_DELAY_MAX,
    FAILED_SEARCH_DELAY_MIN,
    FAILED_SEARCH_DELAY_MAX,
)

from .browser import GoogleMapsBrowser
from .cache import GoogleMapsCache
from .matcher import find_best_match

from .search import (
    GoogleMapsSearchEngine,
    verify_candidate_address,
    is_google_maps_place_url,
    result_matches_address,
    result_matches_title,
    get_address_province,
)

from .utils import (
    safe_text,
    normalize_name,
    clean_google_maps_url,
    is_google_maps_url,
    random_delay,
    now_iso,
    save_json_atomic,
)


# ============================================================
# COLUMN HELPERS
# ============================================================


def find_column(df, candidates):
    """
    Tìm column theo tên một cách linh hoạt.

    Ví dụ:

        Google Maps URL
        google_maps_url
        GoogleMapsURL

    đều có thể match nếu normalize_name()
    xử lý tương ứng.
    """

    normalized = {normalize_name(column): column for column in df.columns}

    for candidate in candidates:
        key = normalize_name(candidate)

        if key in normalized:
            return normalized[key]

    return None


def _record_value(record, *keys):
    """
    Lấy giá trị đầu tiên không rỗng từ record.
    """

    for key in keys:
        value = safe_text(record.get(key))

        if value:
            return value

    return ""


# ============================================================
# EXISTING GOOGLE MAPS URL
# ============================================================


def _get_existing_maps_url(existing_maps, existing_url):
    """
    Lấy Google Maps Place URL đã có sẵn.

    Ưu tiên:
        1. google_maps_url
        2. url

    HARD FAST PATH:

        Nếu URL là Google Maps Place URL hợp lệ
        -> trả về ngay.

    KHÔNG navigate.
    KHÔNG validate.
    KHÔNG search.
    """

    # --------------------------------------------------------
    # 1. google_maps_url
    # --------------------------------------------------------

    existing_maps = clean_google_maps_url(safe_text(existing_maps))

    if existing_maps and is_google_maps_place_url(existing_maps):
        return existing_maps

    # --------------------------------------------------------
    # 2. url
    # --------------------------------------------------------

    existing_url = clean_google_maps_url(safe_text(existing_url))

    if existing_url and is_google_maps_place_url(existing_url):
        return existing_url

    return ""


# ============================================================
# JSON LOOKUP
# ============================================================


def load_json_lookup(base_dir, logger=None):
    """
    Load tất cả JSON trong cùng folder với Excel.

    Chỉ lấy Google Maps Place URL hợp lệ.
    """

    lookup = {
        "title": {},
        "address": {},
        "combined": {},
        "items": [],
    }

    base_dir = str(base_dir)

    if not os.path.isdir(base_dir):
        return lookup

    for filename in os.listdir(base_dir):
        if not filename.lower().endswith(".json"):
            continue

        json_path = os.path.join(
            base_dir,
            filename,
        )

        try:
            with open(
                json_path,
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

        except Exception as error:
            if logger:
                logger.warning(
                    "Cannot read JSON %s: %s",
                    json_path,
                    error,
                )

            continue

        # ----------------------------------------------------
        # JSON dạng object chứa list
        # ----------------------------------------------------

        if isinstance(data, dict):
            data = next(
                (
                    data.get(key)
                    for key in (
                        "data",
                        "results",
                        "items",
                        "businesses",
                        "hotels",
                        "places",
                    )
                    if isinstance(
                        data.get(key),
                        list,
                    )
                ),
                [],
            )

        if not isinstance(data, list):
            continue

        # ----------------------------------------------------
        # Records
        # ----------------------------------------------------

        for record in data:
            if not isinstance(record, dict):
                continue

            title = _record_value(
                record,
                "title",
                "name",
                "hotel_name",
                "business_name",
            )

            address = _record_value(
                record,
                "address",
                "full_address",
                "location",
            )

            maps_url = _record_value(
                record,
                "google_maps_url",
                "maps_url",
                "google_map_url",
                "map_url",
                "url",
            )

            maps_url = clean_google_maps_url(maps_url)

            # ------------------------------------------------
            # Chỉ nhận Google Maps Place URL
            # ------------------------------------------------

            if not maps_url or not is_google_maps_place_url(maps_url):
                continue

            n_title = normalize_name(title)
            n_address = normalize_name(address)

            combined = f"{n_title}|{n_address}"

            item = {
                "title": n_title,
                "address": n_address,
                "combined": combined,
                "url": maps_url,
            }

            lookup["items"].append(item)

            # ------------------------------------------------
            # Combined lookup
            # ------------------------------------------------

            if combined != "|":
                values = lookup["combined"].setdefault(combined, [])
                if maps_url not in values:
                    values.append(maps_url)

            # ------------------------------------------------
            # Title lookup
            # ------------------------------------------------

            if n_title:
                values = lookup["title"].setdefault(n_title, [])
                if maps_url not in values:
                    values.append(maps_url)

            # ------------------------------------------------
            # Address lookup
            # ------------------------------------------------

            if n_address:
                values = lookup["address"].setdefault(n_address, [])
                if maps_url not in values:
                    values.append(maps_url)

    if logger:
        logger.info(
            "JSON lookup loaded: %s Maps URLs",
            len(lookup["items"]),
        )

    return lookup


# ============================================================
# EXCEL SAVE
# ============================================================


def safe_save_excel(df, output_path):
    """
    Atomic Excel save.
    """

    output_path = str(output_path)

    directory = os.path.dirname(os.path.abspath(output_path))

    os.makedirs(
        directory,
        exist_ok=True,
    )

    temp_path = output_path + ".tmp.xlsx"

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

        return True

    except Exception as error:
        print(f"⚠️ Excel save failed: {error}")

        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        except Exception:
            pass

        return False


# ============================================================
# CACHE / JSON VALIDATION
# ============================================================


def _validate_existing_url(
    search_engine,
    page,
    context,
    title,
    address,
    maps_url,
    logger=None,
):
    """
    Validate URL từ CACHE / JSON.

    KHÁC với Excel existing URL.

    Cache / JSON vẫn phải validate vì có thể:

        - stale
        - sai hotel
        - sai address
        - URL cũ
        - record bị duplicate

    Existing Excel URL KHÔNG đi qua function này.
    """

    maps_url = clean_google_maps_url(maps_url)

    if not maps_url or not is_google_maps_place_url(maps_url):
        return None, page

    candidate = {
        "url": maps_url,
        "google_maps_url": maps_url,
        "title": title,
        "texts": [],
    }

    verified = verify_candidate_address(
        page,
        context,
        candidate,
        title,
        address,
        logger=logger,
    )

    verified_page = verified.get(
        "page",
        page,
    )

    search_engine.update_page(verified_page)

    if (
        verified.get("success")
        and result_matches_address(
            address,
            verified.get("address", ""),
        )
        and result_matches_title(
            title,
            verified.get("title", ""),
        )
    ):
        # ----------------------------------------------------
        # Đảm bảo URL luôn được lấy đúng
        # ----------------------------------------------------

        verified_url = (
            verified.get("google_maps_url") or verified.get("url") or maps_url
        )

        verified_url = clean_google_maps_url(verified_url)

        verified["google_maps_url"] = verified_url
        verified["url"] = verified_url

        return (
            verified,
            verified_page,
        )

    if verified.get("success") and logger:
        logger.warning(
            "Reused Maps URL rejected: title/address mismatch | "
            "url=%r | input_title=%r | actual_title=%r | "
            "input_address=%r | actual_address=%r",
            maps_url,
            title,
            verified.get("title", ""),
            address,
            verified.get("address", ""),
        )

    if logger:
        logger.debug(
            "Reused Maps URL rejected: url=%r address=%r score=%.3f reason=%s",
            maps_url,
            verified.get(
                "address",
                "",
            ),
            verified.get(
                "address_score",
                0.0,
            ),
            verified.get(
                "reason",
                "",
            ),
        )

    return (
        None,
        verified_page,
    )


# ============================================================
# WRITE FOUND RESULT
# ============================================================


def _write_found_result(
    df,
    index,
    google_maps_col,
    url_col,
    maps_url,
    existing_url,
):
    """
    Ghi kết quả FOUND.

    Không overwrite URL gốc nếu URL gốc
    đã là Google Maps URL.
    """

    maps_url = clean_google_maps_url(maps_url)

    if not maps_url:
        return False

    df.at[
        index,
        google_maps_col,
    ] = maps_url

    # --------------------------------------------------------
    # Chỉ điền URL nếu URL hiện tại chưa phải Maps URL
    # --------------------------------------------------------

    if not is_google_maps_place_url(safe_text(existing_url)):
        df.at[
            index,
            url_col,
        ] = maps_url

    return True


def _write_url_to_column(
    df,
    index,
    column,
    maps_url,
    clear_columns=None,
):
    maps_url = clean_google_maps_url(maps_url)

    if not maps_url or not is_google_maps_place_url(maps_url):
        return False

    for clear_column in clear_columns or []:
        df.at[index, clear_column] = ""

    df.at[index, column] = maps_url
    return True


def _write_verified_result(
    df,
    index,
    address_col,
    google_maps_col,
    other_province_col,
    url_col,
    existing_url,
    input_address,
    verified,
    maps_url,
    require_province=False,
    logger=None,
):
    actual_address = safe_text(verified.get("address", ""))
    input_province = get_address_province(input_address)
    actual_province = get_address_province(actual_address)

    if require_province and (not actual_address or not actual_province):
        if logger:
            logger.warning(
                "RESULT REJECTED: usable province was not found | address=%r",
                actual_address,
            )
        return False, False, actual_address, actual_province

    if actual_address:
        df.at[index, address_col] = actual_address

    is_other_province = bool(
        input_province and actual_province and input_province != actual_province
    )

    if is_other_province:
        wrote = _write_url_to_column(
            df,
            index,
            other_province_col,
            maps_url,
            clear_columns=[google_maps_col],
        )
    else:
        wrote = _write_found_result(
            df=df,
            index=index,
            google_maps_col=google_maps_col,
            url_col=url_col,
            maps_url=maps_url,
            existing_url=existing_url,
        )

        if wrote:
            df.at[index, other_province_col] = ""

    return wrote, is_other_province, actual_address, actual_province


# ============================================================
# PROCESS EXCEL
# ============================================================


def process_excel(
    file_path,
    headless=True,
    logger=None,
):
    """
    Main Excel processor.

    Priority:

        1. Existing Excel Maps URL
        2. Cache
        3. JSON
        4. Real Google Maps search

    Existing Excel Maps URL:

        -> HARD FAST PATH
        -> FOUND immediately
        -> NEVER navigate
        -> NEVER validate
        -> NEVER search
    """

    # ========================================================
    # FILE
    # ========================================================

    file_path = os.path.abspath(str(file_path))

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    print("\n" + "=" * 75)

    print("GOOGLE MAPS TOOL - STRICT ADDRESS VALIDATION")

    print("=" * 75)

    print(f"📄 Input: {file_path}")

    # ========================================================
    # READ EXCEL
    # ========================================================

    df = pd.read_excel(file_path)

    print(f"📊 Rows: {len(df)}")

    # ========================================================
    # FIND COLUMNS
    # ========================================================

    title_col = find_column(
        df,
        TITLE_COLUMNS,
    )

    address_col = find_column(
        df,
        ADDRESS_COLUMNS,
    )

    url_col = find_column(
        df,
        URL_COLUMNS,
    )

    google_maps_col = find_column(
        df,
        GOOGLE_MAPS_COLUMNS,
    )

    other_province_col = find_column(
        df,
        ["tỉnh khác", "tinh khac", "other province"],
    )

    if not title_col:
        raise ValueError("Cannot find title/name column.")

    if not address_col:
        raise ValueError("Cannot find address column.")

    # ========================================================
    # CREATE MISSING COLUMNS
    # ========================================================

    if not url_col:
        url_col = "url"

        df[url_col] = ""

    if not google_maps_col:
        google_maps_col = "google_maps_url"

        df[google_maps_col] = ""

    if not other_province_col:
        other_province_col = "tỉnh khác"
        df[other_province_col] = ""

    print(f"📝 Title column       : {title_col}")

    print(f"📍 Address column     : {address_col}")

    print(f"🔗 URL column         : {url_col}")

    print(f"🗺️ Google Maps column : {google_maps_col}")
    print(f"🌍 Other province    : {other_province_col}")

    # ========================================================
    # PATHS
    # ========================================================

    input_path = os.path.splitext(file_path)[0]

    result_path = input_path + RESULT_SUFFIX

    missing_path = input_path + MISSING_SUFFIX

    report_path = input_path + REPORT_SUFFIX

    cache_path = CACHE_DIR / CACHE_FILE_NAME

    # ========================================================
    # CACHE
    # ========================================================

    cache = GoogleMapsCache(cache_path)

    print(f"💾 Cache entries: {len(cache)}")

    # ========================================================
    # JSON LOOKUP
    # ========================================================

    json_lookup = load_json_lookup(
        os.path.dirname(file_path),
        logger,
    )

    # ========================================================
    # BROWSER
    # ========================================================

    browser = GoogleMapsBrowser(
        headless=headless,
        logger=logger,
    )

    browser.start()

    page = browser.get_page()

    context = browser.get_context()

    search_engine = GoogleMapsSearchEngine(
        page,
        context,
        logger,
    )

    # ========================================================
    # COUNTERS
    # ========================================================

    total_rows = len(df)

    processed = 0
    skipped = 0
    cache_hits = 0
    json_hits = 0
    searches = 0
    found = 0
    missing = 0
    recovery_count = 0

    missing_indexes = []

    # ========================================================
    # TIMER
    # ========================================================

    start_time = time.time()

    try:
        # ====================================================
        # MAIN LOOP
        # ====================================================

        for index in df.index:
            processed += 1

            # ------------------------------------------------
            # INPUT VALUES
            # ------------------------------------------------

            title = safe_text(
                df.at[
                    index,
                    title_col,
                ]
            )

            address = safe_text(
                df.at[
                    index,
                    address_col,
                ]
            )

            existing_maps = safe_text(
                df.at[
                    index,
                    google_maps_col,
                ]
            )

            existing_url = safe_text(
                df.at[
                    index,
                    url_col,
                ]
            )

            # ------------------------------------------------
            # HEADER
            # ------------------------------------------------

            print("\n" + "-" * 75)

            print(f"[{processed}/{total_rows}] {title}")

            print(f"📍 {address}")

            # =================================================
            # EMPTY INPUT
            # =================================================

            if not title:
                missing += 1

                missing_indexes.append(index)

                print("❌ MISSING")
                print("🎯 status=MISSING")
                print("🎯 reason=NOT_VERIFIED")
                print("🎯 detail_reason=TITLE_EMPTY")
                print("🎯 source=INPUT")

                continue

            # =================================================
            # 1. EXISTING EXCEL MAPS URL
            #
            # HARD FAST PATH
            #
            # NO:
            #   navigation
            #   validation
            #   cache
            #   JSON
            #   search
            #   delay
            # =================================================

            reusable_url = _get_existing_maps_url(
                existing_maps,
                existing_url,
            )

            # -------------------------------------------------
            # DEBUG
            # -------------------------------------------------

            if logger:
                logger.info(
                    "Existing URL check | "
                    "maps_col=%s | "
                    "url_col=%s | "
                    "maps=%r | "
                    "url=%r | "
                    "reusable=%r",
                    google_maps_col,
                    url_col,
                    existing_maps,
                    existing_url,
                    reusable_url,
                )

            if reusable_url:
                # =================================================
                # HARD FAST PATH
                #
                # Existing Google Maps Place URL came directly from
                # the Excel record. Trust it immediately.
                #
                # NO:
                #   - navigation
                #   - validation
                #   - cache
                #   - JSON
                #   - real search
                # =================================================

                reusable_url = clean_google_maps_url(reusable_url)

                if reusable_url and is_google_maps_place_url(reusable_url):
                    wrote = _write_found_result(
                        df=df,
                        index=index,
                        google_maps_col=google_maps_col,
                        url_col=url_col,
                        maps_url=reusable_url,
                        existing_url=existing_url,
                    )

                    if wrote:
                        df.at[
                            index,
                            other_province_col,
                        ] = ""

                        found += 1
                        skipped += 1

                        print("✅ FOUND")
                        print("🎯 status=FOUND")
                        print("🎯 reason=VERIFIED")
                        print("🎯 detail_reason=EXISTING_EXCEL_URL_TRUSTED")
                        print("🎯 source=EXCEL")
                        print(f"🔗 {reusable_url}")

                        if logger:
                            logger.info(
                                "FINAL RESULT | "
                                "status=FOUND | "
                                "reason=VERIFIED | "
                                "detail_reason="
                                "EXISTING_EXCEL_URL_TRUSTED | "
                                "source=EXCEL | "
                                "title=%r | address=%r | "
                                "url=%r",
                                title,
                                address,
                                reusable_url,
                            )

                        continue

            # =================================================
            # 2. CACHE
            # =================================================

            cached = cache.get(
                title,
                address,
            )

            if cached:
                maps_url = clean_google_maps_url(
                    cached.get(
                        "google_maps_url",
                        "",
                    )
                )

                verified, page = _validate_existing_url(
                    search_engine,
                    page,
                    context,
                    title,
                    address,
                    maps_url,
                    logger,
                )

                if verified:
                    good_url = clean_google_maps_url(
                        verified.get("google_maps_url")
                        or verified.get(
                            "url",
                            "",
                        )
                    )

                    if good_url and is_google_maps_place_url(good_url):
                        wrote, is_other, _, _ = _write_verified_result(
                            df,
                            index,
                            address_col,
                            google_maps_col,
                            other_province_col,
                            url_col,
                            existing_url,
                            address,
                            verified,
                            good_url,
                            logger=logger,
                        )

                        if wrote:
                            found += 1
                            cache_hits += 1

                            print("✅ FOUND")
                            print("🎯 status=FOUND")
                            print("🎯 reason=VERIFIED")
                            print(
                                f"🎯 detail_reason="
                                f"{verified.get('reason', 'CACHE_VALIDATED')}"
                            )
                            print("🎯 source=CACHE")
                            print(f"🔗 {good_url}")
                            print(
                                f"🎯 title_score={verified.get('title_score', 0):.3f}"
                            )
                            print(
                                f"🎯 address_score="
                                f"{verified.get('address_score', 0):.3f}"
                            )
                            print(
                                f"🎯 location_score="
                                f"{verified.get('location_score', 0):.3f}"
                            )

                            continue
            # =================================================
            # 3. JSON LOOKUP
            # =================================================

            match = find_best_match(
                title,
                address,
                json_lookup,
            )

            if match:
                maps_url = clean_google_maps_url(
                    match.get(
                        "url",
                        "",
                    )
                )

                verified, page = _validate_existing_url(
                    search_engine,
                    page,
                    context,
                    title,
                    address,
                    maps_url,
                    logger,
                )

                if verified:
                    good_url = clean_google_maps_url(
                        verified.get("google_maps_url")
                        or verified.get(
                            "url",
                            "",
                        )
                    )

                    if good_url and is_google_maps_place_url(good_url):
                        wrote, is_other, _, _ = _write_verified_result(
                            df,
                            index,
                            address_col,
                            google_maps_col,
                            other_province_col,
                            url_col,
                            existing_url,
                            address,
                            verified,
                            good_url,
                            logger=logger,
                        )

                        if wrote:
                            found += 1
                            json_hits += 1

                            # ---------------------------------
                            # Save validated URL to cache
                            # ---------------------------------

                            if not is_other:
                                cache.set(
                                    title,
                                    address,
                                    good_url,
                                    confidence="HIGH",
                                    name_score=verified.get(
                                        "title_score",
                                        0,
                                    ),
                                    address_score=verified.get(
                                        "address_score",
                                        0,
                                    ),
                                    method=match.get(
                                        "method",
                                        "JSON",
                                    ),
                                )

                            print("✅ FOUND")
                            print("🎯 status=FOUND")
                            print("🎯 reason=VERIFIED")
                            print(
                                f"🎯 detail_reason="
                                f"{verified.get('reason', 'JSON_VALIDATED')}"
                            )
                            print("🎯 source=JSON")
                            print(f"🔗 {good_url}")

                            continue

            # =================================================
            # 4. REAL GOOGLE MAPS SEARCH
            # =================================================

            searches += 1

            print("🔎 REAL GOOGLE MAPS SEARCH")

            # ------------------------------------------------
            # Keep page reference
            # ------------------------------------------------

            old_page = page

            search_engine.update_page(page)

            # ------------------------------------------------
            # SEARCH
            # ------------------------------------------------

            result = search_engine.search(
                title,
                address,
            )

            # ------------------------------------------------
            # Get returned page
            # ------------------------------------------------

            page = result.get(
                "page",
                search_engine.page,
            )

            search_engine.update_page(page)

            # ------------------------------------------------
            # Recovery counter
            # ------------------------------------------------

            if page is not old_page:
                recovery_count += 1

            # =================================================
            # RESULT URL
            # =================================================

            raw_google_maps_url = result.get("google_maps_url")

            raw_url = result.get("url")

            maps_url = clean_google_maps_url(raw_google_maps_url or raw_url or "")

            # =================================================
            # DEBUG SEARCH RESULT
            # =================================================

            if logger:
                logger.info(
                    "EXCEL SEARCH RESULT | "
                    "success=%s | "
                    "status=%s | "
                    "reason=%s | "
                    "detail_reason=%s | "
                    "google_maps_url=%r | "
                    "url=%r | "
                    "cleaned_url=%r",
                    result.get("success"),
                    result.get("status"),
                    result.get("reason"),
                    result.get("detail_reason"),
                    raw_google_maps_url,
                    raw_url,
                    maps_url,
                )

            # =================================================
            # SEARCH SUCCESS
            #
            # search.py đã validate candidate.
            #
            # KHÔNG:
            #   verify_candidate_address()
            #   navigate again
            #   validate again
            # =================================================

            if result.get("success"):
                # ------------------------------------------------
                # SUCCESS nhưng URL không hợp lệ
                # ------------------------------------------------

                if not maps_url or not is_google_maps_place_url(maps_url):
                    if logger:
                        logger.error(
                            "SEARCH SUCCESS BUT INVALID "
                            "MAPS URL | "
                            "title=%r | "
                            "address=%r | "
                            "google_maps_url=%r | "
                            "url=%r | "
                            "cleaned=%r",
                            title,
                            address,
                            raw_google_maps_url,
                            raw_url,
                            maps_url,
                        )

                    reason = "SUCCESS_BUT_INVALID_MAPS_URL"

                    print("❌ MISSING")
                    print("🎯 status=MISSING")
                    print("🎯 reason=NOT_VERIFIED")
                    print("🎯 detail_reason=SUCCESS_BUT_INVALID_MAPS_URL")
                    print("🎯 source=SEARCH")
                    print(f"   google_maps_url={raw_google_maps_url!r}")
                    print(f"   url={raw_url!r}")

                    # ---------------------------------------------
                    # Không được gọi đây là NOT_FOUND.
                    #
                    # Nhưng vẫn đưa vào missing để user biết
                    # record chưa có URL usable.
                    # ---------------------------------------------

                    missing += 1

                    missing_indexes.append(index)

                    if logger:
                        logger.warning(
                            "Record marked missing due to "
                            "invalid URL after successful "
                            "candidate verification | "
                            "reason=%s",
                            reason,
                        )

                    random_delay(
                        FAILED_SEARCH_DELAY_MIN,
                        FAILED_SEARCH_DELAY_MAX,
                    )

                    # ---------------------------------------------
                    # Page reset
                    # ---------------------------------------------

                    if (
                        RESET_PAGE_EVERY_SEARCHES
                        and searches % RESET_PAGE_EVERY_SEARCHES == 0
                    ):
                        print(f"♻️ Periodic Page reset after {searches} searches")

                        page = browser.recreate_page()

                        search_engine.update_page(page)

                        recovery_count += 1

                    # ---------------------------------------------
                    # Checkpoint
                    # ---------------------------------------------

                    if CHECKPOINT_EVERY_ROWS and processed % CHECKPOINT_EVERY_ROWS == 0:
                        print(f"💾 CHECKPOINT row={processed}")

                        safe_save_excel(
                            df,
                            result_path,
                        )

                        cache.save()

                        if missing_indexes:
                            safe_save_excel(
                                df.loc[missing_indexes].copy(),
                                missing_path,
                            )

                    continue

                # =================================================
                # VALID SUCCESS
                # =================================================

                wrote, is_other_province, actual_address, actual_province = (
                    _write_verified_result(
                        df,
                        index,
                        address_col,
                        google_maps_col,
                        other_province_col,
                        url_col,
                        existing_url,
                        address,
                        result,
                        maps_url,
                        require_province=(result.get("search_mode") == "title"),
                        logger=logger,
                    )
                )

                if is_other_province and logger:
                    logger.warning(
                        "RESULT STORED IN OTHER PROVINCE COLUMN | "
                        "input_province=%r | actual_province=%r | url=%r",
                        get_address_province(address),
                        actual_province,
                        maps_url,
                    )

                if not wrote:
                    # ------------------------------------------------
                    # Safety guard.
                    #
                    # Không nên xảy ra vì maps_url đã validate.
                    # ------------------------------------------------

                    if logger:
                        logger.error(
                            "SEARCH SUCCESS BUT "
                            "WRITE FOUND FAILED | "
                            "title=%r | "
                            "address=%r | "
                            "maps_url=%r",
                            title,
                            address,
                            maps_url,
                        )

                    missing += 1

                    missing_indexes.append(index)

                    continue

                # ------------------------------------------------
                # FOUND COUNTER
                # ------------------------------------------------

                found += 1

                # ------------------------------------------------
                # CACHE SUCCESS
                # ------------------------------------------------

                if not is_other_province:
                    cache.set(
                        title,
                        actual_address or address,
                        maps_url,
                        confidence="HIGH",
                        name_score=result.get(
                            "title_score",
                            0,
                        ),
                        address_score=result.get(
                            "address_score",
                            0,
                        ),
                        method="SEARCH",
                    )

                # ------------------------------------------------
                # OUTPUT
                # ------------------------------------------------

                print("✅ FOUND")
                print("🎯 status=FOUND")
                print("🎯 reason=VERIFIED")
                print(
                    f"🎯 detail_reason="
                    f"{result.get('detail_reason', 'VERIFIED_CANDIDATE')}"
                )
                print("🎯 source=SEARCH")

                if is_other_province:
                    print(f"🌍 province_result=OTHER_PROVINCE:{actual_province}")

                print(f"🔗 {maps_url}")
                print(f"🎯 title_score={result.get('title_score', 0):.3f}")
                print(f"🎯 address_score={result.get('address_score', 0):.3f}")
                print(f"🎯 location_score={result.get('location_score', 0):.3f}")

                # ------------------------------------------------
                # NORMAL DELAY
                # ------------------------------------------------

                random_delay(
                    SEARCH_DELAY_MIN,
                    SEARCH_DELAY_MAX,
                )

                # ------------------------------------------------
                # ABSOLUTE STOP
                # ------------------------------------------------

                continue

            # =================================================
            # SEARCH FAILED
            # =================================================

            missing += 1

            missing_indexes.append(index)

            detail_reason = (
                result.get("detail_reason") or result.get("reason") or "NOT_FOUND"
            )

            print("❌ MISSING")
            print("🎯 status=MISSING")
            print("🎯 reason=NOT_VERIFIED")
            print(f"🎯 detail_reason={detail_reason}")
            print("🎯 source=SEARCH")

            # ------------------------------------------------
            # FAILED DELAY
            # ------------------------------------------------

            random_delay(
                FAILED_SEARCH_DELAY_MIN,
                FAILED_SEARCH_DELAY_MAX,
            )

            # =================================================
            # PERIODIC PAGE RESET
            # =================================================

            if RESET_PAGE_EVERY_SEARCHES and searches % RESET_PAGE_EVERY_SEARCHES == 0:
                print(f"♻️ Periodic Page reset after {searches} searches")

                page = browser.recreate_page()

                search_engine.update_page(page)

                recovery_count += 1

            # =================================================
            # CHECKPOINT
            # =================================================

            if CHECKPOINT_EVERY_ROWS and processed % CHECKPOINT_EVERY_ROWS == 0:
                print(f"💾 CHECKPOINT row={processed}")

                safe_save_excel(
                    df,
                    result_path,
                )

                cache.save()

                if missing_indexes:
                    safe_save_excel(
                        df.loc[missing_indexes].copy(),
                        missing_path,
                    )

    # ========================================================
    # CTRL+C
    # ========================================================

    except KeyboardInterrupt:
        print("\n🛑 CTRL+C detected. Saving emergency checkpoint...")

        safe_save_excel(
            df,
            result_path,
        )

        cache.save()

        if missing_indexes:
            safe_save_excel(
                df.loc[missing_indexes].copy(),
                missing_path,
            )

        raise

    # ========================================================
    # UNEXPECTED ERROR
    # ========================================================

    except Exception as error:
        if logger:
            logger.exception("Unexpected processing error")

        print(f"\n❌ Unexpected error: {error}")

        safe_save_excel(
            df,
            result_path,
        )

        cache.save()

        if missing_indexes:
            safe_save_excel(
                df.loc[missing_indexes].copy(),
                missing_path,
            )

        raise

    # ========================================================
    # CLOSE BROWSER
    # ========================================================

    finally:
        browser.close()

    # ========================================================
    # FINAL SAVE
    # ========================================================

    safe_save_excel(
        df,
        result_path,
    )

    cache.save()

    # ========================================================
    # MISSING FILE
    # ========================================================

    if missing_indexes:
        safe_save_excel(
            df.loc[missing_indexes].copy(),
            missing_path,
        )

    else:
        try:
            if os.path.exists(missing_path):
                os.remove(missing_path)

        except Exception:
            pass

    # ========================================================
    # REPORT
    # ========================================================

    elapsed = time.time() - start_time

    report = {
        "input_file": file_path,
        "result_file": str(result_path),
        "missing_file": (str(missing_path) if missing_indexes else None),
        "total_rows": total_rows,
        "processed": processed,
        "skipped": skipped,
        "cache_hits": cache_hits,
        "json_hits": json_hits,
        "real_searches": searches,
        "found": found,
        "missing": missing,
        "recoveries": recovery_count,
        "elapsed_seconds": round(
            elapsed,
            2,
        ),
        "finished_at": now_iso(),
    }

    save_json_atomic(
        report,
        report_path,
    )

    # ========================================================
    # FINAL OUTPUT
    # ========================================================

    print("\n" + "=" * 75)

    print("GOOGLE MAPS TOOL FINISHED")

    print("=" * 75)

    print(f"📊 Total      : {total_rows}")

    print(f"⚡ Skipped    : {skipped}")

    print(f"💾 Cache      : {cache_hits}")

    print(f"📂 JSON       : {json_hits}")

    print(f"🔎 Searches   : {searches}")

    print(f"✅ Found      : {found}")

    print(f"❌ Missing    : {missing}")

    print(f"♻️ Recoveries : {recovery_count}")

    print(f"⏱️ Time       : {elapsed:.2f}s")

    print(f"📁 Result     : {result_path}")

    if missing_indexes:
        print(f"📁 Missing    : {missing_path}")

    print(f"📄 Report     : {report_path}")

    print("=" * 75)

    return report
