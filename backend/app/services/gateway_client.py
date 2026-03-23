from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings


class GatewayMaskedEntity(BaseModel):
    original: str
    placeholder: str
    type: str
    start: int
    end: int


class GatewayMaskData(BaseModel):
    masked_text: str
    entity_count: int
    entities_detected: list[GatewayMaskedEntity] = Field(default_factory=list)


class GatewayRestoreData(BaseModel):
    restored_text: str
    restored_count: int


class GatewayEnvelope(BaseModel):
    code: int
    message: str = "success"
    data: dict[str, Any]


class GatewayClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.gateway_url).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.masking_enabled = settings.gateway_masking_enabled

    async def mask_text(
        self,
        *,
        session_id: str,
        text: str,
        entity_types: list[str] | None = None,
    ) -> GatewayMaskData:
        if not self.masking_enabled:
            return GatewayMaskData(masked_text=text, entity_count=0, entities_detected=[])

        payload: dict[str, Any] = {"session_id": session_id, "text": text}
        if entity_types is not None:
            payload["entity_types"] = entity_types

        data = await self._post_json("/mask", payload)
        return GatewayMaskData.model_validate(data)

    async def restore_text(
        self,
        *,
        session_id: str,
        text: str,
    ) -> GatewayRestoreData:
        if not self.masking_enabled:
            return GatewayRestoreData(restored_text=text, restored_count=0)

        data = await self._post_json("/restore", {"session_id": session_id, "text": text})
        return GatewayRestoreData.model_validate(data)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Gateway request failed for {path}: {exc}") from exc

        envelope = GatewayEnvelope.model_validate(response.json())
        return envelope.data
