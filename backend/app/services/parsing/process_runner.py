from __future__ import annotations

import asyncio
import multiprocessing
import os
import queue
import signal
import traceback
from typing import Any

from app.services.parsing.docling_parser import ParsedDocument


class DocumentParseProcessTimeoutError(TimeoutError):
    pass


class DocumentParseProcessFailedError(RuntimeError):
    pass


async def parse_document_in_subprocess(
    file_path: str,
    *,
    include_asset_enrichment: bool,
    timeout_seconds: float,
) -> ParsedDocument:
    return await asyncio.to_thread(
        _parse_document_in_subprocess_sync,
        file_path,
        include_asset_enrichment,
        timeout_seconds,
    )


def _parse_document_in_subprocess_sync(
    file_path: str,
    include_asset_enrichment: bool,
    timeout_seconds: float,
) -> ParsedDocument:
    timeout = max(1.0, float(timeout_seconds or 1.0))
    context = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue[tuple[str, Any]] = context.Queue(maxsize=1)
    process = context.Process(
        target=_parse_document_worker,
        args=(file_path, include_asset_enrichment, result_queue),
        name="document-parse-worker",
    )
    process.daemon = True
    process.start()
    process.join(timeout)

    if process.is_alive():
        _terminate_process_group(process)
        raise DocumentParseProcessTimeoutError(f"Document parsing timed out after {timeout:.0f}s")

    try:
        status, payload = result_queue.get_nowait()
    except queue.Empty as exc:
        raise DocumentParseProcessFailedError(
            f"Document parsing process exited without a result, exit_code={process.exitcode}"
        ) from exc

    if status == "ok":
        return payload
    error_type = str(payload.get("type") or "DocumentParseProcessError")
    message = str(payload.get("message") or "")
    worker_traceback = str(payload.get("traceback") or "")
    raise DocumentParseProcessFailedError(f"{error_type}: {message}\n{worker_traceback}".strip())


def _parse_document_worker(
    file_path: str,
    include_asset_enrichment: bool,
    result_queue: multiprocessing.Queue[tuple[str, Any]],
) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    os.environ["PARSER_PROCESS_ISOLATION_ENABLED"] = "false"
    try:
        from app.config import get_settings
        from app.services.parsing.docling_runtime import apply_parser_thread_limits
        from app.services.parsing.parser import ParserService

        get_settings.cache_clear()
        apply_parser_thread_limits(get_settings())
        parsed = asyncio.run(
            ParserService()._parse_document_inline(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
            )
        )
        result_queue.put(("ok", parsed))
    except Exception as exc:  # noqa: BLE001
        result_queue.put(
            (
                "error",
                {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )


def _terminate_process_group(process: multiprocessing.Process) -> None:
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            process.terminate()
    else:  # pragma: no cover - Windows runner fallback
        process.terminate()
    process.join(5)
    if process.is_alive():
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                process.kill()
        else:  # pragma: no cover - Windows runner fallback
            process.kill()
        process.join(5)
