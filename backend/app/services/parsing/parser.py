from __future__ import annotations

from app.services.parsing.docling_parser import DoclingParser, ParsedDocument
from app.services.parsing.image_extractor import ImageExtractor
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
        tables = await self.table_parser.extract(parsed.markdown)
        images = await self.image_extractor.extract(file_path)

        metadata = {
            **parsed.metadata,
            "table_count": len(tables),
            "image_count": len(images),
        }
        return ParsedDocument(markdown=parsed.markdown, metadata=metadata)
