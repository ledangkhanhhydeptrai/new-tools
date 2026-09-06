# ============================================================
# app/helpers.py
# ============================================================

import re
import pandas as pd


def normalize_name(value) -> str:
    """
    Chuẩn hóa tên column.
    """
    if value is None:
        return ""

    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).strip().lower(),
    )


def find_column(
    df: pd.DataFrame,
    candidates,
    default=None,
):
    """
    Tìm column theo nhiều tên có thể có.

    Ví dụ:
        find_column(
            df,
            ["title", "name", "hotel_name"]
        )
    """

    if df is None or df.empty:
        return default

    normalized_columns = {normalize_name(column): column for column in df.columns}

    for candidate in candidates:
        key = normalize_name(candidate)

        if key in normalized_columns:
            return normalized_columns[key]

    return default


def find_google_maps_column(df):
    """
    Tìm column Google Maps URL.
    """

    return find_column(
        df,
        [
            "google_maps",
            "google map",
            "google maps",
            "google_maps_url",
            "google map url",
            "maps",
            "maps_url",
            "map_url",
        ],
    )
