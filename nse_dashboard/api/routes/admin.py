from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from nse_dashboard.api.deps import require_admin
from nse_dashboard.core.security import MENU_KEYS
from nse_dashboard.infrastructure.auth_repository import AuthRepository


class CreateUserRequest(BaseModel):
    username: str
    password: str


class SetPermissionsRequest(BaseModel):
    menu_keys: list[str]


def create_router(auth_repo: AuthRepository) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)]
    )

    @router.get("/users")
    async def list_users():
        return await run_in_threadpool(auth_repo.list_users_with_permissions)

    @router.post("/users")
    async def create_user(payload: CreateUserRequest):
        if await run_in_threadpool(auth_repo.get_user_by_username, payload.username):
            raise HTTPException(status_code=409, detail="Username already exists")
        return await run_in_threadpool(
            auth_repo.create_user, payload.username, payload.password, "user"
        )

    @router.put("/users/{user_id}/permissions")
    async def set_permissions(user_id: int, payload: SetPermissionsRequest):
        invalid = set(payload.menu_keys) - set(MENU_KEYS)
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown menu keys: {sorted(invalid)}")
        if await run_in_threadpool(auth_repo.get_user_by_id, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        await run_in_threadpool(auth_repo.set_permissions, user_id, payload.menu_keys)
        return {"user_id": user_id, "menu_keys": payload.menu_keys}

    return router
