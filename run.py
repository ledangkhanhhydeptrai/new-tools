# ============================================================
# run.py
# GOOGLE MAPS TOOL
#
# MULTI-WORKER + SEPARATE POWERSHELL TERMINALS
#
# FLOW:
#
#   VSCode Terminal
#       |
#       |-- MAIN process only
#       |
#       |-- Worker 1 -> PowerShell riêng
#       |-- Worker 2 -> PowerShell riêng
#       |-- Worker 3 -> PowerShell riêng
#       |
#       `-- Merge kết quả
#
# IMPORTANT:
#
#   - app/excel.py KHÔNG CẦN SỬA
#   - Mỗi worker browser riêng
#   - Mỗi worker cache riêng
#   - Worker terminal riêng
#   - Merge đúng thứ tự file gốc
#   - Restore STT từ Excel gốc
#   - Ctrl+C MAIN -> kill toàn bộ worker tree
#
# ============================================================

import argparse
import multiprocessing
import os
import shutil
import subprocess
import sys
import time

from pathlib import Path

import pandas as pd

from config import (
    LOG_DIR,
    LOG_FILE_NAME,
    RESULT_SUFFIX,
    MISSING_SUFFIX,
    REPORT_SUFFIX,
    CACHE_DIR,
    CACHE_FILE_NAME,
)

from app.excel import process_excel

from app.browser import (
    GoogleMapsBrowser,
)

from app.search import (
    GoogleMapsSearchEngine,
)

from app.validator import (
    validate_google_maps_url,
)

from app.utils import (
    setup_logger,
)


# ============================================================
# CONFIG
# ============================================================

DEFAULT_WORKERS = 3

ORIGINAL_INDEX_COLUMN = "__gm_original_index"

WORKER_POLL_INTERVAL = 0.5

WORKER_TERMINATE_TIMEOUT = 3.0


# ============================================================
# SINGLE TEST
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

    # --------------------------------------------------------
    # Normal arguments
    # --------------------------------------------------------

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
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(f"Parallel workers. Default: {DEFAULT_WORKERS}"),
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

    # ========================================================
    # INTERNAL WORKER ARGUMENTS
    # ========================================================

    parser.add_argument(
        "--worker-mode",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--worker-id",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--chunk",
        default=None,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--run-dir",
        default=None,
        help=argparse.SUPPRESS,
    )

    return parser


# ============================================================
# STT HELPERS
# ============================================================


def _normalize_column_name(value):

    return (
        str(value)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace(".", "")
    )


def find_stt_column(df):
    """
    Tìm STT column linh hoạt.
    """

    # --------------------------------------------------------
    # Exact STT first
    # --------------------------------------------------------

    for column in df.columns:
        if str(column).strip().upper() == "STT":
            return column

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    candidates = {
        "stt",
        "sốthứtự",
        "sothutu",
        "sốtt",
        "sott",
        "no",
        "number",
    }

    for column in df.columns:
        normalized = _normalize_column_name(column)

        if normalized in candidates:
            return column

    return None


# ============================================================
# SPLIT EXCEL
# ============================================================


def split_excel(
    input_path,
    run_dir,
    workers,
):
    """
    Chia Excel thành N chunk.

    __gm_original_index giúp restore
    chính xác thứ tự file gốc.
    """

    input_path = Path(input_path)

    run_dir = Path(run_dir)

    split_dir = run_dir / "chunks"

    split_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # READ
    # ========================================================

    df = pd.read_excel(input_path)

    total = len(df)

    if total == 0:
        raise ValueError("Excel file contains no rows.")

    # ========================================================
    # RESERVED COLUMN
    # ========================================================

    if ORIGINAL_INDEX_COLUMN in df.columns:
        raise ValueError(
            f"Input already contains reserved column: {ORIGINAL_INDEX_COLUMN}"
        )

    # ========================================================
    # NUMBER OF WORKERS
    # ========================================================

    workers = max(
        1,
        min(
            int(workers),
            total,
        ),
    )

    # ========================================================
    # ORIGINAL INDEX
    # ========================================================

    df = df.copy()

    df.insert(
        0,
        ORIGINAL_INDEX_COLUMN,
        range(total),
    )

    # ========================================================
    # BALANCED SPLIT
    # ========================================================

    chunks = []

    base_size = total // workers

    remainder = total % workers

    start = 0

    for worker_id in range(workers):
        size = base_size + (1 if worker_id < remainder else 0)

        end = start + size

        chunk_df = df.iloc[start:end].copy()

        chunk_path = split_dir / f"chunk_{worker_id:02d}.xlsx"

        chunk_df.to_excel(
            chunk_path,
            index=False,
            engine="openpyxl",
        )

        chunks.append(
            (
                worker_id,
                chunk_path,
                len(chunk_df),
            )
        )

        start = end

    return (
        chunks,
        total,
    )


