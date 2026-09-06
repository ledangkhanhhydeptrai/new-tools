# ============================================================
# app/recovery.py
# FAST / STABLE PLAYWRIGHT NAVIGATION
# ============================================================

import random
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import (
    MAX_RETRIES,
    RETRY_DELAY_BASE,
    RETRY_DELAY_MAX,
    PAGE_RECREATE_DELAY_MIN,
    PAGE_RECREATE_DELAY_MAX,
    PAGE_TIMEOUT,
    DEFAULT_PAGE_TIMEOUT,
)


# ============================================================
# RETRYABLE ERRORS
# ============================================================

RETRYABLE_ERRORS = (
    "ERR_ABORTED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_REFUSED",
    "ERR_TIMED_OUT",
    "ERR_NETWORK_CHANGED",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_EMPTY_RESPONSE",
    "Target page, context or browser has been closed",
    "Navigation failed because page was closed",
    "Browser has been closed",
)


# ============================================================
# PERFORMANCE CONFIG
# ============================================================

# Không retry quá nhiều.
FAST_MAX_RETRIES = 2

# Retry rất ngắn.
FAST_RETRY_DELAY_MIN = 0.15
FAST_RETRY_DELAY_MAX = 0.45

# Chỉ recreate page khi thực sự cần.
RECREATE_ONLY_ON_FAILURE = True

# Không log từng navigation thành INFO vì 1000+ record sẽ rất nặng.
LOG_NAVIGATION_SUCCESS = False


# ============================================================
# ERROR HELPERS
# ============================================================


def is_retryable_error(error):
    """
    Kiểm tra navigation error có nên retry không.
    """

    try:
        message = str(error or "")
    except Exception:
        return False

    if not message:
        return False

    message_lower = message.lower()

    return any(token.lower() in message_lower for token in RETRYABLE_ERRORS)


# ============================================================
# PAGE CONFIGURATION
# ============================================================


def configure_page(page):
    """
    Cấu hình timeout cho page.
    """

    if page is None:
        return None

    try:
        page.set_default_navigation_timeout(PAGE_TIMEOUT)
    except Exception:
        pass

    try:
        page.set_default_timeout(DEFAULT_PAGE_TIMEOUT)
    except Exception:
        pass

    return page


# ============================================================
# CREATE PAGE
# ============================================================


def create_new_page(context, logger=None):
    """
    Tạo page mới.
    """

    if context is None:
        raise RuntimeError("Browser context is None")

    page = context.new_page()

    configure_page(page)

    if logger:
        logger.debug("Created new Playwright page")

    return page


# ============================================================
# CLOSE PAGE
# ============================================================


def close_page_safely(page, logger=None):
    """
    Đóng page an toàn.
    """

    if page is None:
        return

    try:
        if not page.is_closed():
            page.close()
    except Exception as error:
        if logger:
            logger.debug(
                "Close page ignored: %s",
                str(error)[:150],
            )


# ============================================================
# RECREATE PAGE
# ============================================================


def recreate_page(
    page,
    context,
    logger=None,
):
    """
    Recreate page.

    Chỉ gọi khi page thực sự có vấn đề.
    """

    close_page_safely(
        page,
        logger=logger,
    )

    # Delay rất ngắn.
    try:
        minimum = float(PAGE_RECREATE_DELAY_MIN or 0.05)

        maximum = float(PAGE_RECREATE_DELAY_MAX or 0.20)

        minimum = max(0.05, minimum)
        maximum = max(minimum, maximum)

        delay = random.uniform(
            minimum,
            maximum,
        )

        # Không cho delay quá 0.5s.
        delay = min(delay, 0.5)

    except Exception:
        delay = 0.1

    if delay > 0:
        time.sleep(delay)

    return create_new_page(
        context,
        logger=logger,
    )


# ============================================================
# RETRY DELAY
# ============================================================


def retry_delay(attempt):
    """
    Backoff cực ngắn.

    attempt 1 -> ~0.15-0.25s
    attempt 2 -> ~0.25-0.45s
    """

    try:
        attempt = max(
            1,
            int(attempt),
        )
    except Exception:
        attempt = 1

    try:
        base = float(RETRY_DELAY_BASE or FAST_RETRY_DELAY_MIN)
    except Exception:
        base = FAST_RETRY_DELAY_MIN

    try:
        maximum = float(RETRY_DELAY_MAX or FAST_RETRY_DELAY_MAX)
    except Exception:
        maximum = FAST_RETRY_DELAY_MAX

    base = max(
        FAST_RETRY_DELAY_MIN,
        min(base, 0.30),
    )

    maximum = max(
        base,
        min(maximum, FAST_RETRY_DELAY_MAX),
    )

    delay = base * (1.5 ** max(0, attempt - 1))

    delay = min(
        delay,
        maximum,
    )

    delay *= random.uniform(
        0.85,
        1.15,
    )

    return min(
        delay,
        FAST_RETRY_DELAY_MAX,
    )


# ============================================================
# PAGE HEALTH
# ============================================================


