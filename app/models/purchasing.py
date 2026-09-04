"""Compras a proveedores con recepción que alimenta el kardex."""
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), index=True)
    ruc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    folio: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    estado: Mapped[str] = mapped_column(String(20), default="borrador", index=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    igv: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    item_tipo: Mapped[str] = mapped_column(String(20))  # fabric|supply|variant
    item_id: Mapped[int] = mapped_column(Integer)
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    cantidad: Mapped[float] = mapped_column(Float, default=1.0)
    cantidad_recibida: Mapped[float] = mapped_column(Float, default=0.0)
    costo_unitario: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def importe(self) -> float:
        return round(self.cantidad * self.costo_unitario, 2)

    @property
    def pendiente(self) -> float:
        return round(self.cantidad - self.cantidad_recibida, 2)
