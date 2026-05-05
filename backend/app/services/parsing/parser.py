from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.services.parsing.aliyun_docmind_parser import AliyunDocMindParser
from app.services.parsing.asset_review import AssetReviewService, AssetReviewStats
from app.services.parsing.asset_quality import apply_asset_quality_gate
from app.services.parsing.asset_semantic_summary import AssetSemanticSummaryService, AssetSemanticSummaryStats
from app.services.parsing.document_profile import build_document_profile
from app.services.parsing.docling_parser import DoclingParser, ParsedDocument
from app.services.parsing.image_extractor import ImageExtractor
from app.services.parsing.markdown_cleaner import clean_markdown
from app.services.parsing.process_runner import (
    DocumentParseProcessFailedError,
    DocumentParseProcessTimeoutError,
    parse_document_in_subprocess,
)
from app.services.parsing.table_parser import TableParser


class CloudParseRequiredError(RuntimeError):
    """Raised when local parsing is unsafe or insufficient and cloud parsing is required."""


class ParserService:
    """
    Dispatch document parsing and keep extension points explicit.

    Phase 2 uses the document parser output directly, while table/image hooks stay
    as no-op placeholders until richer multimodal extraction is wired in.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.docling_parser = DoclingParser()
        self.aliyun_docmind_parser = AliyunDocMindParser(settings=self.settings)
        self.table_parser = TableParser()
        self.image_extractor = ImageExtractor()
        self.asset_review_service = AssetReviewService(settings=self.settings)
        self.asset_summary_service = AssetSemanticSummaryService(settings=self.settings)

    async def parse_document(self, file_path: str, *, include_asset_enrichment: bool = True) -> ParsedDocument:
        if self.settings.parser_backend in {"aliyun_docmind", "docmind"}:
            return await self._parse_document_inline(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
            )

        try:
            if self.settings.parser_process_isolation_enabled:
                parsed = await parse_document_in_subprocess(
                    file_path,
                    include_asset_enrichment=include_asset_enrichment,
                    timeout_seconds=self.settings.parser_document_timeout_seconds,
                )
            else:
                parsed = await self._parse_document_inline(
                    file_path,
                    include_asset_enrichment=include_asset_enrichment,
                )
        except (DocumentParseProcessTimeoutError, DocumentParseProcessFailedError) as exc:
            return await self._parse_with_cloud_fallback_or_raise(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
                reason=str(exc),
            )

        if self._needs_cloud_fallback(parsed):
            return await self._parse_with_cloud_fallback_or_raise(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
                reason=self._cloud_fallback_reason(parsed),
            )
        return parsed

    async def _parse_document_inline(
        self,
        file_path: str,
        *,
        include_asset_enrichment: bool = True,
        parser_backend_override: str | None = None,
        metadata_overrides: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        parser_backend = parser_backend_override or self.settings.parser_backend
        if parser_backend in {"aliyun_docmind", "docmind"}:
            parsed = await self.aliyun_docmind_parser.parse(file_path, include_assets=include_asset_enrichment)
        else:
            parsed = await self.docling_parser.parse(file_path, include_assets=include_asset_enrichment)
        cleaned_markdown, cleaner_metadata = clean_markdown(parsed.markdown, file_path=file_path)
        tables = await self.table_parser.extract(cleaned_markdown)
        native_assets = await self.image_extractor.extract(file_path) if include_asset_enrichment else []
        assets = [*parsed.assets, *native_assets]
        if include_asset_enrichment:
            assets, asset_review_stats = await self.asset_review_service.review_assets(assets)
            assets, asset_summary_stats = await self.asset_summary_service.summarize_assets(assets)
            if self.settings.parser_asset_quality_gate_enabled:
                assets = apply_asset_quality_gate(
                    assets,
                    confidence_threshold=self.settings.parser_llm_asset_review_confidence_threshold,
                )
        else:
            asset_review_stats = AssetReviewStats()
            asset_summary_stats = AssetSemanticSummaryStats()

        metadata = {
            **parsed.metadata,
            **cleaner_metadata,
            "table_count": len(tables),
            "image_count": sum(1 for asset in assets if asset.asset_type != "table"),
            "figure_asset_count": len(assets),
            "asset_llm_review_enabled": self.settings.parser_llm_asset_review_enabled,
            "asset_llm_reviewed_count": asset_review_stats.reviewed_count,
            "asset_llm_override_count": asset_review_stats.overridden_count,
            "asset_llm_title_refined_count": asset_review_stats.title_refined_count,
            "asset_llm_vision_attached_count": asset_review_stats.vision_attached_count,
            "asset_llm_summary_enabled": self.settings.parser_llm_asset_summary_enabled,
            "asset_llm_summary_candidate_count": asset_summary_stats.candidate_count,
            "asset_llm_summarized_count": asset_summary_stats.summarized_count,
            "asset_llm_summary_vision_attached_count": asset_summary_stats.vision_attached_count,
            "asset_llm_summary_request_count": asset_summary_stats.request_count,
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)
        if asset_review_stats.error:
            metadata["asset_llm_review_error"] = asset_review_stats.error
        if asset_summary_stats.error:
            metadata["asset_llm_summary_error"] = asset_summary_stats.error
        metadata.update(
            build_document_profile(
                markdown=cleaned_markdown,
                metadata=metadata,
                assets=assets,
            ).to_metadata()
        )
        return ParsedDocument(
            markdown=cleaned_markdown,
            metadata=metadata,
            assets=assets,
            structure=parsed.structure,
        )

    async def parse_document_with_cloud(
        self,
        file_path: str,
        *,
        include_asset_enrichment: bool = True,
        reason: str = "cloud_parser_selected",
    ) -> ParsedDocument:
        if not self.settings.parser_cloud_fallback_enabled and self.settings.parser_backend not in {
            "aliyun_docmind",
            "docmind",
        }:
            raise CloudParseRequiredError(
                "Aliyun DocMind parsing is required for this document, but cloud parsing is disabled. "
                f"reason={reason}"
            )
        try:
            return await self._parse_document_inline(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
                parser_backend_override="aliyun_docmind",
                metadata_overrides={
                    "parser_cloud_direct": True,
                    "parser_cloud_direct_reason": reason,
                    "requires_cloud_parse": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise CloudParseRequiredError(
                "Aliyun DocMind parsing is required for this document, but the cloud parser could not run. "
                "Configure ALIYUN_DOCMIND_ACCESS_KEY_ID/ALIYUN_DOCMIND_ACCESS_KEY_SECRET "
                f"or set PARSER_BACKEND=aliyun_docmind after purchasing the service. reason={reason}; "
                f"cloud_error={exc}"
            ) from exc

    async def _parse_with_cloud_fallback_or_raise(
        self,
        file_path: str,
        *,
        include_asset_enrichment: bool,
        reason: str,
    ) -> ParsedDocument:
        try:
            return await self.parse_document_with_cloud(
                file_path,
                include_asset_enrichment=include_asset_enrichment,
                reason=f"local_parser_fallback:{reason}",
            )
        except CloudParseRequiredError as exc:
            raise CloudParseRequiredError(
                "Local document parsing failed or produced insufficient output, and Aliyun DocMind fallback "
                f"could not run. local_reason={reason}; cloud_error={exc}"
            ) from exc

    def _needs_cloud_fallback(self, parsed: ParsedDocument) -> bool:
        metadata = dict(parsed.metadata or {})
        parse_gate_status = str(metadata.get("parse_gate_status") or "").strip().lower()
        parse_gate_reason = str(metadata.get("parse_gate_reason") or "").strip().lower()
        parser_backend_used = str(metadata.get("parser_backend_used") or metadata.get("parser_backend") or "").strip().lower()
        if parse_gate_reason == "fallback_binary_parser":
            return True
        if parse_gate_status in {"insufficient", "parse_insufficient"} and parser_backend_used in {"fallback", "docling"}:
            return True
        if parser_backend_used == "fallback" and not str(parsed.markdown or "").strip():
            return True
        return False

    def _cloud_fallback_reason(self, parsed: ParsedDocument) -> str:
        metadata = dict(parsed.metadata or {})
        return (
            str(metadata.get("parse_gate_reason") or "").strip()
            or str(metadata.get("parser_backend_used") or metadata.get("parser_backend") or "").strip()
            or "local_parser_insufficient"
        )
