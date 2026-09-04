"""Modelos canónicos Inventario & Logística (spec 5).

ProductoInsumo unifica TELAS, FORROS, AVÍOS, EMPAQUES.
MovimientoKardex = kardex auditado.
OrdenCompra + DetalleOrdenCompra = compras a proveedores con estados BORRADOR→COMPLETADA.

Compatibilidad: Fabric/Supply/PurchaseOrder siguen existiendo (legacy inventory.py / purchasing.py)
pero se sincronizan con ProductoInsumo vía sku/código.
"""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

CATEGORIAS = ("TELA", "FORRO", "AVIO", "EMPAQUE", "TELAS", "FORROS", "AVÍOS", "AVIOS", "EMPAQUES")
UNIDADES = ("METROS", "UNIDADES", "ROLLO", "ROTO", "METRO", "PZA", "KG")
# Kardex valorizado: canónicos del spec + aliases legacy que siguen aceptándose en lectura.
TIPOS_MOV = ("ENTRADA", "SALIDA", "AJUSTE_MERMA", "TRANSFERENCIA",
             "INGRESO_COMPRA", "SALIDA_TALLER", "DEVOLUCION", "INGRESO", "SALIDA", "AJUSTE", "MERMA")
# Flujo canónico OC (inglés, spec): DRAFT -> APPROVED -> PARTIALLY_RECEIVED -> RECEIVED -> BILLED
# CANCELLED es terminal lateral. Se mantienen aliases legacy ES/minúsculas solo para lectura
# y migración progresiva; toda escritura nueva debe usar el canónico.
ESTADOS_OC_CANONICOS = ("DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "BILLED", "CANCELLED")
ESTADOS_OC = ESTADOS_OC_CANONICOS + ("BORRADOR", "ENVIADA", "RECIBIDA_PARCIAL", "RECIBIDA", "COMPLETADA",
                                     "CANCELADA", "borrador", "enviada", "recibida_parcial", "recibida", "cancelada")
# Normalización legacy -> canónico (usada por el servicio de compras).
ESTADO_OC_ALIAS = {
    "BORRADOR": "DRAFT", "borrador": "DRAFT",
    "ENVIADA": "APPROVED", "enviada": "APPROVED",
    "RECIBIDA_PARCIAL": "PARTIALLY_RECEIVED", "recibida_parcial": "PARTIALLY_RECEIVED",
    "RECIBIDA": "RECEIVED", "recibida": "RECEIVED",
    "COMPLETADA": "RECEIVED",
    "CANCELADA": "CANCELLED", "cancelada": "CANCELLED",
    "DRAFT": "DRAFT", "APPROVED": "APPROVED", "PARTIALLY_RECEIVED": "PARTIALLY_RECEIVED",
    "RECEIVED": "RECEIVED", "BILLED": "BILLED", "CANCELLED": "CANCELLED",
}
# Transiciones permitidas de la máquina de estados (origen -> destinos válidos).
TRANSICIONES_OC = {
    "DRAFT": ("APPROVED", "CANCELLED"),
    "APPROVED": ("PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"),
    "PARTIALLY_RECEIVED": ("PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"),
    "RECEIVED": ("BILLED", "CANCELLED"),
    "BILLED": (),
    "CANCELLED": (),
}


def normalizar_estado_oc(estado: str | None) -> str:
    """Devuelve el estado canónico. Desconocido -> DRAFT (no rompe lecturas legacy)."""
    if not estado:
        return "DRAFT"
    return ESTADO_OC_ALIAS.get(estado, ESTADO_OC_ALIAS.get(estado.upper(), "DRAFT"))


class ProductoInsumo(Base):
    """Catálogo de insumos de sastrería (spec)."""

    __tablename__ = "productos_insumo"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    categoria: Mapped[str] = mapped_column(String(20), default="TELA", index=True)  # TELA/FORRO/AVIO/EMPAQUE
    composicion: Mapped[str | None] = mapped_column(String(160), nullable=True)
    color: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ancho_cm: Mapped[float | None] = mapped_column(Float, nullable=True, default=150.0)
    unidad_medida: Mapped[str] = mapped_column(String(20), default="METROS")
    costo_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    # Costo Promedio Ponderado (CPP): se recalcula en cada ENTRADA por compra.
    # costo_unitario se mantiene como alias sincronizado de costo_promedio para
    # no romper lecturas legacy (Fabric.precio_metro, reportes, POS).
    costo_promedio: Mapped[float] = mapped_column(Float, default=0.0)
    ultimo_costo: Mapped[float] = mapped_column(Float, default=0.0)
    precio_metro: Mapped[float] = mapped_column(Float, default=0.0)
    stock_fisico: Mapped[float] = mapped_column(Float, default=0.0)
    stock_reservado: Mapped[float] = mapped_column(Float, default=0.0)
    stock_minimo: Mapped[float] = mapped_column(Float, default=10.0)
    proveedor_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True, index=True)
    ancho_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def stock_disponible(self) -> float:
        return round(self.stock_fisico - self.stock_reservado, 2)

    @property
    def stock_bajo(self) -> bool:
        return self.stock_disponible <= self.stock_minimo or self.stock_fisico <= self.stock_minimo


