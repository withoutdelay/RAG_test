from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.audit import AuditRecorder
from app.mapping_store import get_mapping_store
from app.masker import Masker
from app.restorer import Restorer
from app.schemas import APIResponse, MaskRequest, MaskResponseData, MaskedEntityRead, RestoreRequest, RestoreResponseData


def get_masker() -> Masker:
    return Masker()


def get_restorer() -> Restorer:
    return Restorer()


def get_audit_recorder() -> AuditRecorder:
    return AuditRecorder()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await get_mapping_store().aclose()


app = FastAPI(title="Presale Copilot Gateway", version="0.2.0", lifespan=lifespan)


@app.post("/mask", response_model=APIResponse[MaskResponseData])
async def mask_text(
    payload: MaskRequest,
    masker: Masker = Depends(get_masker),
    audit: AuditRecorder = Depends(get_audit_recorder),
) -> APIResponse[MaskResponseData]:
    try:
        result = await masker.mask_text(
            session_id=payload.session_id,
            text=payload.text,
            entity_types=payload.entity_types,
        )
    except Exception as exc:
        audit.record(
            session_id=payload.session_id,
            direction="mask",
            entity_types=payload.entity_types,
            success=False,
            error_message=str(exc),
        )
        raise

    audit.record(
        session_id=payload.session_id,
        direction="mask",
        entity_count=result.entity_count,
        entity_types=sorted({entity.entity_type for entity in result.entities_detected}),
        success=True,
    )
    return APIResponse(
        code=200,
        message="success",
        data=MaskResponseData(
            masked_text=result.masked_text,
            entity_count=result.entity_count,
            entities_detected=[
                MaskedEntityRead(
                    original=entity.original,
                    placeholder=entity.placeholder,
                    type=entity.entity_type,
                    start=entity.start,
                    end=entity.end,
                )
                for entity in result.entities_detected
            ],
        ),
    )


@app.post("/restore", response_model=APIResponse[RestoreResponseData])
async def restore_text(
    payload: RestoreRequest,
    restorer: Restorer = Depends(get_restorer),
    audit: AuditRecorder = Depends(get_audit_recorder),
) -> APIResponse[RestoreResponseData]:
    try:
        result = await restorer.restore_text(session_id=payload.session_id, text=payload.text)
    except Exception as exc:
        audit.record(
            session_id=payload.session_id,
            direction="restore",
            success=False,
            error_message=str(exc),
        )
        raise

    audit.record(
        session_id=payload.session_id,
        direction="restore",
        entity_count=result.restored_count,
        success=True,
    )
    return APIResponse(
        code=200,
        message="success",
        data=RestoreResponseData(
            restored_text=result.restored_text,
            restored_count=result.restored_count,
        ),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
