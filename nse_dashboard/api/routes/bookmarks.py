from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from nse_dashboard.api.deps import get_current_user
from nse_dashboard.services.bookmarks import BookmarkService


def create_router(service: BookmarkService) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/bookmarks", tags=["bookmarks"], dependencies=[Depends(get_current_user)]
    )

    @router.get("")
    async def list_bookmarks(user: dict = Depends(get_current_user)):
        return await run_in_threadpool(service.list, user["id"])

    @router.post("/{symbol}")
    async def follow(symbol: str, user: dict = Depends(get_current_user)):
        try:
            return await run_in_threadpool(service.follow, user["id"], symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/{symbol}")
    async def unfollow(symbol: str, user: dict = Depends(get_current_user)):
        try:
            deleted = await run_in_threadpool(service.unfollow, user["id"], symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        return {"symbol": symbol.strip().upper(), "deleted": True}

    return router
