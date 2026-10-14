"""统一错误响应与异常处理。"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core import logkit


class ApiError(Exception):
    """业务异常：detail 支持 i18n 键，前端按当前语言渲染。"""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        code: str | None = None,
        extra: dict | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code or detail
        self.extra = extra or {}


def _payload(detail: str, code: str | None, extra: dict | None = None) -> dict:
    body = {"detail": detail, "code": code or detail}
    if extra:
        body.update(extra)
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
        headers = {}
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            retry_after = exc.extra.get("retry_after_seconds")
            if retry_after is not None:
                headers["Retry-After"] = str(int(retry_after))
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.detail, exc.code, exc.extra),
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:  # noqa: ARG001
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        return JSONResponse(status_code=exc.status_code, content=_payload(detail, detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors()[:10]:
            loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
            errors.append({"field": loc, "message": item.get("msg", "invalid")})
        first = errors[0] if errors else {"field": "body", "message": "invalid"}
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "error.invalid_input",
                "code": "error.invalid_input",
                "field": first["field"],
                "message": first["message"],
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logkit.error("unhandled error on %s: %s", request.url.path, exc)
        from .core.security import client_ip

        logkit.write_system_log(
            level="ERROR",
            event="unhandled_exception",
            message=f"{type(exc).__name__}: {exc}",
            ip=client_ip(request),
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("error.server", "error.server"),
        )