# ============================================================
# WORKER FILE PATHS
# ============================================================


def find_worker_result_file(
    chunk_path,
):

    chunk_path = Path(chunk_path)

    return Path(
        str(chunk_path).replace(
            ".xlsx",
            RESULT_SUFFIX,
        )
    )


def find_worker_missing_file(
    chunk_path,
):

    chunk_path = Path(chunk_path)

    return Path(
        str(chunk_path).replace(
            ".xlsx",
            MISSING_SUFFIX,
        )
    )


# ============================================================
# WORKER TERMINAL MODE
# ============================================================


def run_worker_terminal(
    worker_id,
    chunk_path,
    run_dir,
    headless,
):
    """
    Đây là code chạy TRONG
    PowerShell riêng của worker.
    """

    worker_id = int(worker_id)

    chunk_path = Path(chunk_path)

    run_dir = Path(run_dir)

    worker_number = worker_id + 1

    # ========================================================
    # TERMINAL TITLE
    # ========================================================

    if os.name == "nt":
        try:
            os.system(f"title Google Maps Worker {worker_number}")

        except Exception:
            pass

    # ========================================================
    # WORKER DIRECTORY
    # ========================================================

    worker_dir = run_dir / f"worker_{worker_id:02d}"

    worker_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # CLEAN OLD MARKERS
    # ========================================================

    for marker_name in (
        "DONE",
        "FAILED",
    ):
        marker_path = worker_dir / marker_name

        try:
            if marker_path.exists():
                marker_path.unlink()

        except Exception:
            pass

    # ========================================================
    # LOGGER
    # ========================================================

    worker_log_dir = worker_dir / "logs"

    worker_log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    worker_log_path = worker_log_dir / f"worker_{worker_id:02d}.log"

    logger = setup_logger(worker_log_path)

    # ========================================================
    # WORKER CACHE
    # ========================================================

    worker_cache_dir = worker_dir / "cache"

    worker_cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    worker_cache_path = worker_cache_dir / CACHE_FILE_NAME

    global_cache_path = Path(CACHE_DIR) / CACHE_FILE_NAME

    # --------------------------------------------------------
    # Copy global cache
    # --------------------------------------------------------

    if global_cache_path.exists():
        try:
            shutil.copy2(
                global_cache_path,
                worker_cache_path,
            )

        except Exception as error:
            logger.warning(
                "Cannot copy global cache: %s",
                error,
            )

    # ========================================================
    # PATCH app.excel CACHE DIR
    # ========================================================

    import app.excel as excel_module

    excel_module.CACHE_DIR = worker_cache_dir

    # ========================================================
    # TERMINAL HEADER
    # ========================================================

    print()
    print("=" * 75)

    print(f"GOOGLE MAPS WORKER {worker_number}")

    print("=" * 75)

    print(f"👷 Worker  : {worker_number}")

    print(f"📄 Chunk   : {chunk_path.name}")

    try:
        worker_df = pd.read_excel(chunk_path)

        print(f"📊 Rows    : {len(worker_df)}")

    except Exception:
        pass

    print(f"🖥️ Headless: {headless}")

    print(f"💾 Cache   : {worker_cache_dir}")

    print("=" * 75)
    print()

    started = time.time()

    # ========================================================
    # PROCESS EXCEL
    # ========================================================

    try:
        process_excel(
            str(chunk_path),
            headless=headless,
            logger=logger,
        )

        elapsed = time.time() - started

        # ====================================================
        # DONE MARKER
        # ====================================================

        done_file = worker_dir / "DONE"

        done_file.write_text(
            "DONE",
            encoding="utf-8",
        )

        print()
        print("=" * 75)

        print(f"✅ WORKER {worker_number} FINISHED")

        print(f"⏱️ Time: {elapsed:.2f}s")

        print("=" * 75)

        return 0

    # ========================================================
    # CTRL+C INSIDE WORKER TERMINAL
    # ========================================================

    except KeyboardInterrupt:
        print()
        print("=" * 75)

        print(f"🛑 WORKER {worker_number} STOPPED")

        print("=" * 75)

        return 130

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as error:
        logger.exception(
            "Worker %s failed",
            worker_number,
        )

        failed_file = worker_dir / "FAILED"

        try:
            failed_file.write_text(
                (f"{type(error).__name__}: {error}"),
                encoding="utf-8",
            )

        except Exception:
            pass

        print()
        print("=" * 75)

        print(f"❌ WORKER {worker_number} FAILED")

        print(f"{type(error).__name__}: {error}")

        print("=" * 75)

        return 1


