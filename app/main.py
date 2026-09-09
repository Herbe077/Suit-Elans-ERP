"""Punto de entrada FastAPI — Suit Elans ERP & MES."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware

from app.core.config import BASE_DIR, settings
from app.core.database import Base, engine
from app.core.deps import get_current_user_optional
from app.api import v1 as api_v1
from app.models import *  # noqa: F401,F403 — registra modelos para create_all
from app.routers import admin, auth, comercial, dashboard, finanzas, inventario, legacy, produccion, reportes, rendimiento, taller_cierre, ventas
from app.modules.rendimiento.router import router as rendimiento_router

log = logging.getLogger("suitelans.errors")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev (SQLite): crea tablas si no existen.
    # Prod (PostgreSQL/Neon): ejecuta `alembic upgrade head` automáticamente.
    if settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
        try:
            import sys
            if str(BASE_DIR) not in sys.path:
                sys.path.insert(0, str(BASE_DIR))
            from seed_admin import ensure_admin
            ensure_admin()
        except Exception:
            log.warning("seed admin omitido", exc_info=True)
    else:
        # Prod: pipeline auto-reparable (migraciones + PCGE + admin). Nunca tumba el boot.
        from app.core.startup import init_production_db
        app.state.db_init = init_production_db()
    log.info("SuitElans iniciado; DB=%s", _safe_db_label())
    yield


def _safe_db_label() -> str:
    """URL sin password para logs."""
    import re
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", settings.database_url_normalized)


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Red de seguridad: traceback completo a logs; respuesta según cliente.

    - API/fetch (Accept: application/json o ruta /api*): JSON 500.
    - Navegador (vistas HTML): página 500 amigable en vez de JSON crudo.
    Los handlers específicos (HTTPException, validación) siguen teniendo
    prioridad.
    """
    log.exception("500 %s %s: %s", request.method, request.url.path, exc)
    accept = request.headers.get("accept", "")
    quiere_html = "text/html" in accept and not request.url.path.startswith("/api")
    if quiere_html:
        return HTMLResponse(
            status_code=500,
            content="""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error interno — Suit Elans ERP</title></head>
<body style="font-family:system-ui,sans-serif;background:#FAF8F5;color:#333;
display:flex;align-items:center;justify-content:center;min-height:90vh;margin:0">
<main style="max-width:520px;background:#fff;border:1px solid #e5ddd2;
border-radius:16px;padding:32px;text-align:center">
<h1 style="margin:0 0 8px">⚠️ Error interno del servidor</h1>
<p>Ocurrió un problema inesperado. El equipo ya fue notificado
(revisa los logs del servidor).</p>
<p><a href="/" style="color:#7a0c2e;font-weight:bold">← Volver al inicio</a></p>
</main></body></html>""",
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor (revisa los logs)."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENV}


@app.get("/health/db")
def health_db(request: Request):
    """Diagnóstico de BD (conteos, sin datos sensibles). Nunca 500."""
    out: dict = {"status": "ok"}
    try:
        from app.core.database import SessionLocal
        from app.models.finanzas import CuentaContable
        from app.models.user import User
        db = SessionLocal()
        try:
            out["usuarios"] = db.query(User).count()
            out["cuentas_pcge"] = db.query(CuentaContable).count()
        finally:
            db.close()
        try:
            from alembic.migration import MigrationContext
            from app.core.database import engine
            with engine.connect() as conn:
                ctx = MigrationContext.configure(conn)
                out["alembic"] = ctx.get_current_heads()
        except Exception as e:
            out["alembic"] = f"?: {e}"
        init = getattr(request.app.state, "db_init", None)
        if init:
            out["init"] = init
    except Exception as e:
        out = {"status": "error"}
        if not settings.is_production:
            out["detail"] = str(e)[:200]
    return out


@app.middleware("http")
async def require_login(request: Request, call_next):
    public = ("/auth/login", "/health", "/static", "/manifest", "/docs", "/openapi.json", "/api")
    if request.url.path.startswith(public):
        return await call_next(request)
    # Sesión vía cookie; get_current_user_optional necesita DB — chequeo ligero aquí
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User

    token = request.cookies.get(security.COOKIE_NAME)
    ok = False
    payload = security.decode_token(token) if token else None
    if payload and payload.get("sub"):
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.email == payload["sub"]).first()
            ok = bool(u and u.is_active)
        finally:
            db.close()
    if not ok:
        return RedirectResponse("/auth/login", status_code=303)
    return await call_next(request)


app.include_router(auth.router, prefix="/auth")
app.include_router(dashboard.router)   # / y /dashboard
app.include_router(comercial.router)   # /comercial/...
app.include_router(produccion.router)  # /produccion/... (kanban, fichas, pruebas, calidad)
app.include_router(taller_cierre.router)  # módulo aislado: cierre de jornada y pagos
app.include_router(ventas.router)      # /ventas/...
app.include_router(inventario.router)  # /inventario/...
app.include_router(finanzas.router)    # /finanzas/... (estricto ADMIN/GERENTE/CONTADOR)
app.include_router(rendimiento_router) # /rendimiento/... (destajo operario, app/modules/rendimiento)
app.include_router(admin.router)
app.include_router(reportes.router)
app.include_router(legacy.router)  # redirecciones 302 de rutas antiguas
app.include_router(api_v1.router)  # JSON para web futura / Odoo (Bearer JWT)


@app.get("/manifest.json", include_in_schema=False)
def manifest():
    from fastapi.responses import FileResponse

    return FileResponse(str(BASE_DIR / "app" / "static" / "manifest.json"), media_type="application/manifest+json")
