from __future__ import annotations

import json
import logging


class AuditRecorder:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("presale.gateway.audit")

    def record(
        self,
        *,
        session_id: str,
        direction: str,
        entity_count: int | None = None,
        entity_types: list[str] | None = None,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        payload = {
            "session_id": session_id,
            "direction": direction,
            "entity_count": entity_count,
            "entity_types": entity_types,
            "success": success,
            "error_message": error_message,
        }
        self._logger.info("masking_audit %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