# ============================================================
# POWERSHELL QUOTING
# ============================================================


def _powershell_quote(value):
    """
    Escape argument cho PowerShell.
    """

    value = str(value)

    return (
        "'"
        + value.replace(
            "'",
            "''",
        )
        + "'"
    )


# ============================================================
# START ONE WORKER TERMINAL
# ============================================================


def start_worker_terminal(
    worker_id,
    chunk_path,
    run_dir,
    headless,
):
    """
    Mở worker trong PowerShell riêng.
    """

    run_script = Path(__file__).resolve()

    python_exe = Path(sys.executable).resolve()

    # ========================================================
    # WORKER PYTHON ARGUMENTS
    # ========================================================

    worker_args = [
        str(python_exe),
        str(run_script),
        "--worker-mode",
        "--worker-id",
        str(worker_id),
        "--chunk",
        str(chunk_path),
        "--run-dir",
        str(run_dir),
        "--headless",
        ("true" if headless else "false"),
    ]

    # ========================================================
    # WINDOWS
    # ========================================================

    if os.name == "nt":
        python_command = "& " + " ".join(
            _powershell_quote(value) for value in worker_args
        )

        ps_command = (
            "$Host.UI.RawUI.WindowTitle="
            + _powershell_quote(f"Google Maps Worker {worker_id + 1}")
            + "; "
            + python_command
            + "; "
            + "$code=$LASTEXITCODE; "
            + "exit $code"
        )

        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_command,
        ]

        creationflags = subprocess.CREATE_NEW_CONSOLE

        return subprocess.Popen(
            command,
            creationflags=creationflags,
        )

    # ========================================================
    # NON-WINDOWS FALLBACK
    # ========================================================

    return subprocess.Popen(worker_args)


# ============================================================
# TERMINATE ONE WORKER PROCESS TREE
# ============================================================


def terminate_worker_process(
    process,
    worker_id=None,
    logger=None,
):
    """
    Dừng toàn bộ process tree của worker.

    Windows:
        PowerShell
            -> Python worker
                -> Chrome / Playwright
    """

    if process is None:
        return

    try:
        pid = process.pid

    except Exception:
        return

    # --------------------------------------------------------
    # Already exited
    # --------------------------------------------------------

    try:
        if process.poll() is not None:
            return

    except Exception:
        pass

    worker_label = f"Worker {worker_id + 1}" if worker_id is not None else f"PID {pid}"

    if logger:
        try:
            logger.warning(
                "Stopping %s | PID=%s",
                worker_label,
                pid,
            )

        except Exception:
            pass

    # ========================================================
    # WINDOWS
    # ========================================================

    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=WORKER_TERMINATE_TIMEOUT,
            )

        except Exception as error:
            if logger:
                try:
                    logger.warning(
                        "taskkill failed for %s: %s",
                        worker_label,
                        error,
                    )

                except Exception:
                    pass

        try:
            process.wait(timeout=WORKER_TERMINATE_TIMEOUT)

        except Exception:
            pass

        return

    # ========================================================
    # NON-WINDOWS
    # ========================================================

    try:
        process.terminate()

    except Exception:
        pass

    try:
        process.wait(timeout=WORKER_TERMINATE_TIMEOUT)

        return

    except Exception:
        pass

    try:
        process.kill()

    except Exception:
        pass

    try:
        process.wait(timeout=1.0)

    except Exception:
        pass


