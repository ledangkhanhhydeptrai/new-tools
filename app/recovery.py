# ============================================================
# app/recovery.py
# ============================================================

import random
import time

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

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
# ERROR HELPERS
# ============================================================


def is_retryable_error(error):
    """
    Kiểm tra navigation error có nên retry hay không.
    """

    message = str(error)

    if not message:
        return False

    message_lower = message.lower()

    return any(token.lower() in message_lower for token in RETRYABLE_ERRORS)


# ============================================================
# PAGE CONFIGURATION
# ============================================================


def configure_page(page):
    """
    Cấu hình timeout cho Playwright page.
    """

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
    Tạo page mới an toàn.
    """

    try:
        page = context.new_page()

        configure_page(page)

        if logger:
            logger.info("Created new Playwright page")

        return page

    except Exception as error:
        if logger:
            logger.error(
                "Cannot create new page: %s",
                str(error)[:300],
            )

        raise


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

            if logger:
                logger.info("Closed Playwright page")

    except Exception as error:
        if logger:
            logger.debug(
                "Close page ignored: %s",
                str(error)[:200],
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
    Đóng page hiện tại và tạo page mới.

    Không để exception từ page cũ làm chết worker.
    """

    close_page_safely(
        page,
        logger=logger,
    )

    delay = random.uniform(
        PAGE_RECREATE_DELAY_MIN,
        PAGE_RECREATE_DELAY_MAX,
    )

    if logger:
        logger.info(
            "Recreating page | delay=%.2fs",
            delay,
        )

    time.sleep(delay)

    try:
        return create_new_page(
            context,
            logger=logger,
        )

    except Exception as error:
        if logger:
            logger.error(
                "Page recreation failed: %s",
                str(error)[:300],
            )

        raise


# ============================================================
# RETRY DELAY
# ============================================================


def retry_delay(attempt):
    """
    Exponential backoff + jitter.
    """

    delay = RETRY_DELAY_BASE * (2 ** max(0, attempt - 1))

    delay = min(
        delay,
        RETRY_DELAY_MAX,
    )

    delay *= random.uniform(
        0.8,
        1.2,
    )

    return delay


# ============================================================
# SAFE GOTO
# ============================================================


