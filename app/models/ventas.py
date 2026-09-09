"""Modelos canónicos Ventas & Atención (spec 4).

Mapeo legacy:
- OrdenVenta  -> orders (Order)  : COTIZACION→VENTA_CONFIRMADA→COMPLETADA
- PagoOrden   -> payments+payment (Payment + CashMovement)
- ComprobanteVenta -> invoices (Invoice) : BOLETA/FACTURA  (B001/F001)
- CajaTurno   -> caja_turnos (CajaTurno) : ABIERTA/CERRADA
Spec tablas propias (ordenes_venta etc) se crean para contrato, sincronizadas con legacy.

Compatibilidad: importar desde aquí alias legacy.
"""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, String, func, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Re-export legacy CajaTurno para no duplicar mapper en misma tabla.
# El spec CajaTurno es el mismo que billing.CajaTurno; lo importamos como alias.
try:
    from app.models.billing import CajaTurno as _BillingCajaTurno  # noqa
    # Exponer alias: spec CajaTurno = legacy
    CajaTurno = _BillingCajaTurno
except Exception:
    # Fallback definición si billing no existe (crea tabla propia)
    class CajaTurno(Base):  # type: ignore[no-redef]
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


class OrdenVenta(Base):
    """Orden de venta spec — sincronizada con Order (folio, cliente, total)."""

    __tablename__ = "ordenes_venta"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    folio: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    monto_adelantado: Mapped[float] = mapped_column(Float, default=0.0)
    # saldo_pendiente es computed pero se persiste para spec queries
    saldo_pendiente: Mapped[float] = mapped_column(Float, default=0.0)
    estado: Mapped[str] = mapped_column(String(20), default="COTIZACION", index=True)  # COTIZACION/VENTA_CONFIRMADA/COMPLETADA/CANCELADA
    legacy_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    fecha_pedido: Mapped[date] = mapped_column(Date, default=date.today)
    sastre_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    @property
    def saldo(self) -> float:
        return round(self.total - self.monto_adelantado, 2)


class PagoOrden(Base):
    __tablename__ = "pagos_orden"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_venta_id: Mapped[int] = mapped_column(ForeignKey("ordenes_venta.id"), index=True)
    legacy_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    caja_turno_id: Mapped[int | None] = mapped_column(ForeignKey("caja_turnos.id"), nullable=True, index=True)
    legacy_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    monto: Mapped[float] = mapped_column(Float)
    metodo_pago: Mapped[str] = mapped_column(String(30), default="EFECTIVO")  # EFECTIVO/YAPE/PLIN/TARJETA/TRANSFERENCIA
    tipo_pago: Mapped[str] = mapped_column(String(20), default="ADELANTO")  # ADELANTO/SALDO_FINAL
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ComprobanteVenta(Base):
    __tablename__ = "comprobantes_venta"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_venta_id: Mapped[int | None] = mapped_column(ForeignKey("ordenes_venta.id"), nullable=True, index=True)
    legacy_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    legacy_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(10), default="BOLETA")  # BOLETA/FACTURA
    serie: Mapped[str] = mapped_column(String(10), default="B001")
    numero: Mapped[str] = mapped_column(String(20))
    pdf_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fecha_emision: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    estado: Mapped[str] = mapped_column(String(20), default="emitida", index=True)

    @property
    def folio(self) -> str:
        return f"{self.serie}-{self.numero}"


# Alias para compatibilidad: OrdenTrabajo es Garment
try:
    from app.models.order import Garment as OrdenTrabajo  # noqa: F401
except Exception:
    pass