# ============================================================
# TERMINATE ALL WORKERS
# ============================================================


def terminate_all_workers(
    worker_processes,
    logger=None,
):
    """
    Dừng toàn bộ worker đang còn sống.
    """

    if not worker_processes:
        return

    print()
    print(
        "[MAIN] 🛑 Stopping all workers...",
        flush=True,
    )

    for item in worker_processes:
        process = item.get("process")

        worker_id = item.get("worker_id")

        try:
            if process is not None and process.poll() is None:
                print(
                    f"[MAIN] 🛑 Stopping Worker {worker_id + 1} | PID={process.pid}",
                    flush=True,
                )

                terminate_worker_process(
                    process=process,
                    worker_id=worker_id,
                    logger=logger,
                )

        except Exception as error:
            if logger:
                try:
                    logger.warning(
                        "Cannot stop Worker %s: %s",
                        (worker_id + 1 if worker_id is not None else "?"),
                        error,
                    )

                except Exception:
                    pass

    print(
        "[MAIN] ✅ All workers stopped.",
        flush=True,
    )


# ============================================================
# MERGE WORKER RESULTS
# ============================================================


def merge_worker_results(
    input_path,
    chunks,
    run_dir,
):
    """
    Merge toàn bộ worker result.

    Row order dựa trên:
        __gm_original_index

    STT được lấy trực tiếp từ Excel original.
    """

    input_path = Path(input_path)

    run_dir = Path(run_dir)

    result_frames = []

    missing_frames = []

    # ========================================================
    # ORIGINAL EXCEL
    # ========================================================

    original_df = pd.read_excel(input_path)

    original_total = len(original_df)

    if original_total == 0:
        raise ValueError("Original Excel contains no rows.")

    original_with_index = original_df.copy()

    original_with_index.insert(
        0,
        ORIGINAL_INDEX_COLUMN,
        range(original_total),
    )

    # ========================================================
    # ORIGINAL STT
    # ========================================================

    stt_column = find_stt_column(original_df)

    stt_map = None

    if stt_column is not None:
        stt_map = dict(
            zip(
                original_with_index[ORIGINAL_INDEX_COLUMN].tolist(),
                original_with_index[stt_column].tolist(),
            )
        )

    # ========================================================
    # READ RESULTS
    # ========================================================

    for (
        worker_id,
        chunk_path,
        expected_rows,
    ) in chunks:
        result_path = find_worker_result_file(chunk_path)

        if not result_path.exists():
            raise FileNotFoundError(
                f"Worker {worker_id + 1} result not found: {result_path}"
            )

        worker_df = pd.read_excel(result_path)

        if ORIGINAL_INDEX_COLUMN not in worker_df.columns:
            raise ValueError(
                f"Worker {worker_id + 1} is missing {ORIGINAL_INDEX_COLUMN}"
            )

        if len(worker_df) != expected_rows:
            raise ValueError(
                f"Worker "
                f"{worker_id + 1} "
                f"row count mismatch: "
                f"expected={expected_rows}, "
                f"actual={len(worker_df)}"
            )

        worker_df[ORIGINAL_INDEX_COLUMN] = pd.to_numeric(
            worker_df[ORIGINAL_INDEX_COLUMN],
            errors="raise",
        ).astype("int64")

        result_frames.append(worker_df)

        # ====================================================
        # MISSING
        # ====================================================

        missing_path = find_worker_missing_file(chunk_path)

        if missing_path.exists():
            missing_df = pd.read_excel(missing_path)

            if not missing_df.empty:
                if ORIGINAL_INDEX_COLUMN not in missing_df.columns:
                    raise ValueError(
                        f"Worker "
                        f"{worker_id + 1} "
                        f"missing file lacks "
                        f"{ORIGINAL_INDEX_COLUMN}"
                    )

                missing_df[ORIGINAL_INDEX_COLUMN] = pd.to_numeric(
                    missing_df[ORIGINAL_INDEX_COLUMN],
                    errors="raise",
                ).astype("int64")

                missing_frames.append(missing_df)

    # ========================================================
    # CONCAT
    # ========================================================

    if not result_frames:
        raise RuntimeError("No worker results.")

    merged = pd.concat(
        result_frames,
        ignore_index=True,
    )

    # ========================================================
    # TOTAL VALIDATION
    # ========================================================

    if len(merged) != original_total:
        raise RuntimeError(
            "Merged row count mismatch | "
            f"original={original_total} | "
            f"merged={len(merged)}"
        )

    # ========================================================
    # DUPLICATE INDEX
    # ========================================================

    duplicated_mask = merged[ORIGINAL_INDEX_COLUMN].duplicated(keep=False)

    if duplicated_mask.any():
        duplicated_indexes = merged.loc[
            duplicated_mask,
            ORIGINAL_INDEX_COLUMN,
        ].tolist()

        raise RuntimeError(f"Duplicate original indexes: {duplicated_indexes[:30]}")

    # ========================================================
    # MISSING / EXTRA INDEX
    # ========================================================

    expected_index_set = set(range(original_total))

    actual_index_set = set(merged[ORIGINAL_INDEX_COLUMN].tolist())

    missing_original = expected_index_set - actual_index_set

    extra_original = actual_index_set - expected_index_set

    if missing_original:
        raise RuntimeError(f"Missing original rows: {sorted(missing_original)[:30]}")

    if extra_original:
        raise RuntimeError(f"Extra original rows: {sorted(extra_original)[:30]}")

    # ========================================================
    # RESTORE ORIGINAL ORDER
    # ========================================================

    merged = merged.sort_values(
        ORIGINAL_INDEX_COLUMN,
        kind="stable",
    ).reset_index(drop=True)

    # ========================================================
    # VERIFY FINAL ORDER
    # ========================================================

    final_order = merged[ORIGINAL_INDEX_COLUMN].tolist()

    expected_order = list(range(original_total))

    if final_order != expected_order:
        raise RuntimeError("Final row order validation failed.")

    # ========================================================
    # RESTORE ORIGINAL STT
    # ========================================================

    if stt_column is not None and stt_map is not None:
        merged[stt_column] = merged[ORIGINAL_INDEX_COLUMN].map(stt_map)

    # ========================================================
    # REMOVE INTERNAL COLUMN
    # ========================================================

    merged = merged.drop(columns=[ORIGINAL_INDEX_COLUMN])

    # ========================================================
    # FINAL RESULT
    # ========================================================

    final_base = input_path.with_suffix("")

    final_result_path = Path(str(final_base) + RESULT_SUFFIX)

    temp_result_path = Path(str(final_result_path) + ".tmp.xlsx")

    merged.to_excel(
        temp_result_path,
        index=False,
        engine="openpyxl",
    )

    os.replace(
        temp_result_path,
        final_result_path,
    )

    # ========================================================
    # MISSING MERGE
    # ========================================================

    final_missing_path = Path(str(final_base) + MISSING_SUFFIX)

    if missing_frames:
        missing = pd.concat(
            missing_frames,
            ignore_index=True,
        )

        missing = missing.drop_duplicates(
            subset=[ORIGINAL_INDEX_COLUMN],
            keep="first",
        )

        missing = missing.sort_values(
            ORIGINAL_INDEX_COLUMN,
            kind="stable",
        ).reset_index(drop=True)

        if (
            stt_column is not None
            and stt_map is not None
            and stt_column in missing.columns
        ):
            missing[stt_column] = missing[ORIGINAL_INDEX_COLUMN].map(stt_map)

        missing = missing.drop(columns=[ORIGINAL_INDEX_COLUMN])

        temp_missing_path = Path(str(final_missing_path) + ".tmp.xlsx")

        missing.to_excel(
            temp_missing_path,
            index=False,
            engine="openpyxl",
        )

        os.replace(
            temp_missing_path,
            final_missing_path,
        )

    else:
        try:
            if final_missing_path.exists():
                final_missing_path.unlink()

        except Exception:
            pass

    return (
        final_result_path,
        (final_missing_path if missing_frames else None),
        merged,
    )


