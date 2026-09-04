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


def is_retryable_error(error):
    message = str(error)

    if not message:
        return False

    message_lower = message.lower()

    return any(token.lower() in message_lower for token in RETRYABLE_ERRORS)


def configure_page(page):
    try:
        page.set_default_navigation_timeout(PAGE_TIMEOUT)
    except Exception:
        pass

    try:
        page.set_default_timeout(DEFAULT_PAGE_TIMEOUT)
    except Exception:
        pass

    return page


def create_new_page(context):
    page = context.new_page()

    return configure_page(page)


def close_page_safely(page):
    if page is None:
        return

    try:
        if not page.is_closed():
            page.close()

    except Exception:
        pass


def recreate_page(
    page,
    context,
):
    close_page_safely(page)

    time.sleep(
        random.uniform(
            PAGE_RECREATE_DELAY_MIN,
            PAGE_RECREATE_DELAY_MAX,
        )
    )

    return create_new_page(context)


def retry_delay(attempt):
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


def safe_goto(
    page,
    context,
    url,
    logger=None,
    timeout=PAGE_TIMEOUT,
    retries=MAX_RETRIES,
):
    if not url:
        return (
            page,
            False,
            0,
        )

    current_page = page

    for attempt in range(
        1,
        retries + 1,
    ):
        try:
            current_page.goto(
                url,
                wait_until="commit",
                timeout=timeout,
            )

            return (
                current_page,
                True,
                attempt,
            )

        except PlaywrightTimeoutError:
            if logger:
                logger.warning(
                    f"Navigation timeout attempt={attempt}/{retries} url={url}"
                )

            if attempt >= retries:
                current_page = recreate_page(
                    current_page,
                    context,
                )

                return (
                    current_page,
                    False,
                    attempt,
                )

            time.sleep(retry_delay(attempt))

            current_page = recreate_page(
                current_page,
                context,
            )

        except Exception as error:
            if logger:
                logger.warning(
                    "Navigation error "
                    f"attempt={attempt}/{retries} "
                    f"error={str(error)[:250]}"
                )

            if not is_retryable_error(error):
                return (
                    current_page,
                    False,
                    attempt,
                )

            if attempt >= retries:
                current_page = recreate_page(
                    current_page,
                    context,
                )

                return (
                    current_page,
                    False,
                    attempt,
                )

            time.sleep(retry_delay(attempt))

            current_page = recreate_page(
                current_page,
                context,
            )

    return (
        current_page,
        False,
        retries,
    )
