from fastapi import APIRouter

from app.api import artifacts, documents, generation, library, projects, retrieval, review


api_router = APIRouter()
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(artifacts.router, tags=["artifacts"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(library.router, tags=["library"])
api_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
api_router.include_router(generation.router, prefix="/generation", tags=["generation"])
api_router.include_router(review.router, tags=["review"])
