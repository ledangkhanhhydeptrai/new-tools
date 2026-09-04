# ============================================================
# app/excel.py
# ============================================================

import json
import os
import time

import pandas as pd

from config import (
    TITLE_COLUMNS,
    ADDRESS_COLUMNS,
    URL_COLUMNS,
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

from .helpers import (
    find_column,
    find_google_maps_column,
)

from .matcher import find_best_match

from .search import GoogleMapsSearchEngine

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
# COLUMN
# ============================================================


def find_missing_reason_column(df):
    """
    Tìm column missing/status một cách linh hoạt.

    Hỗ trợ:
        missing_reason
        Missing Reason
        MissingReason
        reason
        status
    """

    normalized_columns = {}

    for column in df.columns:
        key = normalize_name(column)
        normalized_columns[key] = column

    candidates = (
        "missingreason",
        "reason",
        "status",
    )

    for candidate in candidates:
        if candidate in normalized_columns:
            return normalized_columns[candidate]

    return None


# ============================================================
# GOOGLE MAPS STATE
# ============================================================


def normalize_maps_value(value):
    """
    Chuẩn hóa Google Maps URL.

    Trả về:
        Google Maps URL sạch nếu hợp lệ.
        "" nếu không hợp lệ.

    SOURCE OF TRUTH:
        Chỉ cần là Google Maps URL hợp lệ.
        Không bắt buộc /maps/place/.
    """

    text = safe_text(value)

    if not text:
        return ""

    try:
        cleaned = clean_google_maps_url(text)
    except Exception:
        cleaned = text.strip()

    cleaned = safe_text(cleaned)

    if not cleaned:
        return ""

    if is_google_maps_url(cleaned):
        return cleaned

    return ""


def has_google_maps(value):
    """
    Kiểm tra row có Google Maps URL hay chưa.

    QUY TẮC:
        Có Google Maps URL
            -> FOUND

        Không có
            -> có thể MISSING

    Không bắt buộc URL phải là /maps/place/.
    """

    return bool(normalize_maps_value(value))


# ============================================================
# MISSING REASON
# ============================================================


def clear_missing_reason_for_found(
    df,
    google_maps_col,
):
    """
    Nếu row đã có Google Maps URL:

        -> clear missing reason

    Đây là SOURCE OF TRUTH cuối cùng.
    """

    reason_col = find_missing_reason_column(df)

    if not reason_col:
        return

    maps_mask = df[google_maps_col].apply(has_google_maps)

    df.loc[
        maps_mask,
        reason_col,
    ] = ""


def set_missing_reason(
    df,
    index,
    google_maps_col,
    reason="",
):
    """
    Đồng bộ missing reason của một row.

    Nếu đã có Google Maps:
        -> LUÔN clear reason.

    Nếu chưa có Google Maps:
        -> có thể ghi reason.
    """

    reason_col = find_missing_reason_column(df)

    if not reason_col:
        return

    maps_url = normalize_maps_value(
        df.at[
            index,
            google_maps_col,
        ]
    )

    if maps_url:
        df.at[
            index,
            reason_col,
        ] = ""

        return

    if reason:
        df.at[
            index,
            reason_col,
        ] = safe_text(reason)


def get_missing_mask(
    df,
    google_maps_col,
):
    """
    Tạo mask MISSING trực tiếp từ google_maps.

    google_maps có:
        -> False

    google_maps không có:
        -> True
    """

    return ~df[google_maps_col].apply(has_google_maps)


def get_missing_dataframe(
    df,
    google_maps_col,
):
    """
    Trả về DataFrame chỉ chứa row
    thực sự chưa có Google Maps URL.
    """

    mask = get_missing_mask(
        df,
        google_maps_col,
    )

    return df.loc[mask].copy()


def get_missing_indexes(
    df,
    google_maps_col,
):
    """
    Lấy index các row thực sự MISSING.
    """

    mask = get_missing_mask(
        df,
        google_maps_col,
    )

    return list(df.index[mask])


# ============================================================
# JSON LOOKUP
# ============================================================


def load_json_lookup(
    base_dir,
    logger=None,
):
    """
    Load các JSON hiện có trong folder Excel.

    Mục đích:
        Tận dụng Maps URL đã crawl trước đó.
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

    json_files = []

    for filename in os.listdir(base_dir):
        if filename.lower().endswith(".json"):
            json_files.append(
                os.path.join(
                    base_dir,
                    filename,
                )
            )

    total = 0

    for json_path in json_files:
        try:
            with open(
                json_path,
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

        except Exception as error:
            if logger:
                logger.warning(f"Cannot read JSON {json_path}: {error}")

            continue

        # ----------------------------------------------------
        # Normalize possible structures
        # ----------------------------------------------------

        if isinstance(data, dict):
            candidates = [
                data.get("data"),
                data.get("results"),
                data.get("items"),
                data.get("businesses"),
                data.get("hotels"),
                data.get("places"),
            ]

            data = next(
                (
                    item
                    for item in candidates
                    if isinstance(
                        item,
                        list,
                    )
                ),
                [],
            )

        if not isinstance(
            data,
            list,
        ):
            continue

        # ----------------------------------------------------
        # Records
        # ----------------------------------------------------

        for record in data:
            if not isinstance(
                record,
                dict,
            ):
                continue

            title = safe_text(
                record.get("title")
                or record.get("name")
                or record.get("hotel_name")
                or record.get("business_name")
            )

            address = safe_text(
                record.get("address")
                or record.get("full_address")
                or record.get("location")
            )

            maps_url = safe_text(
                record.get("google_maps_url")
                or record.get("maps_url")
                or record.get("google_map_url")
                or record.get("map_url")
                or record.get("url")
            )

            maps_url = normalize_maps_value(maps_url)

            if not maps_url:
                continue

            total += 1

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

            if combined != "|":
                lookup["combined"].setdefault(
                    combined,
                    maps_url,
                )

            if n_title:
                lookup["title"].setdefault(
                    n_title,
                    maps_url,
                )

            if n_address:
                lookup["address"].setdefault(
                    n_address,
                    maps_url,
                )

    if logger:
        logger.info(f"JSON lookup loaded: {total} Maps URLs")

    return lookup


# ============================================================
# SAVE EXCEL
# ============================================================


def safe_save_excel(
    df,
    output_path,
):
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
# SAVE MISSING
# ============================================================


def safe_save_missing(
    df,
    google_maps_col,
    missing_path,
):
    """
    Lưu danh sách MISSING.

    SOURCE OF TRUTH:
        google_maps

    Có google_maps:
        -> không missing

    Không có google_maps:
        -> missing
    """

    clear_missing_reason_for_found(
        df,
        google_maps_col,
    )

    missing_df = get_missing_dataframe(
        df,
        google_maps_col,
    )

    if len(missing_df) == 0:
        try:
            if os.path.exists(missing_path):
                os.remove(missing_path)

        except Exception:
            pass

        return 0

    safe_save_excel(
        missing_df,
        missing_path,
    )

    return len(missing_df)


# ============================================================
# APPLY MAPS URL
# ============================================================


def apply_google_maps_url(
    df,
    index,
    google_maps_col,
    url_col,
    maps_url,
    existing_url="",
):
    """
    Ghi Google Maps URL vào DataFrame.

    Khi ghi thành công:

        google_maps = URL

        missing_reason = ""

    Google Maps là SOURCE OF TRUTH.
    """

    maps_url = normalize_maps_value(maps_url)

    if not maps_url:
        return False

    # --------------------------------------------------------
    # GOOGLE MAPS
    # --------------------------------------------------------

    df.at[
        index,
        google_maps_col,
    ] = maps_url

    # --------------------------------------------------------
    # URL
    #
    # Nếu URL hiện tại chưa phải Google Maps
    # thì ghi Maps URL vào URL.
    #
    # Nếu URL hiện tại là website chính thức
    # thì giữ nguyên.
    # --------------------------------------------------------

    if not is_google_maps_url(existing_url):
        df.at[
            index,
            url_col,
        ] = maps_url

    # --------------------------------------------------------
    # CLEAR MISSING REASON
    # --------------------------------------------------------

    set_missing_reason(
        df=df,
        index=index,
        google_maps_col=google_maps_col,
        reason="",
    )

    return True


# ============================================================
# SEARCH RESULT NORMALIZATION
# ============================================================


def normalize_search_result(
    result,
):
    """
    Chuẩn hóa kết quả từ search.py.

    SOURCE OF TRUTH:

        Nếu tìm thấy Maps URL
            -> luôn coi là success.

    Không phụ thuộc success flag
    của search.py.
    """

    if not isinstance(
        result,
        dict,
    ):
        return {}

    maps_url = normalize_maps_value(
        result.get("google_maps_url")
        or result.get("url")
        or result.get("maps_url")
        or result.get("google_map_url")
    )

    if maps_url:
        result["google_maps_url"] = maps_url

        result["url"] = maps_url

        result["success"] = True

        # Nếu search.py chưa set reason
        if not safe_text(result.get("reason")):
            result["reason"] = "GOOGLE_MAPS_URL"

    return result


# ============================================================
# GET SEARCH FAILURE REASON
# ============================================================


def get_search_failure_reason(
    result,
):
    """
    Lấy reason chính xác từ search result.

    Ưu tiên:

        1. reason
        2. error
        3. SEARCH_FAILED
    """

    if not isinstance(
        result,
        dict,
    ):
        return "SEARCH_FAILED"

    reason = safe_text(result.get("reason"))

    if reason:
        return reason

    error = safe_text(result.get("error"))

    if error:
        return error

    return "SEARCH_FAILED"


# ============================================================
# PROCESS
# ============================================================


def process_excel(
    file_path,
    headless=True,
    logger=None,
):
    """
    Main Google Maps Excel processor.

    SOURCE OF TRUTH:

        google_maps

    Nếu row có google_maps:
        -> FOUND
        -> không search
        -> không MISSING

    Nếu row không có google_maps:
        -> mới search
        -> nếu không tìm được -> MISSING
    """

    file_path = os.path.abspath(str(file_path))

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # ========================================================
    # LOAD
    # ========================================================

    print()
    print("=" * 75)
    print("GOOGLE MAPS TOOL")
    print("=" * 75)

    print(f"📄 Input: {file_path}")

    df = pd.read_excel(file_path)

    print(f"📊 Rows: {len(df)}")

    # ========================================================
    # COLUMNS
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

    google_maps_col = find_google_maps_column(df)

    if not title_col:
        raise ValueError("Cannot find title/name column.")

    if not address_col:
        raise ValueError("Cannot find address column.")

    # --------------------------------------------------------
    # URL column
    # --------------------------------------------------------

    if not url_col:
        url_col = "url"

        df[url_col] = ""

    # --------------------------------------------------------
    # Google Maps column
    # --------------------------------------------------------

    if not google_maps_col:
        google_maps_col = "google_maps_url"

        df[google_maps_col] = ""

    # ========================================================
    # NORMALIZE EXISTING GOOGLE MAPS
    # ========================================================

    normalized_existing_maps = df[google_maps_col].apply(normalize_maps_value)

    df[google_maps_col] = normalized_existing_maps

    # ========================================================
    # URL COLUMN -> GOOGLE MAPS
    #
    # Nếu URL ban đầu đã là Google Maps
    # nhưng google_maps đang rỗng,
    # chuyển nó sang google_maps.
    # ========================================================

    for index in df.index:
        existing_maps = normalize_maps_value(
            df.at[
                index,
                google_maps_col,
            ]
        )

        if existing_maps:
            continue

        existing_url = safe_text(
            df.at[
                index,
                url_col,
            ]
        )

        existing_url_maps = normalize_maps_value(existing_url)

        if existing_url_maps:
            df.at[
                index,
                google_maps_col,
            ] = existing_url_maps

    # ========================================================
    # CLEAR OLD MISSING REASON
    # ========================================================

    clear_missing_reason_for_found(
        df,
        google_maps_col,
    )

    # ========================================================
    # OUTPUT
    # ========================================================

    input_path = os.path.splitext(str(file_path))[0]

    result_path = input_path + RESULT_SUFFIX

    missing_path = input_path + MISSING_SUFFIX

    report_path = input_path + REPORT_SUFFIX

    # ========================================================
    # CACHE
    # ========================================================

    cache_path = CACHE_DIR / CACHE_FILE_NAME

    cache = GoogleMapsCache(cache_path)

    print(f"💾 Cache entries: {len(cache)}")

    # ========================================================
    # JSON LOOKUP
    # ========================================================

    json_lookup = load_json_lookup(
        os.path.dirname(str(file_path)),
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
    # STATS
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

    start_time = time.time()

    try:
        # ====================================================
        # LOOP
        # ====================================================

        for index in df.index:
            processed += 1

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

            existing_maps = normalize_maps_value(
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

            # =================================================
            # PRINT
            # =================================================

            print()

            print("-" * 75)

            print(f"[{processed}/{total_rows}] {title}")

            print(f"📍 {address}")

            # =================================================
            # 1. EXISTING GOOGLE MAPS
            # =================================================

            if existing_maps:
                df.at[
                    index,
                    google_maps_col,
                ] = existing_maps

                set_missing_reason(
                    df=df,
                    index=index,
                    google_maps_col=google_maps_col,
                    reason="",
                )

                skipped += 1

                print("⚡ Existing google_maps -> SKIP")

                print(f"🔗 {existing_maps}")

                continue

            # =================================================
            # 2. URL COLUMN IS GOOGLE MAPS
            # =================================================

            existing_url_maps = normalize_maps_value(existing_url)

            if existing_url_maps:
                df.at[
                    index,
                    google_maps_col,
                ] = existing_url_maps

                set_missing_reason(
                    df=df,
                    index=index,
                    google_maps_col=google_maps_col,
                    reason="",
                )

                skipped += 1

                print("⚡ Existing URL is Google Maps -> SKIP")

                print(f"🔗 {existing_url_maps}")

                continue

            # =================================================
            # 3. GOOGLE MAPS CACHE
            # =================================================

            cached = cache.get(
                title,
                address,
            )

            if cached:
                maps_url = normalize_maps_value(
                    cached.get("google_maps_url")
                    or cached.get("url")
                    or cached.get("maps_url")
                    or cached.get("google_map_url")
                )

                if maps_url:
                    apply_google_maps_url(
                        df=df,
                        index=index,
                        google_maps_col=google_maps_col,
                        url_col=url_col,
                        maps_url=maps_url,
                        existing_url=existing_url,
                    )

                    found += 1
                    cache_hits += 1

                    print("💾 Cache HIT")

                    print(f"🔗 {maps_url}")

                    continue

            # =================================================
            # 4. JSON LOOKUP
            # =================================================

            match = find_best_match(
                title,
                address,
                json_lookup,
            )

            if match:
                maps_url = normalize_maps_value(
                    match.get(
                        "url",
                        "",
                    )
                )

                if maps_url:
                    apply_google_maps_url(
                        df=df,
                        index=index,
                        google_maps_col=google_maps_col,
                        url_col=url_col,
                        maps_url=maps_url,
                        existing_url=existing_url,
                    )

                    found += 1
                    json_hits += 1

                    cache.set(
                        title,
                        address,
                        maps_url,
                        confidence="HIGH",
                        name_score=100,
                        address_score=100,
                        method=match.get(
                            "method",
                            "JSON",
                        ),
                    )

                    print(f"💾 JSON MATCH -> {match.get('method')}")

                    print(f"🔗 {maps_url}")

                    continue

            # =================================================
            # 5. REAL GOOGLE MAPS SEARCH
            # =================================================

            searches += 1

            print("🔎 REAL GOOGLE MAPS SEARCH")

            old_page = page

            search_engine.update_page(page)

            # -------------------------------------------------
            # SEARCH
            # -------------------------------------------------

            result = search_engine.search(
                title,
                address,
            )

            # -------------------------------------------------
            # DEBUG RESULT
            # -------------------------------------------------

            if isinstance(
                result,
                dict,
            ):
                print("🔍 SEARCH RESULT:")

                print(f"   success={result.get('success')}")

                print(f"   url={result.get('url')}")

                print(f"   google_maps_url={result.get('google_maps_url')}")

                print(f"   reason={result.get('reason')}")

                print(f"   error={result.get('error')}")

            # =================================================
            # NORMALIZE SEARCH RESULT
            # =================================================

            result = normalize_search_result(result)

            # =================================================
            # UPDATE PAGE
            # =================================================

            page = result.get(
                "page",
                search_engine.page,
            )

            search_engine.update_page(page)

            if page is not old_page:
                recovery_count += 1

            # =================================================
            # GET MAPS URL AGAIN
            # =================================================

            maps_url = normalize_maps_value(
                result.get(
                    "google_maps_url",
                    "",
                )
            )

            attempts = result.get(
                "attempts",
                0,
            )

            # =================================================
            # SUCCESS
            #
            # IMPORTANT:
            #
            # Không kiểm tra:
            #
            #     result["success"]
            #
            # Chỉ cần maps_url.
            # =================================================

            if maps_url:
                applied = apply_google_maps_url(
                    df=df,
                    index=index,
                    google_maps_col=google_maps_col,
                    url_col=url_col,
                    maps_url=maps_url,
                    existing_url=existing_url,
                )

                if applied:
                    found += 1

                    cache.set(
                        title,
                        address,
                        maps_url,
                        confidence="HIGH",
                        name_score=result.get(
                            "title_score",
                            0,
                        ),
                        address_score=result.get(
                            "address_score",
                            result.get(
                                "score",
                                0,
                            ),
                        ),
                        method="SEARCH",
                    )

                    print("✅ FOUND")

                    print(f"🔗 {maps_url}")

                    print(f"🔁 attempts={attempts}")

                    random_delay(
                        SEARCH_DELAY_MIN,
                        SEARCH_DELAY_MAX,
                    )

                else:
                    print("⚠️ Maps URL exists but could not be applied")

            # =================================================
            # FAILED
            # =================================================

            else:
                # =================================================
                # SAFETY CHECK:
                #
                # Search failed nhưng DataFrame có Maps
                # =================================================

                current_maps = normalize_maps_value(
                    df.at[
                        index,
                        google_maps_col,
                    ]
                )

                if current_maps:
                    df.at[
                        index,
                        google_maps_col,
                    ] = current_maps

                    set_missing_reason(
                        df=df,
                        index=index,
                        google_maps_col=google_maps_col,
                        reason="",
                    )

                    print(
                        "⚠️ Search returned empty "
                        "but google_maps already exists "
                        "-> NOT MISSING"
                    )

                    print(f"🔗 {current_maps}")

                else:
                    # -------------------------------------------------
                    # IMPORTANT:
                    #
                    # Đọc reason thực tế từ search.py.
                    #
                    # Không còn biến mọi lỗi thành SEARCH_FAILED.
                    # -------------------------------------------------

                    missing += 1

                    reason = get_search_failure_reason(result)

                    set_missing_reason(
                        df=df,
                        index=index,
                        google_maps_col=google_maps_col,
                        reason=reason,
                    )

                    print("❌ MISSING")

                    print(f"   reason={reason}")

                    random_delay(
                        FAILED_SEARCH_DELAY_MIN,
                        FAILED_SEARCH_DELAY_MAX,
                    )

            # =================================================
            # PERIODIC PAGE RESET
            # =================================================

            if searches > 0 and searches % RESET_PAGE_EVERY_SEARCHES == 0:
                print()

                print(f"♻️ Periodic Page reset after {searches} searches")

                page = browser.recreate_page()

                search_engine.update_page(page)

                recovery_count += 1

            # =================================================
            # CHECKPOINT
            # =================================================

            if processed % CHECKPOINT_EVERY_ROWS == 0:
                print()

                print(f"💾 CHECKPOINT row={processed}")

                # -------------------------------------------------
                # Normalize Maps
                # -------------------------------------------------

                df[google_maps_col] = df[google_maps_col].apply(normalize_maps_value)

                # -------------------------------------------------
                # Clear reason for rows with Maps
                # -------------------------------------------------

                clear_missing_reason_for_found(
                    df,
                    google_maps_col,
                )

                # -------------------------------------------------
                # Save result
                # -------------------------------------------------

                safe_save_excel(
                    df,
                    result_path,
                )

                # -------------------------------------------------
                # Save cache
                # -------------------------------------------------

                cache.save()

                # -------------------------------------------------
                # Recalculate missing
                # -------------------------------------------------

                current_missing_count = safe_save_missing(
                    df,
                    google_maps_col,
                    missing_path,
                )

                print(f"📊 Current missing: {current_missing_count}")

    # ========================================================
    # CTRL+C
    # ========================================================

    except KeyboardInterrupt:
        print()

        print("🛑 CTRL+C detected.")

        print("💾 Saving emergency checkpoint...")

        # ----------------------------------------------------
        # Normalize
        # ----------------------------------------------------

        df[google_maps_col] = df[google_maps_col].apply(normalize_maps_value)

        # ----------------------------------------------------
        # Clear found reasons
        # ----------------------------------------------------

        clear_missing_reason_for_found(
            df,
            google_maps_col,
        )

        # ----------------------------------------------------
        # Save Excel
        # ----------------------------------------------------

        safe_save_excel(
            df,
            result_path,
        )

        # ----------------------------------------------------
        # Save cache
        # ----------------------------------------------------

        cache.save()

        # ----------------------------------------------------
        # Save missing
        # ----------------------------------------------------

        current_missing_count = safe_save_missing(
            df,
            google_maps_col,
            missing_path,
        )

        print(f"📊 Current missing: {current_missing_count}")

        raise

    # ========================================================
    # UNEXPECTED ERROR
    # ========================================================

    except Exception as error:
        if logger:
            logger.exception("Unexpected processing error")

        print()

        print(f"❌ Unexpected error: {error}")

        print("💾 Saving emergency checkpoint...")

        # ----------------------------------------------------
        # Normalize
        # ----------------------------------------------------

        df[google_maps_col] = df[google_maps_col].apply(normalize_maps_value)

        # ----------------------------------------------------
        # Clear found reasons
        # ----------------------------------------------------

        clear_missing_reason_for_found(
            df,
            google_maps_col,
        )

        # ----------------------------------------------------
        # Save Excel
        # ----------------------------------------------------

        safe_save_excel(
            df,
            result_path,
        )

        # ----------------------------------------------------
        # Save cache
        # ----------------------------------------------------

        cache.save()

        # ----------------------------------------------------
        # Save missing
        # ----------------------------------------------------

        current_missing_count = safe_save_missing(
            df,
            google_maps_col,
            missing_path,
        )

        print(f"📊 Current missing: {current_missing_count}")

        raise

    finally:
        browser.close()

    # ========================================================
    # FINAL NORMALIZATION
    # ========================================================

    df[google_maps_col] = df[google_maps_col].apply(normalize_maps_value)

    # ========================================================
    # FINAL MISSING REASON CLEANUP
    # ========================================================

    clear_missing_reason_for_found(
        df,
        google_maps_col,
    )

    # ========================================================
    # FINAL SAVE
    # ========================================================

    safe_save_excel(
        df,
        result_path,
    )

    cache.save()

    # ========================================================
    # FINAL MISSING CALCULATION
    # ========================================================

    final_missing_df = get_missing_dataframe(
        df,
        google_maps_col,
    )

    final_missing_indexes = list(final_missing_df.index)

    missing = len(final_missing_df)

    # ========================================================
    # SAVE / REMOVE MISSING FILE
    # ========================================================

    if len(final_missing_df) > 0:
        safe_save_excel(
            final_missing_df,
            missing_path,
        )

    else:
        try:
            if os.path.exists(missing_path):
                os.remove(missing_path)

        except Exception:
            pass

    # ========================================================
    # FINAL FOUND
    # ========================================================

    final_has_maps_mask = df[google_maps_col].apply(has_google_maps)

    final_found_count = int(final_has_maps_mask.sum())

    # ========================================================
    # REPORT
    # ========================================================

    elapsed = time.time() - start_time

    report = {
        "input_file": file_path,
        "result_file": str(result_path),
        "missing_file": (str(missing_path) if len(final_missing_df) > 0 else None),
        "total_rows": total_rows,
        "processed": processed,
        "skipped": skipped,
        "cache_hits": cache_hits,
        "json_hits": json_hits,
        "real_searches": searches,
        # ---------------------------------------------
        # FINAL SOURCE OF TRUTH
        # ---------------------------------------------
        "found": final_found_count,
        "missing": len(final_missing_df),
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
    # SUMMARY
    # ========================================================

    print()

    print("=" * 75)

    print("GOOGLE MAPS TOOL FINISHED")

    print("=" * 75)

    print(f"📊 Total       : {total_rows}")

    print(f"⚡ Skipped     : {skipped}")

    print(f"💾 Cache       : {cache_hits}")

    print(f"📂 JSON        : {json_hits}")

    print(f"🔎 Searches    : {searches}")

    print(f"✅ Found       : {final_found_count}")

    print(f"❌ Missing     : {len(final_missing_df)}")

    print(f"♻️ Recoveries  : {recovery_count}")

    print(f"⏱️ Time        : {elapsed:.2f}s")

    print()

    print("📁 Result:")

    print(f"   {result_path}")

    if len(final_missing_df) > 0:
        print()

        print("📁 Missing:")

        print(f"   {missing_path}")

    print()

    print("📄 Report:")

    print(f"   {report_path}")

    print("=" * 75)

    return report