# ============================================================
# MERGE CACHE
# ============================================================


def merge_caches(
    chunks,
):
    """
    Chọn worker cache lớn nhất
    rồi copy về global cache.
    """

    candidates = []

    for (
        worker_id,
        chunk_path,
        _,
    ) in chunks:
        worker_dir = Path(chunk_path).parent.parent / f"worker_{worker_id:02d}"

        worker_cache = worker_dir / "cache" / CACHE_FILE_NAME

        if worker_cache.exists():
            try:
                candidates.append(
                    (
                        worker_cache.stat().st_size,
                        worker_cache,
                    )
                )

            except Exception:
                pass

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    source = candidates[0][1]

    global_cache_dir = Path(CACHE_DIR)

    global_cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = global_cache_dir / CACHE_FILE_NAME

    try:
        shutil.copy2(
            source,
            destination,
        )

        return destination

    except Exception:
        return None


# ============================================================
# PARALLEL EXCEL
# ============================================================


def process_excel_parallel(
    file_path,
    headless,
    workers,
    logger,
):
    """
    Main process.

    VSCode terminal chỉ hiện:
        - worker started
        - worker done
        - merge
        - finished

    Worker detail nằm trong PowerShell riêng.
    """

    file_path = Path(os.path.abspath(str(file_path)))

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # ========================================================
    # RUN DIRECTORY
    # ========================================================

    timestamp = time.strftime("%Y%m%d_%H%M%S")

    run_dir = file_path.parent / f".gm_run_{timestamp}"

    run_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # HEADER
    # ========================================================

    print()
    print("=" * 75)
    print("GOOGLE MAPS MULTI-WORKER MODE")
    print("=" * 75)

    print(f"📄 Input    : {file_path}")

    print(f"👷 Workers  : {workers}")

    print(f"🖥️ Headless : {headless}")

    print(f"📁 Run dir  : {run_dir}")

    # ========================================================
    # SPLIT
    # ========================================================

    split_started = time.time()

    (
        chunks,
        total_rows,
    ) = split_excel(
        file_path,
        run_dir,
        workers,
    )

    split_elapsed = time.time() - split_started

    print()
    print(
        f"📦 Split {total_rows} rows into {len(chunks)} workers in {split_elapsed:.2f}s"
    )

    print()

    for (
        worker_id,
        chunk_path,
        chunk_rows,
    ) in chunks:
        print(f"   Worker {worker_id + 1}: {chunk_rows} rows")

    print()
    print("🚀 STARTING WORKER TERMINALS...")
    print()

    # ========================================================
    # START ALL WORKERS
    # ========================================================

    started = time.time()

    worker_processes = []

    failed_workers = []

    try:
        for (
            worker_id,
            chunk_path,
            chunk_rows,
        ) in chunks:
            process = start_worker_terminal(
                worker_id=worker_id,
                chunk_path=chunk_path,
                run_dir=run_dir,
                headless=headless,
            )

            worker_processes.append(
                {
                    "worker_id": worker_id,
                    "chunk_path": chunk_path,
                    "rows": chunk_rows,
                    "process": process,
                }
            )

            print(
                f"[MAIN] 🚀 Worker "
                f"{worker_id + 1} "
                f"started | "
                f"PID={process.pid} | "
                f"{chunk_rows} rows",
                flush=True,
            )

        # ====================================================
        # WAIT FOR WORKERS
        # ====================================================

        remaining = list(worker_processes)

        while remaining:
            next_remaining = []

            for item in remaining:
                process = item["process"]

                return_code = process.poll()

                # --------------------------------------------
                # Still running
                # --------------------------------------------

                if return_code is None:
                    next_remaining.append(item)

                    continue

                worker_id = item["worker_id"]

                # --------------------------------------------
                # Finished
                # --------------------------------------------

                if return_code == 0:
                    print(
                        f"[MAIN] ✅ Worker {worker_id + 1} DONE",
                        flush=True,
                    )

                else:
                    print(
                        f"[MAIN] ❌ Worker {worker_id + 1} FAILED | exit={return_code}",
                        flush=True,
                    )

                    failed_workers.append(worker_id)

            remaining = next_remaining

            if remaining:
                time.sleep(WORKER_POLL_INTERVAL)

    # ========================================================
    # CTRL+C
    # ========================================================

    except KeyboardInterrupt:
        print()
        print(
            "[MAIN] 🛑 CTRL+C detected.",
            flush=True,
        )

        terminate_all_workers(
            worker_processes,
            logger=logger,
        )

        raise

    # ========================================================
    # OTHER MAIN ERROR
    # ========================================================

    except Exception:
        terminate_all_workers(
            worker_processes,
            logger=logger,
        )

        raise

    parallel_elapsed = time.time() - started

    # ========================================================
    # FAILED WORKER
    # ========================================================

    if failed_workers:
        terminate_all_workers(
            worker_processes,
            logger=logger,
        )

        failed_text = ", ".join(str(worker_id + 1) for worker_id in failed_workers)

        raise RuntimeError(
            f"Workers failed: {failed_text}. Outputs preserved in {run_dir}"
        )

    # ========================================================
    # MERGE
    # ========================================================

    print()
    print("[MAIN] 🔀 MERGING WORKER RESULTS...")

    merge_started = time.time()

    (
        result_path,
        missing_path,
        merged_df,
    ) = merge_worker_results(
        file_path,
        chunks,
        run_dir,
    )

    merge_elapsed = time.time() - merge_started

    # ========================================================
    # CACHE
    # ========================================================

    cache_path = merge_caches(chunks)

    # ========================================================
    # TOTAL
    # ========================================================

    total_elapsed = time.time() - started

    # ========================================================
    # FINAL
    # ========================================================

    print()
    print("=" * 75)
    print("GOOGLE MAPS MULTI-WORKER FINISHED")
    print("=" * 75)

    print(f"📊 Total rows : {len(merged_df)}")

    print(f"👷 Workers    : {len(chunks)}")

    print(f"⏱️ Workers    : {parallel_elapsed:.2f}s")

    print(f"🔀 Merge      : {merge_elapsed:.2f}s")

    print(f"⏱️ Total      : {total_elapsed:.2f}s")

    print(f"📁 Result     : {result_path}")

    if missing_path:
        print(f"📁 Missing    : {missing_path}")

    if cache_path:
        print(f"💾 Cache      : {cache_path}")

    print(f"📁 Worker dir : {run_dir}")

    print("=" * 75)

    return {
        "total_rows": len(merged_df),
        "workers": len(chunks),
        "elapsed_seconds": round(
            total_elapsed,
            2,
        ),
        "result_file": str(result_path),
        "missing_file": (str(missing_path) if missing_path else None),
        "run_dir": str(run_dir),
    }


