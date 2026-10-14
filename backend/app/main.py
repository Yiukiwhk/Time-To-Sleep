"""Application entrypoint: FastAPI instance, middleware, static hosting, cleanup task.

NOTE: this file is intentionally ASCII-only. Python defaults to the locale
codepage (cp936 on zh-CN Windows) when reading source files without a BOM,
which corrupted Chinese literals in earlier revisions.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import models
from .api import api_router
from .config import settings
from .core import logkit
from .core.security import hash_password
from .db import init_db, write_session
from .errors import install_error_handlers
from .models import utcnow
from .services.retention import run_cleanup

logger = logkit.get_logger("main")


# --------------------------------------------------------------------------- #
# Startup bootstrap
# --------------------------------------------------------------------------- #
def bootstrap_admin() -> None:
    """Create the built-in administrator on first launch (username + N password parts).

    Part 1 is the account password; remaining parts are stored hashed in
    admin_credentials and are additionally required by the admin login endpoint.
    """
    username = settings.BOOTSTRAP_ADMIN_USERNAME
    passwords = list(settings.BOOTSTRAP_ADMIN_PASSWORDS)
    promoted = False
    created = False

    with write_session() as session:
        existing = session.execute(
            select(models.User).where(models.User.username == username)
        ).scalar_one_or_none()
        if existing is not None:
            if not existing.is_admin:
                existing.is_admin = True
                promoted = True
        else:
            admin = models.User(
                username=username,
                password_hash=hash_password(passwords[0]),
                is_admin=True,
                is_active=True,
                language="zh",
                theme="dark",
            )
            session.add(admin)
            session.flush()
            for index, secret in enumerate(passwords[1:], start=1):
                session.add(
                    models.AdminCredential(
                        user_id=admin.id,
                        part_index=index,
                        part_hash=hash_password(secret),
                        label=f"part {index + 1}",
                    )
                )
            created = True

    # Log outside the transaction: SQLite allows a single writer, and logging
    # while the write transaction is still open would deadlock on the lock.
    if promoted:
        logger.warning("bootstrap: existing user %r promoted to administrator", username)
    if created:
        logger.warning(
            "bootstrap: administrator %r created with %d password parts - change them after login",
            username,
            len(passwords),
        )


async def _cleanup_loop() -> None:
    """Periodically purge expired sessions, old login attempts, old logs and records."""
    interval = max(60, int(settings.CLEANUP_INTERVAL_SECONDS))
    while True:
        try:
            await asyncio.sleep(interval)
            result = await asyncio.to_thread(run_cleanup)
            if result:
                logger.info("cleanup: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - cleanup failures must not kill the app
            logger.exception("cleanup task failed")
            await asyncio.sleep(60)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    logkit.install_db_log_handler()
    bootstrap_admin()
    try:
        run_cleanup()
    except Exception:  # pragma: no cover
        logger.exception("startup cleanup failed")
    task = asyncio.create_task(_cleanup_loop(), name="ssp-cleanup")
    logger.info(
        "service started: http://%s:%s (db=%s, frontend=%s)",
        settings.HOST,
        settings.PORT,
        settings.DB_PATH,
        settings.FRONTEND_DIR,
    )
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("service stopped")


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(
        title="Sleep Schedule Advisor",
        description="Generate sleep-schedule adjustment advice from historical sleep records",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
    )

    # CORS: only the configured frontend origins may call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_origin_regex=settings.CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "X-Requested-With"],
        max_age=600,
    )

    install_error_handlers(app)
    app.include_router(api_router)

    _install_frontend(app)
    return app


def _install_frontend(app: FastAPI) -> None:
    """Serve the frontend static files; SPA routes fall back to the matching index.html."""
    frontend_dir = Path(settings.FRONTEND_DIR)
    if not frontend_dir.exists():
        logger.warning("frontend directory missing, static hosting disabled: %s", frontend_dir)
        return

    user_index = frontend_dir / "user" / "index.html"
    admin_index = frontend_dir / "admin" / "index.html"

    @app.get("/healthz", include_in_schema=False)
    def _healthz() -> dict:
        return {"status": "ok", "time": utcnow().isoformat(timespec="seconds")}

    if (frontend_dir / "shared").exists():
        app.mount(
            "/shared",
            StaticFiles(directory=str(frontend_dir / "shared")),
            name="shared",
        )

    if (frontend_dir / "user").exists():
        app.mount(
            "/user",
            StaticFiles(directory=str(frontend_dir / "user"), html=True),
            name="user",
        )

    if (frontend_dir / "admin").exists():
        app.mount(
            "/admin",
            StaticFiles(directory=str(frontend_dir / "admin"), html=True),
            name="admin",
        )

    @app.get("/", include_in_schema=False)
    def _root():
        if user_index.exists():
            return RedirectResponse(url="/user/")
        return JSONResponse(status_code=404, content={"detail": "error.not_found"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(request: Request, full_path: str):
        """Serve a real file when it exists; otherwise fall back to the matching SPA shell."""
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": "error.not_found", "code": "error.not_found"},
            )
        root = frontend_dir.resolve()
        candidate = (frontend_dir / full_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"detail": "error.not_found", "code": "error.not_found"},
            )
        if full_path and candidate.is_file():
            return FileResponse(candidate)

        if full_path.startswith("admin") and admin_index.exists():
            return FileResponse(admin_index)
        if user_index.exists():
            return FileResponse(user_index)
        return JSONResponse(status_code=404, content={"detail": "error.not_found"})


app = create_app()
