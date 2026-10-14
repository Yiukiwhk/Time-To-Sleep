"""API 路由包。"""
from __future__ import annotations

from fastapi import APIRouter

from . import account, admin, advice, auth, records, settings_api

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(records.router)
api_router.include_router(advice.router)
api_router.include_router(settings_api.router)
api_router.include_router(account.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
