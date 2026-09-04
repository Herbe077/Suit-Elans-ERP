"""Configuración de sede (clave-valor) editable desde /admin/configuracion."""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Configuracion(Base):
    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(String(255), default="")


DEFAULTS = {
    "sede_nombre": "Suit Elans — Sede Principal",
    "direccion": "",
    "telefono": "",
    "whatsapp": "",
    "igv_default": "18",
    "anticipo_min_pct": "50",
}
