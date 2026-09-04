"""Caja y comprobantes internos (no electrónicos)."""
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CashMovement(Base):
    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), index=True)  # ingreso|egreso
    concepto: Mapped[str] = mapped_column(String(255))
    monto: Mapped[float] = mapped_column(Float)
    metodo: Mapped[str] = mapped_column(String(30), default="efectivo")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    turno_id: Mapped[int | None] = mapped_column(ForeignKey("caja_turnos.id"), nullable=True,
                                                 index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CajaTurno(Base):
    """Turno de caja diaria por usuario: apertura, movimientos y cierre con arqueo."""

    __tablename__ = "caja_turnos"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    saldo_apertura: Mapped[float] = mapped_column(Float, default=0.0)
    saldo_cierre_teorico: Mapped[float | None] = mapped_column(Float, nullable=True)
    saldo_cierre_real: Mapped[float | None] = mapped_column(Float, nullable=True)
    fecha_apertura: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    estado: Mapped[str] = mapped_column(String(10), default="ABIERTA", index=True)

    @property
    def diferencia(self) -> float | None:
        if self.saldo_cierre_teorico is None or self.saldo_cierre_real is None:
            return None
        return round(self.saldo_cierre_real - self.saldo_cierre_teorico, 2)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    serie: Mapped[str] = mapped_column(String(10), default="B001")
    numero: Mapped[str] = mapped_column(String(20))  # correlativo con ceros, ej. 000123
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    igv_pct: Mapped[float] = mapped_column(Float, default=18.0)
    igv: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    estado: Mapped[str] = mapped_column(String(20), default="emitida", index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def folio(self) -> str:
        return f"{self.serie}-{self.numero}"
