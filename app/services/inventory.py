"""Lógica de inventario: movimientos auditados, reservas por pedido, alertas.

Soporta legacy (Fabric/Supply) y spec ProductoInsumo con stock_fisico/reservado/disponible.
"""
from sqlalchemy.orm import Session

from app.models.catalog import ProductVariant
from app.models.inventory import Fabric, StockMovement, Supply
from app.models.inventario import MovimientoKardex, ProductoInsumo


def apply_movement(db: Session, item_tipo: str, item_id: int, cantidad: float,
                   tipo: str, motivo: str, usuario_id: int | None) -> StockMovement:
    if item_tipo == "fabric":
        item = db.get(Fabric, item_id)
        if not item:
            raise ValueError("Tela no encontrada")
        nuevo = item.stock_metros + cantidad
        if nuevo < -1e-6:
            raise ValueError(f"Stock insuficiente ({item.stock_metros} m disponibles)")
        item.stock_metros = round(nuevo, 2)
    elif item_tipo == "supply":
        item = db.get(Supply, item_id)
        if not item:
            raise ValueError("Avío no encontrado")
        nuevo = item.stock + cantidad
        if nuevo < -1e-6:
            raise ValueError(f"Stock insuficiente ({item.stock} disponibles)")
        item.stock = round(nuevo, 2)
    elif item_tipo == "variant":
        item = db.get(ProductVariant, item_id)
        if not item:
            raise ValueError("Variante no encontrada")
        nuevo = item.stock + cantidad
        if nuevo < -1e-6:
            raise ValueError(f"Stock insuficiente ({item.stock} disponibles)")
        item.stock = round(nuevo, 2)
    else:
        raise ValueError("item_tipo debe ser fabric|supply|variant")

    mov = StockMovement(item_tipo=item_tipo, item_id=item_id, cantidad=cantidad,
                        tipo=tipo, motivo=motivo, usuario_id=usuario_id)
    db.add(mov)
    db.commit()
    db.refresh(mov)
    return mov


def low_stock(db: Session) -> dict:
    fabrics = db.query(Fabric).filter(Fabric.stock_metros <= Fabric.stock_minimo).all()
    supplies = db.query(Supply).filter(Supply.stock <= Supply.stock_minimo).all()
    variants = db.query(ProductVariant).filter(ProductVariant.stock <= ProductVariant.stock_minimo).all()
    # Spec productos: stock_disponible es property Python (no columna SQL) -> filtra en memoria
    try:
        productos_bajo = [p for p in db.query(ProductoInsumo).all()
                          if p.stock_disponible <= (p.stock_minimo or 0)]
    except Exception:
        productos_bajo = []
    return {"fabrics": fabrics, "supplies": supplies, "variants": variants, "productos": productos_bajo}


# ---------- Spec helpers ----------

def _categoria_from_fabric(f: Fabric) -> str:
    return "TELA"

def _categoria_from_supply(s: Supply) -> str:
    # mapeo simple
    if "forro" in s.nombre.lower():
        return "FORRO"
    if "empaque" in s.nombre.lower() or "funda" in s.nombre.lower():
        return "EMPAQUE"
    return "AVIO"

def ensure_producto_for_fabric(db: Session, fabric: Fabric) -> ProductoInsumo:
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == fabric.codigo).first()
    if prod:
        return prod
    prod = ProductoInsumo(
        sku=fabric.codigo, nombre=fabric.nombre, categoria="TELA",
        composicion=fabric.composicion, color=fabric.color,
        ancho_cm=(fabric.ancho_m * 100 if fabric.ancho_m else 150),
        unidad_medida="METROS", costo_unitario=fabric.precio_metro,
        precio_metro=fabric.precio_metro,
        stock_fisico=fabric.stock_metros, stock_reservado=0,
        stock_minimo=fabric.stock_minimo, proveedor_id=fabric.proveedor_id,
        ancho_m=fabric.ancho_m)
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod

def ensure_producto_for_supply(db: Session, sup: Supply) -> ProductoInsumo:
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sup.codigo).first()
    if prod:
        return prod
    prod = ProductoInsumo(
        sku=sup.codigo, nombre=sup.nombre, categoria=_categoria_from_supply(sup),
        unidad_medida="UNIDADES", costo_unitario=sup.costo_unitario,
        stock_fisico=sup.stock, stock_reservado=sup.stock_reservado or 0,
        stock_minimo=sup.stock_minimo)
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod

