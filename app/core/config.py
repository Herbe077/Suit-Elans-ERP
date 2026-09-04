"""Configuración central. SQLite en dev, PostgreSQL en prod (vía DATABASE_URL)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto (…/Suit-Elans-ERP). Todas las rutas por defecto cuelgan
# de aquí para que `seed.py` y `uvicorn` usen la misma DB/plantillas
# sin importar desde qué directorio se lancen.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SQLITE_URL = f"sqlite:///{BASE_DIR / 'suitelans.db'}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Suit Elans ERP & MES"
    ENV: str = "dev"
    # Alias estándar de plataforma (Render): si existe, manda sobre ENV.
    ENVIRONMENT: str | None = None
    SECRET_KEY: str = "CHANGE_ME_dev_only"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    COOKIE_SECURE: bool = False
    DATABASE_URL: str = DEFAULT_SQLITE_URL
    CORS_ORIGINS: str = "http://localhost:8000"
    TZ: str = "America/Lima"

    @property
    def environment(self) -> str:
        return (self.ENVIRONMENT or self.ENV or "dev").lower()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def database_url_normalized(self) -> str:
        """Normaliza la URL para Postgres/Neon: formato heredado + SSL.

        `sslmode=require` solo se agrega si falta Y el host es Neon o el
        entorno es producción (no rompe el Postgres local de docker).
        """
        url = (self.DATABASE_URL or "").strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if (url.startswith("postgresql") and "sslmode=" not in url
                and ("neon.tech" in url or self.is_production)):
            url += ("&" if "?" in url else "?") + "sslmode=require"
        return url or DEFAULT_SQLITE_URL

    @property
    def is_sqlite(self) -> bool:
        return self.database_url_normalized.startswith("sqlite")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
