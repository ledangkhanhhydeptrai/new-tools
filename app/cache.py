# ============================================================
# app/cache.py
# ============================================================

from pathlib import Path

from .utils import (
    build_cache_key,
    clean_google_maps_url,
    load_json,
    save_json_atomic,
    now_iso,
)


class GoogleMapsCache:
    def __init__(
        self,
        cache_path,
    ):
        self.cache_path = Path(cache_path)

        self.data = {
            "version": 1,
            "items": {},
        }

        self.load()

    # ========================================================
    # LOAD
    # ========================================================

    def load(self):

        if not self.cache_path.exists():
            return

        loaded = load_json(
            self.cache_path,
            {},
        )

        if not isinstance(
            loaded,
            dict,
        ):
            return

        if "items" not in loaded:
            return

        if not isinstance(
            loaded["items"],
            dict,
        ):
            return

        self.data = loaded

    # ========================================================
    # GET
    # ========================================================

    def get(
        self,
        title,
        address,
    ):
        key = build_cache_key(
            title,
            address,
        )

        item = self.data.get(
            "items",
            {},
        ).get(key)

        if not isinstance(item, dict):
            return None

        url = clean_google_maps_url(
            item.get(
                "google_maps_url",
                "",
            )
        )

        if not url:
            return None

        return item

    # ========================================================
    # SET
    # ========================================================

    def set(
        self,
        title,
        address,
        google_maps_url,
        confidence="HIGH",
        name_score=0,
        address_score=0,
        method="SEARCH",
    ):
        google_maps_url = clean_google_maps_url(google_maps_url)

        if not google_maps_url:
            return False

        key = build_cache_key(
            title,
            address,
        )

        self.data.setdefault(
            "items",
            {},
        )[key] = {
            "title": title,
            "address": address,
            "google_maps_url": google_maps_url,
            "confidence": confidence,
            "name_score": name_score,
            "address_score": address_score,
            "method": method,
            "updated_at": now_iso(),
        }

        return True

    # ========================================================
    # SAVE
    # ========================================================

    def save(self):

        self.cache_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        save_json_atomic(
            self.data,
            self.cache_path,
        )

    # ========================================================
    # SIZE
    # ========================================================

    def __len__(self) -> int:
        items = self.data.get("items", {})

        if isinstance(items, (dict, list, tuple, set, str)):
            return len(items)

        return 0
