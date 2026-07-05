from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from nse_dashboard.core.security import decode_access_token
from nse_dashboard.infrastructure.auth_repository import AuthRepository


async def get_current_user(request: Request) -> dict:
    auth_repo: AuthRepository | None = getattr(request.app.state, "auth_repository", None)
    if auth_repo is None:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):]
    settings = request.app.state.settings
    try:
        payload = decode_access_token(token, settings.jwt_secret)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    user = await run_in_threadpool(auth_repo.get_user_by_id, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


async def require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def require_menu(menu_key: str) -> Callable:
    async def _dependency(request: Request) -> dict:
        user = await get_current_user(request)
        if user["role"] == "admin":
            return user
        auth_repo: AuthRepository = request.app.state.auth_repository
        permissions = await run_in_threadpool(auth_repo.get_permissions, user["id"])
        if menu_key not in permissions:
            raise HTTPException(status_code=403, detail=f"Access to '{menu_key}' is not permitted")
        return user

    return _dependency
