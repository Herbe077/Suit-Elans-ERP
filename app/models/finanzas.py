"""Modelos Finanzas & Contabilidad (spec 6).

CuentaPorCobrar: saldos clientes (Order/OrdenVenta)
CuentaPorPagar: compromisos proveedores (PurchaseOrder/OrdenCompra)
MovimientoFinanciero: flujo caja consolidado (INGRESO/EGRESO)
"""
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CuentaPorCobrar(Base):
    __tablename__ = "cuentas_por_cobrar"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_venta_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    # alias legacy
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    monto_total: Mapped[float] = mapped_column(Float, default=0.0)
    monto_pagado: Mapped[float] = mapped_column(Float, default=0.0)
    saldo_pendiente: Mapped[float] = mapped_column(Float, default=0.0)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", index=True)  # PENDIENTE/VENCIDO/COBRADO_PARCIAL/COBRADO
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CuentaPorPagar(Base):
    __tablename__ = "cuentas_por_pagar"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    orden_compra_id: Mapped[int | None] = mapped_column(ForeignKey("ordenes_compra.id"), nullable=True)
    numero_factura: Mapped[str | None] = mapped_column(String(40), nullable=True)
    monto_total: Mapped[float] = mapped_column(Float, default=0.0)
    monto_pagado: Mapped[float] = mapped_column(Float, default=0.0)
    saldo_pendiente: Mapped[float] = mapped_column(Float, default=0.0)
    retencion: Mapped[float] = mapped_column(Float, default=0.0)
    fecha_emision: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="POR_PAGAR", index=True)  # POR_PAGAR/PAGADO/PARCIAL
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MovimientoFinanciero(Base):
    __tablename__ = "movimientos_financieros"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10), index=True)  # INGRESO/EGRESO
    categoria: Mapped[str] = mapped_column(String(60), index=True)  # Venta de Trajes/Compra de Telas/Costos Operativos/Pago Servicios
    monto: Mapped[float] = mapped_column(Float)
    cuenta_origen: Mapped[str] = mapped_column(String(20), default="Caja")  # Banco/Caja/Caja General
    comprobante_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── PCGE — Plan Contable ────────────────────────────────────────────────
class CuentaContable(Base):
    __tablename__ = "cuentas_contables"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    tipo: Mapped[str] = mapped_column(String(20), index=True)  # ACTIVO/PASIVO/PATRIMONIO/INGRESO/GASTO
    nivel: Mapped[int] = mapped_column(default=1)
    elemento: Mapped[int | None] = mapped_column(nullable=True, index=True)  # 1-9 PCGE Perú
    es_analitica: Mapped[bool] = mapped_column(default=True)
    cuenta_padre_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas_contables.id"), nullable=True, index=True)
    acepta_movimientos: Mapped[bool] = mapped_column(default=True)
    activo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PeriodoContable(Base):
    __tablename__ = "periodos_contables"

    id: Mapped[int] = mapped_column(primary_key=True)
    anio: Mapped[int] = mapped_column(index=True)
    mes: Mapped[int] = mapped_column(index=True)
    estado: Mapped[str] = mapped_column(String(20), default="ABIERTO", index=True)  # ABIERTO/CERRADO/BLOQUEADO
    fecha_apertura: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, server_default=func.now())
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CentroCosto(Base):
    __tablename__ = "centros_costo"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(100))
    tipo: Mapped[str] = mapped_column(String(30), index=True)  # TALLER/SHOWROOM/ADMINISTRACION/COMERCIAL
    activo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Partida doble ────────────────────────────────────────────────────────
# Alias del spec: la tarea pide JournalEntry; el modelo canónico es AsientoContable
# (tabla asientos_contables). Se expone el alias sin duplicar tabla.
class AsientoContable(Base):
    __tablename__ = "asientos_contables"

    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    fecha: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    periodo_id: Mapped[int | None] = mapped_column(ForeignKey("periodos_contables.id"), nullable=True, index=True)
    glosa: Mapped[str] = mapped_column(String(255))
    origen_tipo: Mapped[str] = mapped_column(String(20), index=True)  # VENTA/COMPRA/PAGO/COBRO/INVENTARIO/PRODUCCION/MOD/CIF/AJUSTE/CIERRE/MANUAL
    origen_id: Mapped[int | None] = mapped_column(index=True)
    estado: Mapped[str] = mapped_column(String(20), default="REGISTRADO", index=True)  # REGISTRADO/ANULADO
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Detalle contable (líneas Debe/Haber). Solo lectura en vistas del Diario.
    lineas: Mapped[list["LineaAsientoContable"]] = relationship(
        "LineaAsientoContable", back_populates="asiento", lazy="select",
        order_by="LineaAsientoContable.id")


class LineaAsientoContable(Base):
    __tablename__ = "lineas_asiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    asiento_id: Mapped[int] = mapped_column(ForeignKey("asientos_contables.id"), index=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas_contables.id"), index=True)
    debe: Mapped[float] = mapped_column(default=0.0)
    haber: Mapped[float] = mapped_column(default=0.0)
    centro_costo_id: Mapped[int | None] = mapped_column(ForeignKey("centros_costo.id"), nullable=True)
    centro_gestion_id: Mapped[int | None] = mapped_column(ForeignKey("centros_costo.id"), nullable=True)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asiento: Mapped["AsientoContable"] = relationship("AsientoContable", back_populates="lineas", lazy="joined")
    cuenta: Mapped["CuentaContable"] = relationship("CuentaContable", lazy="joined")


# Alias del spec: JournalEntry = AsientoContable (cabecera del Libro Diario).
JournalEntry = AsientoContable


# ── Gastos con clasificación de costos ──────────────────────────────────
class GastoRegistrado(Base):
    __tablename__ = "gastos_registrados"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    ruc_proveedor: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    tipo_comprobante: Mapped[str | None] = mapped_column(String(20), nullable=True)  # FACTURA/RECIBO_HONORARIOS/BOLETA/PLANILLA/OTRO
    numero_comprobante: Mapped[str | None] = mapped_column(String(40), nullable=True)
    categoria: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)  # ALQUILER/HONORARIOS/PLANILLA/ACTIVO_FIJO/OTRO
    glosa: Mapped[str | None] = mapped_column(String(255), nullable=True)  # descripción libre
    monto_base: Mapped[float] = mapped_column(Float, default=0.0)
    monto_igv: Mapped[float] = mapped_column(Float, default=0.0)
    monto_total: Mapped[float] = mapped_column(Float, default=0.0)
    clasificacion: Mapped[str] = mapped_column(String(30), index=True)  # MPD/CIF/GASTO_ADMINISTRATIVO/GASTO_VENTAS/GASTO_FINANCIERO/ACTIVO_FIJO
    variabilidad: Mapped[str] = mapped_column(String(20), default="FIJO")  # FIJO/VARIABLE
    centro_costo_id: Mapped[int | None] = mapped_column(ForeignKey("centros_costo.id"), nullable=True)
    cuenta_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas_contables.id"), nullable=True)
    fecha_emision: Mapped[date] = mapped_column(Date, default=date.today)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE")  # PENDIENTE/PAGADO
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
