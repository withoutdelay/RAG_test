from __future__ import annotations

from app.config import get_settings
from app.services.parsing.asset_review import AssetReviewService, AssetReviewStats
from app.services.parsing.asset_semantic_summary import AssetSemanticSummaryService, AssetSemanticSummaryStats
from app.services.parsing.document_profile import build_document_profile
from app.services.parsing.docling_parser import DoclingParser, ParsedDocument
from app.services.parsing.image_extractor import ImageExtractor
from app.services.parsing.markdown_cleaner import clean_markdown
from app.services.parsing.table_parser import TableParser


class ParserService:
    """
    Dispatch document parsing and keep extension points explicit.

    Phase 2 uses the document parser output directly, while table/image hooks stay
    as no-op placeholders until richer multimodal extraction is wired in.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.docling_parser = DoclingParser()
        self.table_parser = TableParser()
        self.image_extractor = ImageExtractor()
        self.asset_review_service = AssetReviewService(settings=self.settings)
        self.asset_summary_service = AssetSemanticSummaryService(settings=self.settings)

    async def parse_document(self, file_path: str, *, include_asset_enrichment: bool = True) -> ParsedDocument:
        parsed = await self.docling_parser.parse(file_path, include_assets=include_asset_enrichment)
        cleaned_markdown, cleaner_metadata = clean_markdown(parsed.markdown, file_path=file_path)
        tables = await self.table_parser.extract(cleaned_markdown)
        native_assets = await self.image_extractor.extract(file_path) if include_asset_enrichment else []
        assets = [*parsed.assets, *native_assets]
        if include_asset_enrichment:
            assets, asset_review_stats = await self.asset_review_service.review_assets(assets)
            assets, asset_summary_stats = await self.asset_summary_service.summarize_assets(assets)
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
