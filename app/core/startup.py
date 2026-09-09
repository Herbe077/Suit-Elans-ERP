"""Inicialización de producción (Neon/PostgreSQL).

`init_production_db()` deja la BD lista en el arranque aunque parta vacía:
  1. `alembic upgrade head` con reintentos (Neon puede tardar en aceptar).
  2. Verificación de tablas críticas; si faltan, `create_all` + `stamp head`
     (último recurso para no dejar la app en 503).
  3. Seed idempotente: plan contable PCGE + usuario admin inicial.

Nunca lanza: devuelve dict de estado y loguea el traceback completo.
"""
import logging
import time

log = logging.getLogger("suitelans")

CRITICAL_TABLES = ("users", "cuentas_contables", "asientos_contables",
                   "caja_turnos", "cash_movements", "clients", "orders")


def _alembic_cfg():
    from alembic.config import Config

    from app.core.config import BASE_DIR, settings

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url_normalized)
    return cfg


def _upgrade_with_retries(attempts: int = 3, delay: float = 5.0) -> str:
    from alembic import command

    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            command.upgrade(_alembic_cfg(), "head")
            return f"upgraded (intento {i})"
        except Exception as e:
            last = e
            log.warning("alembic upgrade intento %s/%s falló: %s", i, attempts, e)
            time.sleep(delay)
    log.exception("alembic upgrade falló tras %s intentos: %s", attempts, last)
    return f"failed: {last}"


def _missing_tables() -> list[str]:
    from sqlalchemy import inspect

    from app.core.database import engine

    try:
        have = set(inspect(engine).get_table_names())
    except Exception as e:
        log.exception("no se pudo inspeccionar la BD: %s", e)
        return list(CRITICAL_TABLES)
    return [t for t in CRITICAL_TABLES if t not in have]


def _fallback_create_and_stamp() -> str:
    from alembic import command

    from app.core.database import Base, engine

    Base.metadata.create_all(bind=engine)
    try:
        command.stamp(_alembic_cfg(), "head")
    except Exception as e:
        log.warning("stamp head omitido: %s", e)
    return "create_all+stamp"


def init_production_db() -> dict:
    """Ejecuta el pipeline de producción. Idempotente y sin excepciones.

    SQLite no ejecuta Alembic de producción: los tests/dev ya materializan
    el esquema con SQLAlchemy. Evitar intentar migraciones históricas sobre
    una BD SQLite creada desde metadata elimina reintentos y esperas espurias.
    """
    from app.core.database import SessionLocal
    from app.core.config import settings
    from app.services.plan_operativo import asegurar_plan_operativo

    status: dict = {"migrations": "skipped", "tables": [], "pcge": "skipped",
                    "admin": "skipped", "schema": "skipped"}
    try:
        if settings.is_sqlite:
            missing = _missing_tables()
            status["tables"] = missing
            db = SessionLocal()
            try:
                asegurar_plan_operativo(db)
                db.commit()
                from app.models.user import User
                status["admin"] = "exists" if db.query(User).first() else "skipped"
            finally:
                db.close()
            status["pcge"] = "ok"
            return status
        status["migrations"] = _upgrade_with_retries()
        if status["migrations"].startswith("failed"):
            # Alembic estancado (versión previa corrupta/conflictiva):
            # fuerza el sello a head para no quedar bloqueado y deja que
            # ensure_runtime_schema sanee el DDL real abajo.
            try:
                from alembic import command
                command.stamp(_alembic_cfg(), "head")
                status["migrations"] += " | forced stamp head"
                log.warning("alembic con fallo: stamp head forzado")
            except Exception as e:
                log.warning("forced stamp omitido: %s", e)
        missing = _missing_tables()
        status["tables"] = missing
        if missing:
            log.warning("tablas faltantes tras upgrade %s: fallback create_all", missing)
            status["migrations"] += " | fallback: " + _fallback_create_and_stamp()
        db = SessionLocal()
        try:
            from app.models.finanzas import CuentaContable
            from app.services.plan_operativo import cargar_plan_operativo
            antes = db.query(CuentaContable).count()
            if not antes:
                cargar_plan_operativo(db, desde_cero=False)
                db.commit()
            total = db.query(CuentaContable).count()
            status["pcge"] = f"ok ({total} cuentas, +{total - antes} nuevas)"
            # Nivelación DDL única (columnas CxP/gastos, origen 40, empleados).
            # Fuera del request path: evita apilar locks en el pooler.
            try:
                from app.services.finanzas import ensure_runtime_schema
                status["schema"] = ensure_runtime_schema(db)
            except Exception as e:
                log.warning("ensure_runtime_schema omitido: %s", e)
                status["schema"] = f"skipped: {e}"
        finally:
            db.close()
        import sys
        from app.core.config import BASE_DIR
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from seed_admin import ensure_admin
        status["admin"] = ensure_admin()
    except Exception as e:
        log.exception("init_production_db falló: %s", e)
        status["error"] = str(e)
    log.info("init_production_db: %s", status)
    return status
