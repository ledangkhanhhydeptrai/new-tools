"""
Script tổng hợp số lượng theo từng trạng thái (cột maps_check_status)
trong file hotels_export_google_maps_checked.xlsx

Cách chạy:
    python summarize_status.py [đường_dẫn_file.xlsx]

Nếu không truyền đường dẫn, script sẽ dùng file mặc định bên dưới.
"""

import sys
import pandas as pd

DEFAULT_FILE = "hotels_export_google_maps_checked.xlsx"
STATUS_COLUMN = "maps_check_status"


def summarize(file_path: str, column: str = STATUS_COLUMN) -> None:
    df = pd.read_excel(file_path)

    if column not in df.columns:
        print(f"Không tìm thấy cột '{column}' trong file. Các cột hiện có:")
        print(list(df.columns))
        return

    # Coi các ô trống (NaN) là "CHƯA KIỂM TRA"
    counts = df[column].fillna("CHƯA KIỂM TRA").value_counts()
    total = len(df)

    print(f"Tổng số dòng: {total}\n")
    print(f"{'Trạng thái':<20}{'Số lượng':>10}{'Tỷ lệ':>10}")
    print("-" * 40)
    for status, count in counts.items():
        pct = count / total * 100
        print(f"{status:<20}{count:>10}{pct:>9.1f}%")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE
    summarize(path)
