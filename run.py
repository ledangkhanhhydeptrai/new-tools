# ============================================================
# run.py
# GOOGLE MAPS TOOL
# ============================================================

import argparse
import sys

from config import LOG_DIR, LOG_FILE_NAME

from app.excel import process_excel
from app.browser import GoogleMapsBrowser
from app.search import GoogleMapsSearchEngine
from app.validator import validate_google_maps_url
from app.utils import setup_logger


# ============================================================
# TEST SINGLE
# ============================================================


def test_single(
    title,
    address,
    headless,
    logger,
):
    """
    Test một địa điểm duy nhất.
    """

    print()
    print("=" * 75)
    print("GOOGLE MAPS SINGLE TEST")
    print("=" * 75)

    print(f"🏨 Title  : {title}")

    print(f"📍 Address: {address}")

    browser = GoogleMapsBrowser(
        headless=headless,
        logger=logger,
    )

    browser.start()

    try:
        page = browser.get_page()

        context = browser.get_context()

        engine = GoogleMapsSearchEngine(
            page,
            context,
            logger,
        )

        result = engine.search(
            title,
            address,
        )

        print()
        print("RESULT")

        print(f"Success : {result.get('success')}")

        print(f"Attempts: {result.get('attempts')}")

        print(f"Error   : {result.get('error')}")

        maps_url = result.get(
            "google_maps_url",
            "",
        )

        print(f"Maps URL: {maps_url}")

        print()

        if validate_google_maps_url(maps_url):
            print("✅ VALID GOOGLE MAPS PLACE URL")

        else:
            print("❌ INVALID / NOT PLACE URL")

        return result

    finally:
        browser.close()


# ============================================================
# ARGUMENT PARSER
# ============================================================


def build_parser():
    parser = argparse.ArgumentParser(
        description=("Google Maps URL Finder and Validator")
    )

    parser.add_argument(
        "file",
        nargs="?",
        help=("Excel input file. Example: input/hotels.xlsx"),
    )

    parser.add_argument(
        "--headless",
        default="true",
        choices=[
            "true",
            "false",
        ],
        help=("Run browser headless. Default: true"),
    )

    parser.add_argument(
        "--test",
        nargs=2,
        metavar=(
            "TITLE",
            "ADDRESS",
        ),
        help=("Test one place."),
    )

    return parser


# ============================================================
# MAIN
# ============================================================


def main():
    parser = build_parser()

    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    logger = setup_logger(LOG_DIR / LOG_FILE_NAME)

    # ========================================================
    # SINGLE TEST
    # ========================================================

    if args.test:
        title = args.test[0]
        address = args.test[1]

        test_single(
            title,
            address,
            headless,
            logger,
        )

        return 0

    # ========================================================
    # EXCEL
    # ========================================================

    if not args.file:
        parser.print_help()

        print()
        print("Examples:")

        print("  python run.py input/hotels.xlsx")

        print("  python run.py input/hotels.xlsx --headless false")

        print('  python run.py --test "Minh Quân Hotel" "Sa Pa, Lào Cai"')

        return 1

    try:
        process_excel(
            args.file,
            headless=headless,
            logger=logger,
        )

        return 0

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")

        return 130

    except Exception as error:
        logger.exception("Fatal error")

        print()
        print("❌ FATAL ERROR:")

        print(f"{type(error).__name__}: {error}")

        return 1


if __name__ == "__main__":
    sys.exit(main())
