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
    "tarifa_minuto_default": "0.35",
    "capacidad_minutos_mes": "11520",
    # Parámetros laborales y tributarios (Admin > Parámetros).
    "asignacion_familiar": "102.50",
    "essalud_pct": "9.00",
    "retencion_4ta_pct": "8.00",
    "fondo_onp_pct": "13.00",
    "fondo_afp_integra_pct": "12.80",
    "fondo_afp_habitat_pct": "12.87",
    "fondo_afp_prima_pct": "12.82",
    "fondo_afp_profuturo_pct": "12.89",
}

# Fondos pensionarios parametrizables: (sistema, etiqueta, clave_pct).
FONDOS_PENSION = (
    ("ONP", "ONP", "fondo_onp_pct"),
    ("AFP_INTEGRA", "AFP Integra", "fondo_afp_integra_pct"),
    ("AFP_HABITAT", "AFP Habitat", "fondo_afp_habitat_pct"),
    ("AFP_PRIMA", "AFP Prima", "fondo_afp_prima_pct"),
    ("AFP_PROFUTURO", "AFP Profuturo", "fondo_afp_profuturo_pct"),
)
