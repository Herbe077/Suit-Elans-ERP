"""Entorno Alembic: URL dinámica (sin rutas absolutas cableadas).

Orden de resolución del sqlalchemy.url:
  1. $DATABASE_URL (misma variable que usa la app).
  2. app.core.config.settings.database_url_normalized (lee .env + defaults).
  3. sqlalchemy.url de alembic.ini (relativo al proyecto).
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.database import Base  # noqa: E402
from app.models import *  # noqa: E402,F401,F403

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    try:
        from app.core.config import settings
        return settings.database_url_normalized
    except Exception:
        return config.get_main_option("sqlalchemy.url")


target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=_resolve_url(), target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,
                          target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