def reservar_insumo(db: Session, sku_or_id, cantidad: float, orden_venta_id: int | None = None, usuario_id: int | None = None, fabric_id: int | None = None):
    """Reserva stock: incrementa stock_reservado, reduce disponible. No toca físico.
    Si fabric_id provisto, sincroniza Fabric y ProductoInsumo (fisico legacy ya descontado aparte)."""
    prod = None
    if isinstance(sku_or_id, int):
        prod = db.get(ProductoInsumo, sku_or_id)
    if not prod and fabric_id:
        fab = db.get(Fabric, fabric_id)
        if fab:
            prod = ensure_producto_for_fabric(db, fab)
    if not prod and isinstance(sku_or_id, str):
        prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku_or_id).first()
    if not prod:
        raise ValueError("Producto no encontrado")
    if prod.stock_disponible < cantidad - 1e-9:
        raise ValueError(f"Stock disponible insuficiente ({prod.stock_disponible} disponible)")
    prod.stock_reservado = round((prod.stock_reservado or 0) + cantidad, 2)
    # Kardex reserva (auditable) tipo SALIDA_TALLER con observacion
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                            cantidad=cantidad, orden_venta_id=orden_venta_id,
                            usuario_id=usuario_id, observacion="Reserva venta"))
    db.commit()
    db.refresh(prod)
    return prod

def consumir_reserva(db: Session, sku_or_id, cantidad: float, orden_venta_id: int | None = None, usuario_id: int | None = None, fabric_id: int | None = None):
    """Al pasar a EN_CORTE: descontar de fisco y liberar reservado."""
    prod = None
    if isinstance(sku_or_id, int):
        prod = db.get(ProductoInsumo, sku_or_id)
    if not prod and fabric_id:
        fab = db.get(Fabric, fabric_id)
        if fab:
            prod = ensure_producto_for_fabric(db, fab)
    if not prod and isinstance(sku_or_id, str):
        prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku_or_id).first()
    if not prod:
        raise ValueError("Producto no encontrado")
    # liberar reservado
    prod.stock_reservado = round(max(0, (prod.stock_reservado or 0) - cantidad), 2)
    prod.stock_fisico = round(max(0, (prod.stock_fisico or 0) - cantidad), 2)
    # Para telas legacy, físico ya fue descontado en reserva, no descontar de nuevo si ya descontado
    # Detecta si ProductoInsumo fue creado después de reserva: fisico aún no descontado, entonces descontamos
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                            cantidad=cantidad, orden_venta_id=orden_venta_id,
                            usuario_id=usuario_id, observacion="Consumo EN_CORTE"))
    db.commit()
    db.refresh(prod)
    return prod

def registrar_merma(db: Session, producto_id: int, cantidad: float, usuario_id: int | None, observacion: str = "",
                    tipo: str = "AJUSTE_MERMA"):
    if not cantidad or cantidad <= 0:
        raise ValueError("Cantidad debe ser positiva")
    prod = db.get(ProductoInsumo, producto_id)
    if not prod:
        raise ValueError("Producto no encontrado")
    if prod.stock_fisico < cantidad - 1e-9:
        raise ValueError(f"Stock físico insuficiente ({prod.stock_fisico})")
    prod.stock_fisico = round(prod.stock_fisico - cantidad, 2)
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento=tipo or "AJUSTE_MERMA",
                            cantidad=cantidad, usuario_id=usuario_id, observacion=observacion or "Merma"))
    # También legacy Fabric/Supply mirroring
    legacy = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
    if legacy:
        legacy.stock_metros = round(max(0, legacy.stock_metros - cantidad), 2)
    else:
        leg2 = db.query(Supply).filter(Supply.codigo == prod.sku).first()
        if leg2:
            leg2.stock = round(max(0, leg2.stock - cantidad), 2)
    db.commit()
    db.refresh(prod)
    return prod

def registrar_ingreso_compra(db: Session, producto_id: int, cantidad: float, usuario_id: int | None, observacion: str = ""):
    if not cantidad or cantidad <= 0:
        raise ValueError("Cantidad debe ser positiva")
    prod = db.get(ProductoInsumo, producto_id)
    if not prod:
        raise ValueError("Producto no encontrado")
    prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="INGRESO_COMPRA",
                            cantidad=cantidad, usuario_id=usuario_id, observacion=observacion))
    # mirror legacy
    leg = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
    if leg:
        leg.stock_metros = round(leg.stock_metros + cantidad, 2)
    else:
        leg2 = db.query(Supply).filter(Supply.codigo == prod.sku).first()
        if leg2:
            leg2.stock = round(leg2.stock + cantidad, 2)
    db.commit()
    db.refresh(prod)
    return prod
