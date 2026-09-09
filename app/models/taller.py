"""Módulo aislado 'Cierre de Jornada y Pagos por Taller'.

Aislamiento lógico: estas 3 tablas solo se relacionan entre sí y con
users(id). No hay FK ni dependencias hacia inventario, ventas o facturación.
"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TallerTarea(Base):
    """Catálogo maestro de actividades y tarifas por tarea."""

    __tablename__ = "taller_tareas"

    id: Mapped[int] = mapped_column(primary_key=True)
    descripcion: Mapped[str] = mapped_column(String(255), index=True)
    tarifa: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class TallerCierreJornada(Base):
    """Encabezado del registro de jornada de un operario."""

    __tablename__ = "taller_cierres_jornada"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    producto_referencia: Mapped[str] = mapped_column(String(160), default="")
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    total_pago: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


class TallerCierreDetalle(Base):
    """Detalle de actividades marcadas (tarifa histórica al momento del cierre)."""

    __tablename__ = "taller_cierre_detalle"

    id: Mapped[int] = mapped_column(primary_key=True)
    cierre_id: Mapped[int] = mapped_column(ForeignKey("taller_cierres_jornada.id"), index=True)
    tarea_id: Mapped[int] = mapped_column(ForeignKey("taller_tareas.id"), index=True)
    tarifa_aplicada: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
