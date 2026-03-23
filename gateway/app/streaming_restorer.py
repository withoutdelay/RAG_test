from __future__ import annotations

from app.restorer import RestoreResult, Restorer


class StreamingRestorer:
    def __init__(self, *, session_id: str, restorer: Restorer | None = None) -> None:
        self.session_id = session_id
        self.restorer = restorer or Restorer()
        self._buffer = ""

    async def push(self, chunk: str) -> RestoreResult:
        text = self._buffer + chunk
        safe_text, self._buffer = self._split_safe_prefix(text)
        if not safe_text:
            return RestoreResult(restored_text="", restored_count=0)
        return await self.restorer.restore_text(session_id=self.session_id, text=safe_text)

    async def flush(self) -> RestoreResult:
        if not self._buffer:
            return RestoreResult(restored_text="", restored_count=0)

        result = await self.restorer.restore_text(session_id=self.session_id, text=self._buffer)
        self._buffer = ""
        return result

    @staticmethod
    def _split_safe_prefix(text: str) -> tuple[str, str]:
        last_open = text.rfind("[")
        last_close = text.rfind("]")
        if last_open == -1 or last_close > last_open:
            return text, ""
        return text[:last_open], text[last_open:]
