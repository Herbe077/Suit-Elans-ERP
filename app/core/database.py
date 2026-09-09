"""Engine/sesión SQLAlchemy 2.0. Sync (simple, robusto para taller + SQLite/PG).

Producción (Render + Neon): pooling ajustado a capa gratuita y SSL vía URL
normalizada en app.core.config (postgres:// -> postgresql:// + sslmode).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

_db_url = settings.database_url_normalized

if settings.is_sqlite:
    connect_args = {"check_same_thread": False}
    connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(_db_url, connect_args=connect_args, pool_pre_ping=True)
else:
    # Capa gratuita Render/Neon: pool pequeño con reciclaje y pre-ping.
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
