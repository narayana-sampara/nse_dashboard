from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from nse_dashboard.api.deps import get_current_user
from nse_dashboard.core.security import MENU_KEYS, create_access_token
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.auth_repository import AuthRepository


class LoginRequest(BaseModel):
    username: str
    password: str


def create_router(settings: Settings, auth_repo: AuthRepository) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.post("/login")
    async def login(payload: LoginRequest):
        user = await run_in_threadpool(auth_repo.get_user_by_username, payload.username)
        if user is None or not AuthRepository.verify_password(
            payload.password, user["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        permissions = (
            list(MENU_KEYS)
            if user["role"] == "admin"
            else await run_in_threadpool(auth_repo.get_permissions, user["id"])
        )
        token = create_access_token(
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
            secret=settings.jwt_secret,
            expires_minutes=settings.jwt_expires_minutes,
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "username": user["username"],
            "role": user["role"],
            "permissions": permissions,
        }

    @router.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        permissions = (
            list(MENU_KEYS)
            if user["role"] == "admin"
            else await run_in_threadpool(auth_repo.get_permissions, user["id"])
        )
        return {"username": user["username"], "role": user["role"], "permissions": permissions}

    return router