class MovimientoKardex(Base):
    """Kardex valorizado (trazabilidad absoluta).

    Cada fila representa UN movimiento físico + su valorización CPP y su vínculo
    al Libro Diario (asiento_id). Convención de signos: cantidad siempre positiva
    y el sentido lo da tipo_movimiento (ENTRADA suma, SALIDA/AJUSTE_MERMA restan).
    Se aceptan tipos legacy (INGRESO_COMPRA/SALIDA_TALLER/...) en lectura.
    """

    __tablename__ = "movimientos_kardex"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos_insumo.id"), index=True)
    tipo_movimiento: Mapped[str] = mapped_column(String(30), index=True)  # ENTRADA/SALIDA/AJUSTE_MERMA/TRANSFERENCIA (+legacy)
    cantidad: Mapped[float] = mapped_column(Float)  # siempre >= 0; el signo lo da el tipo
    costo_unitario: Mapped[float] = mapped_column(Float, default=0.0)  # CPP vigente o costo landed aplicado
    costo_total: Mapped[float] = mapped_column(Float, default=0.0)  # cantidad * costo_unitario
    saldo_fisico: Mapped[float] = mapped_column(Float, default=0.0)  # stock_fisico tras el movimiento
    saldo_valorizado: Mapped[float] = mapped_column(Float, default=0.0)  # saldo_fisico * CPP tras el movimiento
    orden_compra_id: Mapped[int | None] = mapped_column(ForeignKey("ordenes_compra.id"), nullable=True, index=True)
    detalle_oc_id: Mapped[int | None] = mapped_column(ForeignKey("detalles_orden_compra.id"), nullable=True, index=True)
    orden_venta_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    asiento_id: Mapped[int | None] = mapped_column(ForeignKey("asientos_contables.id"), nullable=True, index=True)
    doc_ref: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)  # folio OC / nº factura / parte taller
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now())
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrdenCompra(Base):
    """Orden de compra a proveedor.

    Flujo canónico: DRAFT -> APPROVED -> PARTIALLY_RECEIVED -> RECEIVED -> BILLED.
    - DRAFT no afecta stock ni contabilidad (regla de negocio).
    - RECEIVED: mercancía completa en almacén (kardex + asiento 241/611 ya emitidos).
    - BILLED: vinculada a comprobante de proveedor + provisión 601+4011/421 y CxP.
    """

    __tablename__ = "ordenes_compra"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    fecha_emision: Mapped[date] = mapped_column(Date, default=date.today, server_default=func.now())
    fecha_entrega_esperada: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    igv: Mapped[float] = mapped_column(Float, default=0.0)
    monto_total: Mapped[float] = mapped_column(Float, default=0.0)
    folio: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    # Facturación del proveedor (transición RECEIVED -> BILLED)
    numero_factura: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    fecha_factura: Mapped[date | None] = mapped_column(Date, nullable=True)
    moneda: Mapped[str] = mapped_column(String(10), default="PEN")
    igv_rate: Mapped[float] = mapped_column(Float, default=0.18)
    # Costos landed (flete / seguro / otros de importación) a prorratear por valor
    # sobre el costo unitario antes de la valorización CPP final.
    landed_flete: Mapped[float] = mapped_column(Float, default=0.0)
    landed_seguro: Mapped[float] = mapped_column(Float, default=0.0)
    landed_otros: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# NOTA: no se aliasa PurchaseOrder aquí para no colisionar con el modelo legacy
# app/models/purchasing.py (tabla purchase_orders, espejo por folio).
# El canónico del spec es OrdenCompra (tabla ordenes_compra).


class DetalleOrdenCompra(Base):
    __tablename__ = "detalles_orden_compra"

    id: Mapped[int] = mapped_column(primary_key=True)
    orden_compra_id: Mapped[int] = mapped_column(ForeignKey("ordenes_compra.id"), index=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos_insumo.id"), index=True)
    cantidad_solicitada: Mapped[float] = mapped_column(Float, default=1.0)
    cantidad_recibida: Mapped[float] = mapped_column(Float, default=0.0)
    precio_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    descuento_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    # Costo landed prorrateado a esta línea (solo informativo; el costo final
    # aplicado al kardex es costo_unitario_final).
    landed_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    costo_unitario_final: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def pendiente(self) -> float:
        return round(self.cantidad_solicitada - self.cantidad_recibida, 2)

    @property
    def subtotal(self) -> float:
        return round(self.cantidad_solicitada * self.precio_unitario, 2)


# Aliases del spec (al final para no referenciar clases aún no definidas).
# KardexEntry / InventoryMovement = MovimientoKardex (tabla movimientos_kardex).
# PurchaseOrderItem = DetalleOrdenCompra (tabla detalles_orden_compra).
KardexEntry = MovimientoKardex
InventoryMovement = MovimientoKardex
PurchaseOrderItem = DetalleOrdenCompra