def _page_is_healthy(page):
    """
    Kiểm tra page còn dùng được không.
    """

    if page is None:
        return False

    try:
        return not page.is_closed()
    except Exception:
        return False


# ============================================================
# FAST GOTO
# ============================================================


def safe_goto(
    page,
    context,
    url,
    logger=None,
    timeout=PAGE_TIMEOUT,
    retries=FAST_MAX_RETRIES,
):
    """
    FAST SAFE GOTO.

    Mục tiêu:
        - Không retry quá nhiều.
        - Không recreate page vô ích.
        - Không sleep dài.
        - Page khỏe thì retry trên cùng page.
        - Chỉ recreate khi page bị hỏng.
    """

    if not url:
        return (
            page,
            False,
            0,
        )

    # --------------------------------------------------------
    # Normalize retries
    # --------------------------------------------------------

    try:
        retries = int(retries)
    except Exception:
        retries = FAST_MAX_RETRIES

    retries = max(
        1,
        min(
            retries,
            FAST_MAX_RETRIES,
        ),
    )

    # --------------------------------------------------------
    # Normalize timeout
    # --------------------------------------------------------

    try:
        timeout = int(timeout)
    except Exception:
        timeout = PAGE_TIMEOUT

    timeout = max(
        1000,
        timeout,
    )

    current_page = page

    # ========================================================
    # ATTEMPTS
    # ========================================================

    for attempt in range(
        1,
        retries + 1,
    ):
        # ----------------------------------------------------
        # Ensure page exists
        # ----------------------------------------------------

        if not _page_is_healthy(current_page):
            try:
                current_page = create_new_page(
                    context,
                    logger=logger,
                )

            except Exception as error:
                if logger:
                    logger.warning(
                        "Cannot create page | error=%s",
                        str(error)[:200],
                    )

                return (
                    current_page,
                    False,
                    attempt,
                )

        # ----------------------------------------------------
        # Configure page
        # ----------------------------------------------------

        configure_page(current_page)

        started_at = time.monotonic()

        # ----------------------------------------------------
        # Navigation
        # ----------------------------------------------------

        try:
            current_page.goto(
                url,
                wait_until="commit",
                timeout=timeout,
            )

            elapsed = time.monotonic() - started_at

            if LOG_NAVIGATION_SUCCESS and logger:
                logger.debug(
                    "Navigation success | attempt=%s/%s | %.2fs | %s",
                    attempt,
                    retries,
                    elapsed,
                    url,
                )

            return (
                current_page,
                True,
                attempt,
            )

        # ====================================================
        # TIMEOUT
        # ====================================================

        except PlaywrightTimeoutError as error:
            elapsed = time.monotonic() - started_at

            if logger:
                logger.warning(
                    "Navigation timeout | attempt=%s/%s | %.2fs | %s",
                    attempt,
                    retries,
                    elapsed,
                    url,
                )

            # ------------------------------------------------
            # Nếu còn retry:
            #
            # KHÔNG recreate page ngay.
            # ------------------------------------------------

            if attempt < retries:
                delay = retry_delay(attempt)

                time.sleep(delay)

                continue

            # ------------------------------------------------
            # Hết retry.
            #
            # Chỉ recreate để trả về page sạch
            # cho record tiếp theo.
            # ------------------------------------------------

            if RECREATE_ONLY_ON_FAILURE:
                try:
                    current_page = recreate_page(
                        current_page,
                        context,
                        logger=logger,
                    )

                except Exception:
                    pass

            return (
                current_page,
                False,
                attempt,
            )

        # ====================================================
        # OTHER ERRORS
        # ====================================================

        except Exception as error:
            elapsed = time.monotonic() - started_at

            retryable = is_retryable_error(error)

            if logger:
                logger.warning(
                    "Navigation error | attempt=%s/%s | %.2fs | retryable=%s | %s",
                    attempt,
                    retries,
                    elapsed,
                    retryable,
                    str(error)[:250],
                )

            # ------------------------------------------------
            # Non-retryable:
            # skip ngay.
            # ------------------------------------------------

            if not retryable:
                return (
                    current_page,
                    False,
                    attempt,
                )

            # ------------------------------------------------
            # Retry
            # ------------------------------------------------

            if attempt < retries:
                delay = retry_delay(attempt)

                time.sleep(delay)

                # --------------------------------------------
                # Chỉ recreate nếu page thực sự chết.
                # --------------------------------------------

                if not _page_is_healthy(current_page):
                    try:
                        current_page = create_new_page(
                            context,
                            logger=logger,
                        )

                    except Exception:
                        return (
                            current_page,
                            False,
                            attempt,
                        )

                continue

            # ------------------------------------------------
            # Hết retry.
            # ------------------------------------------------

            try:
                current_page = recreate_page(
                    current_page,
                    context,
                    logger=logger,
                )

            except Exception:
                pass

            return (
                current_page,
                False,
                attempt,
            )

    # ========================================================
    # FALLBACK
    # ========================================================

    return (
        current_page,
        False,
        retries,
    )
