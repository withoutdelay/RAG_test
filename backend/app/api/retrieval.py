from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.schemas.common import APIResponse
from app.schemas.retrieval import RetrievalSearchRequest, RetrievalSearchResponse
from app.services.vectorstore.retriever import Retriever


router = APIRouter()


@router.post("/search", response_model=APIResponse[RetrievalSearchResponse])
async def search_retrieval(
    payload: RetrievalSearchRequest,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[RetrievalSearchResponse]:
    retriever = Retriever()
    data = await retriever.search(session=session, request=payload)
    return APIResponse(code=200, message="success", data=data)
