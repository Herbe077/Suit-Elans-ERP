"""Lectura/escritura de configuración de sede."""
from sqlalchemy.orm import Session

from app.models.config import DEFAULTS, Configuracion


def get(db: Session, clave: str, default: str = "") -> str:
    row = db.get(Configuracion, clave)
    if row is None:
        return DEFAULTS.get(clave, default)
    return row.valor


def set(db: Session, clave: str, valor: str) -> None:
    row = db.get(Configuracion, clave)
    if row is None:
        db.add(Configuracion(clave=clave, valor=valor or ""))
    else:
        row.valor = valor or ""
    db.commit()


def seed_defaults(db: Session) -> None:
    for clave, valor in DEFAULTS.items():
        if not db.get(Configuracion, clave):
            db.add(Configuracion(clave=clave, valor=valor))
    db.commit()
