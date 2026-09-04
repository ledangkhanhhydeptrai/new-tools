# Google Maps Tool

Tool chuyên tìm và validate Google Maps Place URL.

## Features

- Existing Google Maps URL detection
- Local JSON lookup
- Persistent cache
- Google Maps search
- Place URL validation
- Title/address matching
- Automatic page recovery
- Retry navigation errors
- Excel checkpoint
- Missing records export
- JSON report
- Single-place test mode
- Headless/debug mode

## Structure

google_maps_tool/

├── run.py
├── config.py
├── requirements.txt
├── README.md
│
├── input/
├── output/
├── cache/
├── logs/
│
└── app/
    ├── __init__.py
    ├── browser.py
    ├── search.py
    ├── matcher.py
    ├── validator.py
    ├── recovery.py
    ├── cache.py
    ├── excel.py
    └── utils.py

## Install

pip install -r requirements.txt

playwright install chromium

## Run

python run.py input/hotels_export.xlsx

## Debug browser

python run.py input/hotels_export.xlsx --headless false

## Test one place

python run.py --test "Minh Quân Hotel" "Sa Pa, Lào Cai"

## Output

google_maps_result.xlsx
google_maps_missing.xlsx
google_maps_report.json

## Cache

cache/google_maps_cache.json

## Logs

logs/google_maps.log