from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


_SEED_PATH = Path(__file__).with_name("seed").joinpath("default_catalog.json")


def _load_seed_payload() -> dict[str, Any]:
    with _SEED_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Catalog seed payload must be an object: {_SEED_PATH}")
    return payload


def _read_seed_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw_value = payload.get(key) or []
    if not isinstance(raw_value, list):
        raise RuntimeError(f"Catalog seed field must be a list: {key}")
    return deepcopy(raw_value)


_SEED_PAYLOAD = _load_seed_payload()

DEFAULT_CATALOG_VERSION = str(_SEED_PAYLOAD.get("catalog_version") or "seed-20260419-v1")
DEFAULT_PRODUCT_FAMILIES: list[dict[str, Any]] = _read_seed_list(_SEED_PAYLOAD, "product_families")
DEFAULT_PRODUCT_COMPATIBILITY: list[dict[str, Any]] = _read_seed_list(_SEED_PAYLOAD, "product_compatibility")
DEFAULT_PRODUCT_CATALOG: list[dict[str, Any]] = _read_seed_list(_SEED_PAYLOAD, "product_catalog")
DEFAULT_PRODUCT_MODELS: list[dict[str, Any]] = _read_seed_list(_SEED_PAYLOAD, "product_models")
DEFAULT_PRODUCT_INTERFACES: list[dict[str, Any]] = _read_seed_list(_SEED_PAYLOAD, "product_interfaces")
