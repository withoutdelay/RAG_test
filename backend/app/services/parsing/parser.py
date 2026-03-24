from __future__ import annotations

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
        self.docling_parser = DoclingParser()
        self.table_parser = TableParser()
        self.image_extractor = ImageExtractor()

    async def parse_document(self, file_path: str) -> ParsedDocument:
        parsed = await self.docling_parser.parse(file_path)
        cleaned_markdown, cleaner_metadata = clean_markdown(parsed.markdown, file_path=file_path)
        tables = await self.table_parser.extract(cleaned_markdown)
        native_assets = await self.image_extractor.extract(file_path)
        assets = [*parsed.assets, *native_assets]

        metadata = {
            **parsed.metadata,
            **cleaner_metadata,
            "table_count": len(tables),
            "image_count": sum(1 for asset in assets if asset.asset_type != "table"),
            "figure_asset_count": len(assets),
        }
        metadata.update(
            build_document_profile(
                markdown=cleaned_markdown,
                metadata=metadata,
                assets=assets,
            ).to_metadata()
        )
        return ParsedDocument(markdown=cleaned_markdown, metadata=metadata, assets=assets)
