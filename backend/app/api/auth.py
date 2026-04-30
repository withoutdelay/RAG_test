from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.schemas.auth import AuthUser, LoginRequest
from app.schemas.common import APIResponse
from app.services.auth import (
    AuthenticatedUser,
    authenticate_credentials,
    create_session_token,
    expired_session_cookie_kwargs,
    require_authenticated_user,
    session_cookie_kwargs,
)


router = APIRouter()


@router.post("/login", response_model=APIResponse[AuthUser])
async def login(payload: LoginRequest, response: Response) -> APIResponse[AuthUser]:
    user = authenticate_credentials(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    token = create_session_token(user)
    response.set_cookie(value=token, **session_cookie_kwargs())
    return APIResponse(code=200, message="success", data=AuthUser(username=user.username))


@router.post("/logout", response_model=APIResponse[AuthUser])
async def logout(response: Response) -> APIResponse[AuthUser]:
    response.delete_cookie(**expired_session_cookie_kwargs())
    return APIResponse(code=200, message="success", data=AuthUser(username="", authenticated=False))


@router.get("/me", response_model=APIResponse[AuthUser])
async def me(user: AuthenticatedUser = Depends(require_authenticated_user)) -> APIResponse[AuthUser]:
    return APIResponse(code=200, message="success", data=AuthUser(username=user.username, authenticated=user.authenticated))

