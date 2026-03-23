from __future__ import annotations

class TableParser:
    async def extract(self, markdown: str) -> list[dict]:
        tables: list[dict] = []
        blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
        for index, block in enumerate(blocks):
            if "|" in block and "\n|" in block:
                tables.append({"index": index, "kind": "markdown_table"})
        return tables
