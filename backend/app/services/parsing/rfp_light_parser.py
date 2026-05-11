"""RFP Light Parser.

Lightweight, time-bounded text extractor for project RFP documents.  Designed to
keep the upload -> requirement-extraction loop under 60 seconds and to never
trigger the heavy historical-ingestion pipeline (no chunking, no embeddings,
no Qdrant writes, no figure/table asset extraction, no library refresh).

Supported formats:

- ``.txt`` / ``.md``  : plain-text read (UTF-8 -> GBK -> latin-1 fallback)
- ``.docx``           : python-docx paragraphs + table cells
- ``.pdf``            : pypdfium2 textpage extraction, capped to ``max_pages``
- ``.doc``            : not supported in this path; raises ``RfpLightParseInsufficient``
                       so the caller records ``parse_status='parse_insufficient'``
                       and prompts the user to upload .docx / .pdf instead.

**Hard timeout (Review R2 #1).**  Each parse runs in a *spawned* subprocess and
is bounded by a ``Process.join(timeout=...)`` watchdog.  When the watchdog
fires the parent process escalates SIGTERM -> SIGKILL on the child so a stuck
PDF cannot pin a worker thread or saturate the CPU.  This replaces the previous
soft ``asyncio.wait_for`` approach (which left worker threads leaking until
the underlying parser returned).

The parent still uses a dedicated ``ThreadPoolExecutor`` (NOT the asyncio
default executor) to bound concurrency at the watchdog level so we do not
fork-bomb the host with subprocesses; ``rfp_light_parse_max_workers`` controls
how many parses can run in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import multiprocessing.connection
import os
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings


logger = logging.getLogger(__name__)

# ``spawn`` instead of ``fork`` so the child does not inherit asyncio loops,
# DB session factories, file descriptors, or thread-locked state.  This is
# also the only context that works consistently on macOS (default since 3.8)
# without surprising behaviour from ``fork`` after threads have been spawned.
_mp_context = multiprocessing.get_context("spawn")

# Dedicated executor for RFP light parsing.  Kept module-private and lazily
# initialised so test runs and CLI tools do not pay the cost of a thread pool
# until the first parse.
_rfp_light_executor: ThreadPoolExecutor | None = None
_rfp_light_executor_lock = threading.Lock()
_rfp_light_executor_workers: int | None = None


def _get_rfp_light_executor(*, max_workers: int) -> ThreadPoolExecutor:
    global _rfp_light_executor, _rfp_light_executor_workers
    with _rfp_light_executor_lock:
        if _rfp_light_executor is None or _rfp_light_executor_workers != max_workers:
            if _rfp_light_executor is not None:
                # Settings changed at runtime (e.g. tests); shut down the old pool
                # without waiting so we do not stall on a stuck parser.
                _rfp_light_executor.shutdown(wait=False, cancel_futures=True)
            _rfp_light_executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="rfp-light-parse",
            )
            _rfp_light_executor_workers = max_workers
        return _rfp_light_executor


SUPPORTED_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
SUPPORTED_DOCX_SUFFIXES = frozenset({".docx"})
SUPPORTED_PDF_SUFFIXES = frozenset({".pdf"})
LEGACY_DOC_SUFFIXES = frozenset({".doc"})
ALL_SUPPORTED_SUFFIXES = (
    SUPPORTED_TEXT_SUFFIXES | SUPPORTED_DOCX_SUFFIXES | SUPPORTED_PDF_SUFFIXES
)


class RfpLightParseError(Exception):
    """Base error for RFP light parser failures."""


class RfpLightParseInsufficient(RfpLightParseError):
    """Raised when light extraction could not produce usable requirement text.

    The caller should map this to ``Document.parse_status='parse_insufficient'``
    (or ``failed`` for unrecoverable errors), keep the original file on disk so
    the user can manually re-upload a different format, and surface a friendly
    message to the UI.  The original RFP file MUST NOT be auto-promoted into the
    historical proposal library.
    """

    def __init__(
        self,
        *,
        reason: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.metadata = dict(metadata or {})


@dataclass
class RfpLightParseResult:
    """Outcome of a successful light extraction.

    **Review R4 #1 update.**  Prior to this fix the parser silently truncated
    ``text`` to ``max_chars`` (default 80,000 characters) and the lossy
    truncated payload was the only thing that ever reached
    ``rfp_text_storage_path``.  Any requirement appearing in the back half of a
    long RFP was permanently lost.  The parser now returns the *full*
    extracted text and the caller is responsible for storing it in full and
    deciding how to feed it to downstream LLM prompts.

    Fields:

    - ``text``: full extracted text, normalised but **not truncated**.  Length
      is reported in ``char_count``.
    - ``excerpt``: leading slice of length ``excerpt_chars`` for the requirement
      extraction prompt.  This stays a mechanical head-slice for now; F3
      "RFP knowledge extractor" will replace it with section-aware sampling.
    - ``page_count``: PDF page count (0 for docx/text).
    - ``truncated_pages``: True when the PDF had more pages than ``max_pages``.
    - ``truncated_chars``: kept for API compatibility, always ``False`` under
      the R4 design (the parser does not truncate; an OOM-grade hard cap will
      raise ``RfpLightParseInsufficient`` rather than silently truncating).
    """

    text: str
    excerpt: str
    page_count: int = 0
    char_count: int = 0
    elapsed_seconds: float = 0.0
    source_format: str = ""
    truncated_chars: bool = False
    truncated_pages: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Module-level extraction helpers (importable, picklable, run inside subprocess)
# ============================================================================


def _extract_plain_text(path: Path) -> str:
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_docx(path: Path) -> str:
    try:
        import docx  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RfpLightParseError("python-docx is required for .docx extraction") from exc

    document = docx.Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if text:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            row_text = " | ".join(cell for cell in cells if cell)
            if row_text:
                parts.append(row_text)
    return "\n\n".join(parts)


def _extract_pdf(path: Path, *, max_pages: int) -> tuple[str, int, bool]:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RfpLightParseError("pypdfium2 is required for .pdf extraction") from exc

    pdf = pdfium.PdfDocument(str(path))
    try:
        total_pages = len(pdf)
        pages_to_read = min(total_pages, max(1, max_pages))
        truncated = total_pages > pages_to_read
        parts: list[str] = []
        for page_index in range(pages_to_read):
            page = pdf[page_index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range() or ""
                finally:
                    textpage.close()
            finally:
                page.close()
            stripped = text.strip()
            if stripped:
                parts.append(stripped)
        return ("\n\n".join(parts), pages_to_read, truncated)
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def _subprocess_extract_entrypoint(
    conn: "multiprocessing.connection.Connection",
    file_path_str: str,
    max_pages: int,
    output_text_path: str,
) -> None:
    """Top-level subprocess entry: parse and stream the full text to disk.

    **Review R4 #1 design.**  Previously the child sent the entire extracted
    text through the ``multiprocessing.Pipe`` and the parent truncated to
    ``max_chars`` before persisting.  Two problems with that:

    1. *Pipe blocking.*  The OS pipe buffer is small (16-32KB on macOS, ~64KB
       on Linux), so a 200KB RFP would block ``conn.send`` until the parent
       drained the pipe.  R3 worked around this by truncating in the child,
       but truncation lost the back half of the RFP.
    2. *Requirement loss.*  Any requirement appearing past character 80k was
       silently dropped before it reached storage, so the LLM never saw it.

    The new contract:

    - The parent creates a tmpfile at ``output_text_path`` and passes the path
      to the child as a string.
    - The child writes the *full* extracted text to that file with UTF-8
      encoding.  Disk I/O is unbounded so this never deadlocks.
    - The child sends a small metadata dict (~200 bytes) over the pipe:
      ``{ok, page_count, truncated_pages, source_format, char_count}``.
    - The parent reads the tmpfile back as a single string and deletes it.

    Errors are still serialised into the metadata payload because custom
    exception classes are not always pickle-safe across the spawn boundary.
    """

    payload: dict[str, Any]
    text: str = ""
    page_count = 0
    truncated_pages = False
    source_format = ""
    extraction_ok = False
    try:
        path = Path(file_path_str)
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_TEXT_SUFFIXES:
            text = _extract_plain_text(path)
            source_format = "text"
            extraction_ok = True
        elif suffix in SUPPORTED_DOCX_SUFFIXES:
            text = _extract_docx(path)
            source_format = "docx"
            extraction_ok = True
        elif suffix in SUPPORTED_PDF_SUFFIXES:
            text, page_count, truncated_pages = _extract_pdf(path, max_pages=max_pages)
            source_format = "pdf"
            extraction_ok = True
        else:
            payload = {
                "ok": False,
                "exc_kind": "RfpLightParseInsufficient",
                "reason": "unsupported_format",
                "message": f"暂不支持的 RFP 文件格式：{suffix}",
                "metadata": {"file_suffix": suffix},
            }
    except RfpLightParseInsufficient as exc:
        payload = {
            "ok": False,
            "exc_kind": "RfpLightParseInsufficient",
            "reason": exc.reason,
            "message": str(exc),
            "metadata": exc.metadata,
        }
    except RfpLightParseError as exc:
        payload = {
            "ok": False,
            "exc_kind": "RfpLightParseError",
            "reason": "extraction_failed",
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "exc_kind": "Exception",
            "reason": "extraction_failed",
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    if extraction_ok:
        # Stream the full text to the tmpfile created by the parent.  Disk I/O
        # is unbounded so we never deadlock on the pipe buffer.  Writing
        # errors (read-only fs, no space) surface as an extraction failure
        # rather than a silent truncation.
        try:
            Path(output_text_path).write_text(text, encoding="utf-8")
            payload = {
                "ok": True,
                "page_count": page_count,
                "truncated_pages": truncated_pages,
                "source_format": source_format,
                "char_count": len(text),
            }
        except Exception as exc:  # noqa: BLE001
            payload = {
                "ok": False,
                "exc_kind": "RfpLightParseError",
                "reason": "tmpfile_write_failed",
                "message": f"RFP 解析子进程写入临时文件失败：{exc}",
            }

    try:
        conn.send(payload)
    except Exception:  # noqa: BLE001 - best-effort send before exit
        pass
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _terminate_subprocess(
    process: multiprocessing.Process,
    *,
    grace_seconds: float = 2.0,
) -> tuple[bool, bool]:
    """Send SIGTERM, wait briefly, escalate to SIGKILL.

    Returns ``(terminated, killed)`` flags so the caller can log what happened.
    """

    terminated = False
    killed = False
    if process.is_alive():
        process.terminate()
        terminated = True
        process.join(timeout=grace_seconds)
    if process.is_alive():
        process.kill()
        killed = True
        process.join(timeout=grace_seconds)
    return terminated, killed


def _run_extract_with_subprocess(
    file_path_str: str,
    max_pages: int,
    max_seconds: float,
) -> tuple[str, int, bool, str, dict[str, Any]]:
    """Synchronous wrapper that runs extraction in a SIGKILL-able child process.

    Meant to be wrapped by :func:`asyncio.AbstractEventLoop.run_in_executor`
    from a worker thread so the asyncio loop stays responsive.  Returns a
    ``(text, page_count, truncated_pages, source_format, extras)`` tuple on
    success — ``text`` is the *full* extracted text (no silent truncation) —
    or raises :class:`RfpLightParseInsufficient` /
    :class:`RfpLightParseError`.

    **Review R4 #1 design.**  The IPC contract carries the full text through
    a parent-owned tmpfile rather than the multiprocessing pipe:

    1. The parent creates a tmpfile via :mod:`tempfile`.
    2. The child writes the full text to that path.
    3. The pipe only carries a tiny metadata dict (~200 bytes), so it can
       never block on the OS pipe buffer.
    4. The parent reads the tmpfile and deletes it in ``finally``.

    The watchdog is still a poll/recv loop (Review R3 #1) so a child that
    hangs in the parser proper is SIGKILL-ed and reported as a real timeout
    rather than as a phantom pipe-block.
    """

    # Parent-owned tmpfile.  We delete it ourselves in ``finally``.  Using
    # ``delete=False`` keeps the path accessible after we close the fd, and
    # ``mode="w"`` truncates any prior content before the child writes.
    tmpfile = tempfile.NamedTemporaryFile(
        prefix="rfp-light-",
        suffix=".rfptxt",
        delete=False,
    )
    tmpfile_path = tmpfile.name
    tmpfile.close()

    parent_conn, child_conn = _mp_context.Pipe(duplex=False)
    process = _mp_context.Process(
        target=_subprocess_extract_entrypoint,
        args=(child_conn, file_path_str, max_pages, tmpfile_path),
        name="rfp-light-parse-subprocess",
        daemon=True,
    )
    process.start()
    # Close the child's end in the parent so ``parent_conn.poll`` reflects EOF
    # if the child exits without sending anything.
    child_conn.close()

    suffix = Path(file_path_str).suffix.lower()
    payload: dict[str, Any] | None = None
    timed_out = False
    try:
        deadline = time.monotonic() + max_seconds
        # Poll/recv loop guarding the child's metadata send.  Since the child
        # now sends ~200 bytes (R4 #1) the pipe will never block, but the
        # loop is retained as a clean watchdog for the parse step itself.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            poll_wait = min(0.1, remaining)
            if parent_conn.poll(timeout=poll_wait):
                try:
                    payload = parent_conn.recv()
                except EOFError:
                    payload = None
                break
            if not process.is_alive():
                # Child exited; give the kernel a beat to flush the last
                # frame, then attempt a final drain.
                if parent_conn.poll(timeout=0.5):
                    try:
                        payload = parent_conn.recv()
                    except EOFError:
                        payload = None
                break

        if timed_out:
            terminated, killed = _terminate_subprocess(process)
            logger.warning(
                "RFP light parse hard-timeout: pid=%s file=%s limit=%.1fs "
                "terminated=%s killed=%s. Subprocess sandbox prevents thread leaks.",
                process.pid,
                file_path_str,
                max_seconds,
                terminated,
                killed,
            )
            raise RfpLightParseInsufficient(
                reason="timeout",
                message=(
                    f"RFP 轻量解析超过 {max_seconds:.1f}s 上限，已 SIGKILL 子进程并标记 parse_insufficient。"
                ),
                metadata={
                    "timeout_seconds": max_seconds,
                    "file_suffix": suffix,
                    "subprocess_terminated": terminated,
                    "subprocess_killed": killed,
                },
            )

        # Drain the child if it is still alive (e.g. crashed mid-send).
        if process.is_alive():
            _terminate_subprocess(process, grace_seconds=0.5)

        if payload is None:
            raise RfpLightParseInsufficient(
                reason="subprocess_exited_silently",
                message=(
                    f"RFP 解析子进程异常退出 (exitcode={process.exitcode})，请重试或更换文件格式。"
                ),
                metadata={
                    "exitcode": process.exitcode,
                    "file_suffix": suffix,
                },
            )

        if not isinstance(payload, dict):
            raise RfpLightParseError(
                f"unexpected payload type from RFP subprocess: {type(payload).__name__}"
            )

        if payload.get("ok"):
            # Read the full text from the parent-owned tmpfile.  We trust the
            # child's ``char_count`` only as a hint and rely on the actual
            # file contents as the source of truth.
            try:
                full_text = Path(tmpfile_path).read_text(encoding="utf-8")
            except OSError as exc:
                raise RfpLightParseError(
                    f"failed to read RFP tmpfile {tmpfile_path}: {exc}"
                ) from exc
            extras: dict[str, Any] = {
                "reported_char_count": int(payload.get("char_count") or 0),
            }
            return (
                full_text,
                int(payload.get("page_count") or 0),
                bool(payload.get("truncated_pages") or False),
                str(payload.get("source_format") or ""),
                extras,
            )

        exc_kind = payload.get("exc_kind", "Exception")
        if exc_kind == "RfpLightParseInsufficient":
            raise RfpLightParseInsufficient(
                reason=str(payload.get("reason") or "extraction_failed"),
                message=str(payload.get("message") or ""),
                metadata=dict(payload.get("metadata") or {}),
            )
        raise RfpLightParseError(str(payload.get("message") or "RFP subprocess failed"))
    finally:
        try:
            parent_conn.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        try:
            process.close()
        except (ValueError, AttributeError):  # pragma: no cover - py<3.7 / already-closed
            pass
        # Always delete the tmpfile so a long-running backend never leaks
        # disk; the parent already holds the bytes in ``full_text``.
        try:
            os.unlink(tmpfile_path)
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover - best-effort cleanup
            logger.warning("Failed to clean RFP tmpfile %s: %s", tmpfile_path, exc)


# ============================================================================
# Public parser facade
# ============================================================================


class RfpLightParser:
    """Time-bounded RFP text extractor with subprocess hard-timeout.

    The parser is intentionally narrow: it never invokes docling, the Aliyun
    Docmind cloud parser, ``ParserService`` enrichment, embedders, or the
    Qdrant client.  Callers using this parser MUST also skip:

    - ``app.services.vectorstore.chunker.Chunker`` for indexing
    - ``app.services.vectorstore.embedder.Embedder``
    - ``app.services.vectorstore.qdrant_client.QdrantService.upsert_chunk``
    - ``app.api.documents._upsert_raw_document``
    - ``app.api.documents._replace_figure_assets``
    - ``app.services.knowledge.request_case_library_refresh``
    """

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def parse(self, file_path: str | Path) -> RfpLightParseResult:
        path = Path(file_path)
        if not path.exists():
            raise RfpLightParseError(f"RFP file not found: {path}")
        suffix = path.suffix.lower()
        if suffix in LEGACY_DOC_SUFFIXES:
            raise RfpLightParseInsufficient(
                reason="legacy_doc_format",
                message=(
                    "旧版 .doc 文件不在轻量解析支持范围内，请重新上传 .docx 或 .pdf。"
                ),
                metadata={"file_suffix": suffix},
            )
        if suffix not in ALL_SUPPORTED_SUFFIXES:
            raise RfpLightParseInsufficient(
                reason="unsupported_format",
                message=f"暂不支持的 RFP 文件格式：{suffix}",
                metadata={"file_suffix": suffix},
            )

        max_seconds = float(self._settings.rfp_light_parse_max_seconds)
        max_pages = int(self._settings.rfp_light_parse_max_pages)
        excerpt_chars = int(self._settings.rfp_light_parse_excerpt_chars)
        max_workers = int(self._settings.rfp_light_parse_max_workers)

        # Dedicated thread pool bounds how many subprocess watchdogs run in
        # parallel; the subprocess itself enforces the hard timeout via
        # SIGTERM -> SIGKILL inside ``_run_extract_with_subprocess``.
        executor = _get_rfp_light_executor(max_workers=max_workers)
        loop = asyncio.get_running_loop()

        started_at = time.perf_counter()
        text, page_count, truncated_pages, source_format, extras = await loop.run_in_executor(
            executor,
            _run_extract_with_subprocess,
            str(path),
            max_pages,
            max_seconds,
        )

        # Review R4 #1: ``text`` is now the *full* extracted body.  We do not
        # truncate to ``max_chars`` any more — that previously dropped the
        # back half of any RFP longer than 80,000 characters, and that
        # truncated copy was the only thing that ever reached
        # ``rfp_text_storage_path``.  The downstream "give the LLM at most N
        # chars" decision lives on ``excerpt`` (and will be replaced by
        # section-aware sampling under F3).
        normalized_text = text.replace("\u0000", "").strip()
        if not normalized_text:
            raise RfpLightParseInsufficient(
                reason="empty_text",
                message="RFP 文件中未能提取到任何文本内容。",
                metadata={"file_suffix": suffix, "source_format": source_format},
            )

        excerpt = normalized_text[:excerpt_chars]
        elapsed = time.perf_counter() - started_at
        logger.info(
            "RFP light parse ok: format=%s pages=%s chars=%s truncated_pages=%s elapsed=%.2fs",
            source_format,
            page_count,
            len(normalized_text),
            truncated_pages,
            elapsed,
        )
        result_metadata: dict[str, Any] = {
            "file_suffix": suffix,
            "excerpt_char_count": len(excerpt),
        }
        if extras:
            # Carry through any opportunistic hint from the subprocess (e.g.
            # the reported char count) so logs / debugging surfaces have it.
            result_metadata["subprocess_extras"] = dict(extras)
        return RfpLightParseResult(
            text=normalized_text,
            excerpt=excerpt,
            page_count=page_count,
            char_count=len(normalized_text),
            elapsed_seconds=elapsed,
            source_format=source_format,
            truncated_chars=False,
            truncated_pages=truncated_pages,
            metadata=result_metadata,
        )

    # Backward-compat: keep the old static helpers reachable on the class so
    # legacy callers / tests that patched ``RfpLightParser._extract_blocking``
    # still resolve a meaningful symbol.  The hot path no longer uses them
    # directly; subprocess work goes through the module-level functions above.
    _extract_plain_text = staticmethod(_extract_plain_text)
    _extract_docx = staticmethod(_extract_docx)
    _extract_pdf = staticmethod(_extract_pdf)

    @staticmethod
    def _extract_blocking(path: Path, max_pages: int) -> tuple[str, int, bool, str]:
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_TEXT_SUFFIXES:
            return _extract_plain_text(path), 0, False, "text"
        if suffix in SUPPORTED_DOCX_SUFFIXES:
            return _extract_docx(path), 0, False, "docx"
        if suffix in SUPPORTED_PDF_SUFFIXES:
            text, page_count, truncated = _extract_pdf(path, max_pages=max_pages)
            return text, page_count, truncated, "pdf"
        raise RfpLightParseInsufficient(
            reason="unsupported_format",
            message=f"暂不支持的 RFP 文件格式：{suffix}",
            metadata={"file_suffix": suffix},
        )


__all__ = [
    "ALL_SUPPORTED_SUFFIXES",
    "LEGACY_DOC_SUFFIXES",
    "SUPPORTED_DOCX_SUFFIXES",
    "SUPPORTED_PDF_SUFFIXES",
    "SUPPORTED_TEXT_SUFFIXES",
    "RfpLightParser",
    "RfpLightParseError",
    "RfpLightParseInsufficient",
    "RfpLightParseResult",
]
