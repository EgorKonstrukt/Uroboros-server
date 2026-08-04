import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from server.auth.routes import router as auth_router, yggdrasil_router
from server.web.admin import router as admin_router, _migrate_modpacks_from_json
from server.web import projects_router, launcher_router, news_router
from server.plugins import attach_to_app

_log = logging.getLogger("uroboros")


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: http: https:; "
        "connect-src 'self'; "
        "font-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/admin/static"):
            response.headers["Content-Security-Policy"] = self._CSP
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _migrate_modpacks_from_json()
    from server.mc.whitelist import sync_all_whitelists
    try:
        await sync_all_whitelists()
    except Exception:
        pass
    import threading
    from server.mc.java import scan_java
    threading.Thread(target=scan_java, daemon=True).start()
    from server.plugins import ensure_bootstrap, startup_all, shutdown_all
    ensure_bootstrap()
    await startup_all(app)
    yield
    await shutdown_all(app)


app = FastAPI(title="Uroboros Server", version="2.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(_SecurityHeadersMiddleware)

_admin_static = Path(__file__).parent / "web" / "static"


@app.exception_handler(Exception)
async def global_exception(request: Request, exc: Exception):
    _log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "errorMessage": "An internal error occurred"},
    )


app.include_router(auth_router, prefix="/auth")
app.include_router(yggdrasil_router)
app.include_router(admin_router, prefix="/admin")
app.include_router(projects_router, prefix="/projects")
app.include_router(news_router, prefix="/projects")
app.include_router(launcher_router, prefix="/launcher")

attach_to_app(app)

app.mount("/admin/static", StaticFiles(directory=str(_admin_static)), name="admin_static")


@app.get("/")
async def root(request: Request):
    host = request.url.hostname or "127.0.0.1"
    skin_domains = ["localhost", "127.0.0.1", "0.0.0.0", host]
    try:
        from server.config import ServerConfig
        public = (getattr(ServerConfig.load(), "public_url", "") or "").strip()
        if public:
            try:
                public_host = urlparse(public if "://" in public else f"//{public}").hostname
                if public_host:
                    skin_domains.append(public_host)
            except ValueError:
                pass
    except Exception:
        pass
    seen = []
    for d in skin_domains:
        if d and d not in seen:
            seen.append(d)
    return {
        "status": "ok",
        "server": "Uroboros Server",
        "version": "2.0.0",
        "meta": {
            "serverName": "Uroboros Server",
            "implementationName": "Uroboros Yggdrasil",
            "implementationVersion": "2.0.0",
            "feature.non_email_login": True,
            "feature.no_mojang_namespace": True,
        },
        "skinDomains": seen,
    }
