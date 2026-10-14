"""通用工具：分页、客户端信息。"""
from __future__ import annotations

import math

from fastapi import Query

from ..config import settings


def paginate_params(
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
) -> tuple[int, int]:
    """所有列表接口统一分页，禁止一次性加载全部数据。"""
    return page, page_size


def total_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return max(1, math.ceil(total / page_size)) if total else 1


def page_offset(page: int, page_size: int) -> int:
    return max(0, (page - 1) * page_size)
