from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import Settings, get_settings
from app.services.parsing.docling_parser import ParsedAsset, ParsedDocument


MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class AliyunDocMindParser:
    """Parser adapter for Alibaba Cloud Document Mind document parsing API."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def parse(self, file_path: str, *, include_assets: bool = True) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, Path(file_path), include_assets)

    def _parse_sync(self, path: Path, include_assets: bool) -> ParsedDocument:
        try:
            from alibabacloud_credentials.client import Client as CredentialClient
            from alibabacloud_docmind_api20220711.client import Client as DocMindClient
            from alibabacloud_docmind_api20220711 import models as docmind_models
            from alibabacloud_tea_openapi import models as open_api_models
            from alibabacloud_tea_util import models as util_models
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Aliyun DocMind SDK is not installed. Install backend parsing extras.") from exc

        original_path = path
        if not path.exists():
            raise FileNotFoundError(str(path))

        access_key_id = str(self.settings.aliyun_docmind_access_key_id or "").strip()
        access_key_secret = str(self.settings.aliyun_docmind_access_key_secret or "").strip()
        if not access_key_id or not access_key_secret:
            credential = CredentialClient()
            access_key_id = str(credential.get_credential().get_access_key_id() or "").strip()
            access_key_secret = str(credential.get_credential().get_access_key_secret() or "").strip()
        if not access_key_id or not access_key_secret:
            raise RuntimeError("Aliyun DocMind AccessKey is not configured")

        path, preparation_metadata, cleanup_prepared_path = self._prepare_cloud_input(path)
        try:
            config = open_api_models.Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
            config.endpoint = str(self.settings.aliyun_docmind_endpoint or "docmind-api.cn-hangzhou.aliyuncs.com")
            config.connect_timeout = 10000
            config.read_timeout = max(10000, int(min(float(self.settings.aliyun_docmind_timeout_seconds), 60.0) * 1000))
            client = DocMindClient(config)
            runtime = util_models.RuntimeOptions(
                connect_timeout=10000,
                read_timeout=max(10000, int(self.settings.aliyun_docmind_timeout_seconds * 1000)),
            )

            llm_enhancement = bool(self.settings.aliyun_docmind_llm_enhancement)
            output_html_table = bool(self.settings.aliyun_docmind_output_html_table) and llm_enhancement
            with path.open("rb") as handle:
                request = docmind_models.SubmitDocParserJobAdvanceRequest(
                    file_url_object=handle,
                    file_name=path.name,
                    file_name_extension=path.suffix.lower().lstrip("."),
                    formula_enhancement=bool(self.settings.aliyun_docmind_formula_enhancement),
                    llm_enhancement=llm_enhancement,
                    output_format=["markdown", "visualLayoutInfo"],
                    output_html_table=output_html_table,
                )
                enhancement_mode = str(self.settings.aliyun_docmind_enhancement_mode or "").strip()
                if enhancement_mode:
                    request.enhancement_mode = enhancement_mode
                submit_response = client.submit_doc_parser_job_advance(request, runtime)

            submit_payload = _to_plain(submit_response.body)
            job_id = _extract_docmind_job_id(submit_payload)
            if not job_id:
                raise RuntimeError(f"Aliyun DocMind did not return a job id: {submit_payload}")

            status_payload = self._wait_for_success(
                client=client,
                docmind_models=docmind_models,
                runtime=runtime,
                job_id=job_id,
            )
            markdown = self._extract_markdown_from_status(status_payload)
            if not markdown:
                result_payload = self._fetch_result_payload(
                    client=client,
                    docmind_models=docmind_models,
                    runtime=runtime,
                    job_id=job_id,
                    status_payload=status_payload,
                )
                markdown = _extract_markdown(result_payload)
            else:
                result_payload = {}
        finally:
            if cleanup_prepared_path is not None:
                cleanup_prepared_path()

        assets = self._extract_markdown_image_assets(markdown) if include_assets else []
        metadata = {
            "source_name": original_path.name,
            "parser": "aliyun-docmind",
            "parser_backend_requested": self.settings.parser_backend,
            "parser_backend_used": "aliyun_docmind",
            "docmind_endpoint": config.endpoint,
            "docmind_job_id": job_id,
            "docmind_status": _compact_payload(status_payload),
            "docmind_result": _compact_payload(result_payload),
            "format": path.suffix.lower().lstrip("."),
            "original_format": original_path.suffix.lower().lstrip("."),
            **preparation_metadata,
            "asset_extraction_enabled": include_assets,
            "image_count": len(assets),
            "figure_asset_count": len(assets),
            "docmind_llm_enhancement": llm_enhancement,
            "docmind_output_html_table": output_html_table,
        }
        if not markdown.strip():
            metadata["parse_gate_status"] = "parse_insufficient"
            metadata["parse_gate_reason"] = "aliyun_docmind_returned_empty_markdown"

        return ParsedDocument(markdown=markdown, metadata=metadata, assets=assets, structure={})

    def _prepare_cloud_input(self, path: Path) -> tuple[Path, dict[str, Any], Callable[[], None] | None]:
        threshold = int(self.settings.aliyun_docmind_convert_office_to_pdf_min_bytes or 0)
        suffix = path.suffix.lower()
        if threshold <= 0 or suffix not in {".doc", ".docx"}:
            return path, {}, None
        try:
            source_size = path.stat().st_size
        except OSError:
            return path, {}, None
        if source_size < threshold:
            return path, {}, None

        office_cmd = _resolve_office_converter(self.settings.docling_libreoffice_cmd)
        if not office_cmd:
            return path, {"docmind_input_conversion_skipped": "libreoffice_unavailable"}, None

        temp_dir = Path(tempfile.mkdtemp(prefix="docmind-office-pdf-"))
        try:
            completed = subprocess.run(
                [office_cmd, "--headless", "--convert-to", "pdf", "--outdir", str(temp_dir), str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max(60.0, min(float(self.settings.parser_document_timeout_seconds), 600.0)),
                check=False,
            )
            pdf_path = temp_dir / f"{path.stem}.pdf"
            if completed.returncode != 0 or not pdf_path.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                return path, {
                    "docmind_input_conversion_skipped": "office_to_pdf_failed",
                    "docmind_input_conversion_error": (completed.stderr or completed.stdout or "")[-1000:],
                }, None
            metadata = {
                "docmind_input_converted_to_pdf": True,
                "docmind_input_original_format": suffix.lstrip("."),
                "docmind_input_original_size_bytes": source_size,
                "docmind_input_pdf_size_bytes": pdf_path.stat().st_size,
            }
            return pdf_path, metadata, lambda: shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return path, {
                "docmind_input_conversion_skipped": "office_to_pdf_exception",
                "docmind_input_conversion_error": str(exc),
            }, None

    def _wait_for_success(self, *, client: Any, docmind_models: Any, runtime: Any, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(self.settings.aliyun_docmind_timeout_seconds))
        interval = max(0.5, float(self.settings.aliyun_docmind_poll_interval_seconds))
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            request = docmind_models.QueryDocParserStatusRequest(id=job_id)
            response = _call_docmind_method(client.query_doc_parser_status, request, runtime)
            payload = _to_plain(response.body)
            last_payload = payload if isinstance(payload, dict) else {}
            data = _payload_data(last_payload)
            status = str(data.get("Status") or data.get("status") or "").strip().lower()
            completed = status in {"success", "succeeded"}
            failed = status in {"fail", "failed"} or str(last_payload.get("Code") or "").lower() not in {"", "200", "ok"}
            if completed:
                return last_payload
            if failed:
                raise RuntimeError(f"Aliyun DocMind job failed: {last_payload}")
            time.sleep(interval)
        raise TimeoutError(f"Aliyun DocMind job timed out: {job_id}; last={last_payload}")

    def _extract_markdown_from_status(self, payload: dict[str, Any]) -> str:
        data = _payload_data(payload)
        output_items = data.get("OutputFormatResult") or data.get("outputFormatResult") or []
        if not isinstance(output_items, list):
            return ""
        for item in output_items:
            if not isinstance(item, dict):
                continue
            output_type = str(item.get("OutputType") or item.get("outputType") or "").strip().lower()
            output_url = str(item.get("OutputFileUrl") or item.get("outputFileUrl") or "").strip()
            if output_type == "markdown" and output_url:
                return _download_text(output_url, timeout_seconds=self.settings.aliyun_docmind_timeout_seconds)
        return ""

    def _fetch_result_payload(
        self,
        *,
        client: Any,
        docmind_models: Any,
        runtime: Any,
        job_id: str,
        status_payload: dict[str, Any],
    ) -> dict[str, Any]:
        data = _payload_data(status_payload)
        total = int(data.get("NumberOfSuccessfulParsing") or data.get("numberOfSuccessfulParsing") or 0)
        step = 200
        collected: list[Any] = []
        offset = 0
        while total <= 0 or offset < total:
            request = docmind_models.GetDocParserResultRequest(id=job_id, layout_num=offset, layout_step_size=step)
            response = _call_docmind_method(client.get_doc_parser_result, request, runtime)
            payload = _to_plain(response.body)
            page_data = _payload_data(payload)
            layouts = page_data.get("Layouts") or page_data.get("layouts") or page_data.get("Data") or []
            if isinstance(layouts, str):
                try:
                    layouts = json.loads(layouts)
                except json.JSONDecodeError:
                    layouts = []
            if not isinstance(layouts, list) or not layouts:
                return payload if not collected else {"Data": {"Layouts": collected}}
            collected.extend(layouts)
            offset += len(layouts)
            if total <= 0 and len(layouts) < step:
                break
        return {"Data": {"Layouts": collected}}

    def _extract_markdown_image_assets(self, markdown: str) -> list[ParsedAsset]:
        if not self.settings.aliyun_docmind_fetch_image_assets:
            return []
        max_assets = int(self.settings.aliyun_docmind_max_image_assets)
        if max_assets <= 0:
            return []
        assets: list[ParsedAsset] = []
        for index, match in enumerate(MARKDOWN_IMAGE_PATTERN.finditer(markdown)):
            if len(assets) >= max_assets:
                break
            title = (match.group(1) or "").strip() or f"DocMind Image {index + 1}"
            uri = (match.group(2) or "").strip()
            image_bytes, image_ext = _download_image(
                uri,
                timeout_seconds=self.settings.aliyun_docmind_timeout_seconds,
                max_bytes=int(self.settings.aliyun_docmind_max_image_bytes),
            )
            assets.append(
                ParsedAsset(
                    asset_type="figure",
                    page_no=None,
                    title=title,
                    caption=title,
                    heading_path=title,
                    context_before=None,
                    context_after=None,
                    bbox=None,
                    source_ref=uri,
                    image_bytes=image_bytes,
                    image_ext=image_ext,
                    meta={
                        "parser": "aliyun_docmind",
                        "visual_role": "engineering_figure",
                        "asset_uri": uri,
                        "external_asset": True,
                        "preserve_in_vector_db": True,
                    },
                )
            )
        return assets


def _to_plain(value: Any) -> Any:
    if hasattr(value, "to_map"):
        return value.to_map()
    if hasattr(value, "toMap"):
        return value.toMap()
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("Data") if isinstance(payload, dict) else None
    if data is None:
        data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    return data if isinstance(data, dict) else {}


def _extract_docmind_job_id(payload: dict[str, Any]) -> str:
    data = _payload_data(payload)
    return str(data.get("Id") or data.get("id") or payload.get("Id") or payload.get("id") or "").strip()


def _extract_markdown(payload: dict[str, Any]) -> str:
    candidates: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {"markdown", "markdowncontent", "markdown_content", "content"} and isinstance(
                    item, str
                ):
                    candidates.append(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    if candidates:
        return "\n\n".join(item.strip() for item in candidates if item.strip())
    layouts = _payload_data(payload).get("Layouts") or _payload_data(payload).get("layouts") or []
    if isinstance(layouts, list):
        lines = []
        for item in layouts:
            if not isinstance(item, dict):
                continue
            text = str(item.get("Text") or item.get("text") or item.get("Content") or item.get("content") or "").strip()
            if text:
                lines.append(text)
        return "\n\n".join(lines)
    return ""


def _call_docmind_method(method: Any, request: Any, runtime: Any) -> Any:
    try:
        return method(request, runtime)
    except TypeError as exc:
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        return method(request)


def _resolve_office_converter(configured_command: str | None) -> str | None:
    candidates = [str(configured_command or "").strip(), "libreoffice", "soffice"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _download_text(url: str, *, timeout_seconds: float) -> str:
    with httpx.Client(timeout=max(5.0, float(timeout_seconds))) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _download_image(url: str, *, timeout_seconds: float, max_bytes: int) -> tuple[bytes | None, str]:
    if not url.lower().startswith(("http://", "https://")):
        return None, Path(url).suffix or ".png"
    try:
        with httpx.Client(timeout=max(5.0, min(float(timeout_seconds), 60.0))) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
    except Exception:
        return None, Path(url).suffix or ".png"
    if max_bytes and len(content) > max_bytes:
        return None, Path(url).suffix or ".png"
    content_type = response.headers.get("content-type", "").lower()
    if "jpeg" in content_type:
        ext = ".jpg"
    elif "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    else:
        ext = Path(url.split("?", 1)[0]).suffix or ".png"
    return content, ext


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= 4000:
        return payload
    return {"truncated": True, "preview": text[:4000]}
