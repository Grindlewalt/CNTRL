"""Operator routes for published pages (backend/pages.py): the live list, the
pending requests, and the decisions. Cookie session only; the agent has no route."""
from fastapi import APIRouter, Depends, HTTPException

from . import pages
from .auth import require_user

router = APIRouter(prefix="/api/pages", tags=["pages"],
                   dependencies=[Depends(require_user)])


async def _decide(fn, pid: str):
    try:
        return await fn(pid)
    except pages.PageError as e:
        raise HTTPException(400, str(e)) from e


@router.get("")
async def list_live():
    return {"items": await pages.list_pages("live")}


@router.get("/requests")
async def list_requests():
    return {"items": await pages.list_pages("pending")}


@router.post("/requests/{pid}/approve")
async def approve(pid: str):
    page = await _decide(pages.approve, pid)
    return {"ok": True, "url": page["url"]}


@router.post("/requests/{pid}/reject")
async def reject(pid: str):
    await _decide(pages.reject, pid)
    return {"ok": True}


@router.post("/{pid}/unpublish")
async def unpublish(pid: str):
    await _decide(pages.unpublish, pid)
    return {"ok": True}
