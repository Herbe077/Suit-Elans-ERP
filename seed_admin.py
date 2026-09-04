"""Seed mínimo de arranque: usuario administrador inicial.

Idempotente: si la tabla `users` no existe o ya hay usuarios, no hace nada.
Uso manual:  .venv/bin/python seed_admin.py
Automático:   lifespan de FastAPI lo invoca en producción con BD vacía.

Configuración por entorno (con valores por defecto solo para desarrollo):
  ADMIN_EMAIL     (default: admin@suitelans.mx)
  ADMIN_PASSWORD  (default: admin123 — cámbiala en producción)
  ADMIN_ROLE      (default: admin)
  ADMIN_NAME      (default: Administrador)
"""
import logging
import os

log = logging.getLogger("suitelans")


def ensure_admin(session=None, email=None, password=None, role=None, name=None) -> str:
    """Crea el admin si no hay ningún usuario. Retorna: created|exists|no_table."""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User

    close = False
    if session is None:
        session = SessionLocal()
        close = True
    try:
        try:
            total = session.query(User).count()
        except (OperationalError, ProgrammingError):
            session.rollback()
            return "no_table"
        if total > 0:
            return "exists"
        email = (email or os.environ.get("ADMIN_EMAIL", "admin@suitelans.mx")).lower().strip()
        password = password or os.environ.get("ADMIN_PASSWORD", "admin123")
        role = role or os.environ.get("ADMIN_ROLE", "admin")
        name = name or os.environ.get("ADMIN_NAME", "Administrador")
        if not email or not password:
            log.warning("seed admin omitido: ADMIN_EMAIL/ADMIN_PASSWORD vacíos")
            return "exists"
        session.add(User(email=email, full_name=name,
                         hashed_password=security.hash_password(password),
                         role=role, is_active=True))
        session.commit()
        if password == "admin123":
            log.warning("admin inicial creado con password por defecto: cámbiala ya")
        else:
            log.info("admin inicial creado: %s", email)
        return "created"
    finally:
        if close:
            session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(ensure_admin())
