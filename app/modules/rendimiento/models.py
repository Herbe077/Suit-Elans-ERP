"""Modelos Rendimiento & Destajo Operario — independiente.

CatalogoOperacion: catálogo 44 operaciones estándar con tarifa Decimal
RegistroJornada: encabezado diario por operario
DetalleJornada: líneas por operación con cantidad y tarifa histórica Decimal
"""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

class CatalogoOperacion(Base):
    __tablename__ = "rendimiento_catalogo"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre_operacion: Mapped[str] = mapped_column(String(255), index=True)
    tarifa_base: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)

class RegistroJornada(Base):
    __tablename__ = "rendimiento_registros"

    id: Mapped[int] = mapped_column(primary_key=True)
    operario_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, default=date.today, server_default=func.current_date(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="REGISTRADO", index=True)  # REGISTRADO/APROBADO/LIQUIDADO
    sede_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # filtro sede actual

class DetalleJornada(Base):
    __tablename__ = "rendimiento_detalles"
    __table_args__ = (
        UniqueConstraint("orden_produccion_id", "operacion_id", name="uq_detalle_prenda_operacion"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registro_jornada_id: Mapped[int] = mapped_column(ForeignKey("rendimiento_registros.id"), index=True)
    orden_produccion_id: Mapped[int | None] = mapped_column(ForeignKey("garments.id"), nullable=True, index=True)
    # Referencia canónica a orden_produccion.id (la columna anterior guarda garments.id por legado)
    orden_produccion_ref_id: Mapped[int | None] = mapped_column(ForeignKey("orden_produccion.id"), nullable=True, index=True)
    # alias legacy order id también soportado
    orden_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    operacion_id: Mapped[int] = mapped_column(ForeignKey("rendimiento_catalogo.id"), index=True)
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    tarifa_aplicada: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    @property
    def subtotal_calc(self) -> Decimal:
        return (self.tarifa_aplicada or Decimal("0")) * Decimal(self.cantidad)
