from fastapi import APIRouter, Depends

from app.api import artifacts, auth, documents, generation, library, projects, retrieval, review
from app.services.auth import require_authenticated_user


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

protected_router = APIRouter(dependencies=[Depends(require_authenticated_user)])
protected_router.include_router(projects.router, prefix="/projects", tags=["projects"])
protected_router.include_router(artifacts.router, tags=["artifacts"])
protected_router.include_router(documents.router, tags=["documents"])
protected_router.include_router(library.router, tags=["library"])
protected_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
protected_router.include_router(generation.router, prefix="/generation", tags=["generation"])
protected_router.include_router(review.router, tags=["review"])
api_router.include_router(protected_router)