# ============================================================
# MAIN
# ============================================================


def main():

    parser = build_parser()

    args = parser.parse_args()

    headless = args.headless.lower() == "true"

    # ========================================================
    # INTERNAL WORKER MODE
    # ========================================================

    if args.worker_mode:
        if args.worker_id is None:
            print("❌ Missing --worker-id")

            return 1

        if not args.chunk:
            print("❌ Missing --chunk")

            return 1

        if not args.run_dir:
            print("❌ Missing --run-dir")

            return 1

        return run_worker_terminal(
            worker_id=args.worker_id,
            chunk_path=args.chunk,
            run_dir=args.run_dir,
            headless=headless,
        )

    # ========================================================
    # MAIN LOGGER
    # ========================================================

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
    # FILE REQUIRED
    # ========================================================

    if not args.file:
        parser.print_help()

        print()
        print("Examples:")

        print("  python run.py input/hotels_export.xlsx")

        print("  python run.py input/hotels_export.xlsx --headless false --workers 3")

        print("  python run.py input/hotels_export.xlsx --headless true --workers 4")

        print('  python run.py --test "Minh Quân Hotel" "Sa Pa, Lào Cai"')

        return 1

    # ========================================================
    # WORKER COUNT
    # ========================================================

    workers = int(args.workers)

    if workers < 1:
        print("❌ --workers must be >= 1")

        return 1

    # ========================================================
    # SINGLE WORKER
    # ========================================================

    if workers == 1:
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

    # ========================================================
    # MULTI-WORKER
    # ========================================================

    try:
        process_excel_parallel(
            file_path=args.file,
            headless=headless,
            workers=workers,
            logger=logger,
        )

        return 0

    except KeyboardInterrupt:
        print()
        print("🛑 MAIN stopped by user.")

        return 130

    except Exception as error:
        logger.exception("Fatal parallel processing error")

        print()
        print("❌ FATAL ERROR:")

        print(f"{type(error).__name__}: {error}")

        return 1


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    multiprocessing.freeze_support()

    sys.exit(main())