def safe_goto(
    page,
    context,
    url,
    logger=None,
    timeout=PAGE_TIMEOUT,
    retries=MAX_RETRIES,
):
    """
    Navigate tới URL một cách an toàn.

    RETURN:
        (
            current_page,
            success,
            attempts
        )

    success=True:
        Navigation thành công.

    success=False:
        Đã thử hết retry và page mới được tạo.

    Quan trọng:
        Không để một URL làm treo toàn bộ scraper.
    """

    if not url:
        return (
            page,
            False,
            0,
        )

    current_page = page

    # --------------------------------------------------------
    # NORMALIZE RETRIES
    # --------------------------------------------------------

    try:
        retries = max(
            1,
            int(retries),
        )
    except Exception:
        retries = 1

    # --------------------------------------------------------
    # NORMALIZE TIMEOUT
    # --------------------------------------------------------

    try:
        timeout = max(
            1,
            int(timeout),
        )
    except Exception:
        timeout = PAGE_TIMEOUT

    # --------------------------------------------------------
    # ATTEMPTS
    # --------------------------------------------------------

    for attempt in range(
        1,
        retries + 1,
    ):
        # ====================================================
        # CHECK PAGE
        # ====================================================

        try:
            if current_page is None:
                current_page = create_new_page(
                    context,
                    logger=logger,
                )

            if current_page.is_closed():
                if logger:
                    logger.warning(
                        "Current page already closed | attempt=%s/%s",
                        attempt,
                        retries,
                    )

                current_page = create_new_page(
                    context,
                    logger=logger,
                )

        except Exception as error:
            if logger:
                logger.warning(
                    "Page validation failed | attempt=%s/%s | error=%s",
                    attempt,
                    retries,
                    str(error)[:250],
                )

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

        # ====================================================
        # CONFIGURE PAGE
        # ====================================================

        try:
            configure_page(current_page)
        except Exception:
            pass

        # ====================================================
        # LOG START
        # ====================================================

        started_at = time.monotonic()

        if logger:
            logger.info(
                "Navigation start | attempt=%s/%s | timeout=%sms | url=%s",
                attempt,
                retries,
                timeout,
                url,
            )

        # ====================================================
        # NAVIGATION
        # ====================================================

        try:
            current_page.goto(
                url,
                wait_until="commit",
                timeout=timeout,
            )

            elapsed = time.monotonic() - started_at

            if logger:
                logger.info(
                    "Navigation success | attempt=%s/%s | %.2fs | url=%s",
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
                    "Navigation TIMEOUT | attempt=%s/%s | %.2fs | url=%s",
                    attempt,
                    retries,
                    elapsed,
                    url,
                )

            # ------------------------------------------------
            # LAST ATTEMPT
            # ------------------------------------------------

            if attempt >= retries:
                if logger:
                    logger.warning(
                        "Navigation failed after all retries | recreate page | url=%s",
                        url,
                    )

                try:
                    current_page = recreate_page(
                        current_page,
                        context,
                        logger=logger,
                    )

                except Exception as recreate_error:
                    if logger:
                        logger.error(
                            "Final page recreation failed | error=%s",
                            str(recreate_error)[:250],
                        )

                return (
                    current_page,
                    False,
                    attempt,
                )

            # ------------------------------------------------
            # WAIT BEFORE RETRY
            # ------------------------------------------------

            delay = retry_delay(attempt)

            if logger:
                logger.info(
                    "Retrying navigation | next_attempt=%s/%s | delay=%.2fs",
                    attempt + 1,
                    retries,
                    delay,
                )

            time.sleep(delay)

            # ------------------------------------------------
            # RECREATE PAGE
            # ------------------------------------------------

            try:
                current_page = recreate_page(
                    current_page,
                    context,
                    logger=logger,
                )

            except Exception as recreate_error:
                if logger:
                    logger.warning(
                        "Page recreation failed | error=%s",
                        str(recreate_error)[:250],
                    )

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

        # ====================================================
        # OTHER ERRORS
        # ====================================================

        except Exception as error:
            elapsed = time.monotonic() - started_at

            message = str(error)

            if logger:
                logger.warning(
                    "Navigation ERROR "
                    "| attempt=%s/%s "
                    "| %.2fs "
                    "| retryable=%s "
                    "| error=%s",
                    attempt,
                    retries,
                    elapsed,
                    is_retryable_error(error),
                    message[:300],
                )

            # ------------------------------------------------
            # NON-RETRYABLE
            # ------------------------------------------------

            if not is_retryable_error(error):
                if logger:
                    logger.warning(
                        "Non-retryable navigation error | skip URL=%s",
                        url,
                    )

                return (
                    current_page,
                    False,
                    attempt,
                )

            # ------------------------------------------------
            # LAST ATTEMPT
            # ------------------------------------------------

            if attempt >= retries:
                if logger:
                    logger.warning(
                        "Retryable navigation error "
                        "but retries exhausted "
                        "| recreate page "
                        "| url=%s",
                        url,
                    )

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

            # ------------------------------------------------
            # RETRY
            # ------------------------------------------------

            delay = retry_delay(attempt)

            if logger:
                logger.info(
                    "Retryable error | retry in %.2fs | next_attempt=%s/%s",
                    delay,
                    attempt + 1,
                    retries,
                )

            time.sleep(delay)

            try:
                current_page = recreate_page(
                    current_page,
                    context,
                    logger=logger,
                )

            except Exception as recreate_error:
                if logger:
                    logger.warning(
                        "Page recreation failed | error=%s",
                        str(recreate_error)[:250],
                    )

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

    # ========================================================
    # FALLBACK
    # ========================================================

    if logger:
        logger.warning(
            "safe_goto exhausted | url=%s",
            url,
        )

    return (
        current_page,
        False,
        retries,
    )
