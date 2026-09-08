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

CRITICAL_TABLES = ("users", "cuentas_contables", "asientos_contables")


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
    """Ejecuta el pipeline completo. Idempotente y sin excepciones."""
    from app.core.database import SessionLocal
    from app.services.finanzas import seed_pcge_basico

    status: dict = {"migrations": "skipped", "tables": [], "pcge": "skipped",
                    "admin": "skipped", "schema": "skipped"}
    try:
        status["migrations"] = _upgrade_with_retries()
        missing = _missing_tables()
        status["tables"] = missing
        if missing:
            log.warning("tablas faltantes tras upgrade %s: fallback create_all", missing)
            status["migrations"] += " | fallback: " + _fallback_create_and_stamp()
        db = SessionLocal()
        try:
            from app.models.finanzas import CuentaContable
            antes = db.query(CuentaContable).count()
            seed_pcge_basico(db)
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
