from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.config import get_settings


logger = logging.getLogger(__name__)


def configure_docling_runtime(settings: Any | None = None) -> None:
    """Apply runtime cache and mirror settings before Docling loads models."""

    settings = settings or get_settings()
    hf_endpoint = str(getattr(settings, "hf_endpoint", "") or "").strip()
    hf_home = str(getattr(settings, "hf_home", "") or "").strip()
    cache_dir = str(getattr(settings, "docling_cache_dir", "") or "").strip()
    artifacts_path = str(getattr(settings, "docling_artifacts_path", "") or "").strip()

    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint
    if hf_home:
        os.environ.setdefault("HF_HOME", hf_home)
    if cache_dir:
        os.environ["DOCLING_CACHE_DIR"] = cache_dir
    if artifacts_path:
        os.environ["DOCLING_ARTIFACTS_PATH"] = artifacts_path

    try:
        from docling.datamodel.settings import settings as docling_settings
    except Exception as exc:  # pragma: no cover - docling is an optional dependency in some test envs
        logger.debug("Docling settings are unavailable: %s", exc)
        return

    if cache_dir:
        docling_settings.cache_dir = Path(cache_dir).expanduser()
    if artifacts_path:
        docling_settings.artifacts_path = Path(artifacts_path).expanduser()


def prewarm_docling_models(settings: Any | None = None, *, force: bool = False) -> Path:
    """Download Docling's default parsing models into the configured artifacts directory."""

    settings = settings or get_settings()
    configure_docling_runtime(settings)

    artifacts_path = str(getattr(settings, "docling_artifacts_path", "") or "").strip()
    output_dir = Path(artifacts_path or "/opt/docling/models").expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    from docling.utils.model_downloader import download_models

    logger.info("Prewarming Docling models into %s", output_dir)
    download_models(
        output_dir=output_dir,
        force=force,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_tableformer_v2=False,
        with_code_formula=True,
        with_picture_classifier=True,
        with_rapidocr=True,
        with_easyocr=False,
    )
    return output_dir


def main() -> None:
    prewarm_docling_models()


if __name__ == "__main__":
    main()
