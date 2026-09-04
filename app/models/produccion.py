"""Modelos canónicos del módulo Producción & Confección (spec 3).

Tabla ``orden_produccion`` es la unidad de producción por prenda, aliada a
``garments`` del MES legacy: para trazabilidad se expone Garment como
OrdenProduccion cuando la integración lo requiere. Se proveen ambas capas:
- Modelos spec (tabla propia) para contratos nuevos.
- Aliases de compatibilidad hacia el flujo real (Garment/measurement etc).

Estados obligatorios: POR_CORTAR, EN_CORTE, ARMADO_HILVAN, EN_PRUEBA,
EN_CONFECCION, ACABADOS, CALIDAD_OK
"""
from datetime import date, datetime
from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.constants import KANBAN_STATES


ESTADOS_PRODUCCION = tuple(KANBAN_STATES)  # 7 fases canónicas incl. EN_CONFECCION


class OrdenProduccion(Base):
    """Unidad de producción por prenda (spec 3. Producción & Confección).

    Campos requeridos: id, orden_venta_id, codigo_qr, estado (7 fases),
    sastre_asignado_id, fecha_inicio, fecha_limite_entrega.
    """

    __tablename__ = "orden_produccion"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_venta_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    codigo_qr: Mapped[str | None] = mapped_column(String(60), nullable=True, unique=True, index=True)
    estado: Mapped[str] = mapped_column(String(20), default="POR_CORTAR", index=True)
    sastre_asignado_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fecha_inicio: Mapped[date | None] = mapped_column(Date, nullable=True, default=date.today)
    fecha_limite_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Extras útiles para trazabilidad Kanban
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FichaMedidas(Base):
    """Ficha técnica con registro de medidas anatómicas (JSON por prenda)."""

    __tablename__ = "fichas_medidas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    orden_produccion_id: Mapped[int | None] = mapped_column(ForeignKey("orden_produccion.id"), nullable=True, index=True)
    # También enlazable a Garment legacy para migración
    garment_id: Mapped[int | None] = mapped_column(ForeignKey("garments.id"), nullable=True, index=True)
    saco_medidas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pantalon_medidas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    chaleco_medidas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observaciones_anatomicas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PruebaEntalle(Base):
    """Registro de pruebas de entalle (Fit Tests 1, 2, 3). Alias spec.

    Tabla propia ``prueba_entalle_produccion`` para no colisionar con la
    tabla legacy ``pruebas_entalle`` (modelo order.PruebaEntalle) que el
    flujo real utiliza.
    """

    __tablename__ = "prueba_entalle_produccion"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_produccion_id: Mapped[int | None] = mapped_column(ForeignKey("orden_produccion.id"), nullable=True, index=True)
    # Compatibilidad Garment
    garment_id: Mapped[int | None] = mapped_column(ForeignKey("garments.id"), nullable=True, index=True)
    numero_prueba: Mapped[int] = mapped_column(default=1)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    notas_sastre: Mapped[str | None] = mapped_column(Text, nullable=True)
    ajustes_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fotos_url: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ControlCalidad(Base):
    """Checklist de inspección final pre-entrega."""

    __tablename__ = "control_calidad_produccion"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_produccion_id: Mapped[int | None] = mapped_column(ForeignKey("orden_produccion.id"), nullable=True, index=True)
    garment_id: Mapped[int | None] = mapped_column(ForeignKey("garments.id"), nullable=True, index=True)
    checklist_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    aprobado: Mapped[bool] = mapped_column(Boolean, default=False)
    revisado_por_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# --- Aliases de compatibilidad: permiten que código spec importe nombres
# canónicos y opere sobre las tablas legacy reales (Garment etc.) ---
try:
    from app.models.order import Garment as _Garment  # noqa: F401
    from app.models.order import PruebaEntalle as _LegacyPrueba  # noqa: F401
    from app.models.order import ControlCalidad as _LegacyCC  # noqa: F401
except Exception:
    pass
