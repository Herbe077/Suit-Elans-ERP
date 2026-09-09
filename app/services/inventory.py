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
    return "TELA_PRINCIPAL"

def _categoria_from_supply(s: Supply) -> str:
    # mapeo simple
    if "forro" in s.nombre.lower():
        return "FORROS"
    if "empaque" in s.nombre.lower() or "funda" in s.nombre.lower():
        return "EMPAQUES_Y_PRESENTACION"
    return "AVIOS_Y_FORNITURAS"

def ensure_producto_for_fabric(db: Session, fabric: Fabric) -> ProductoInsumo:
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == fabric.codigo).first()
    if prod:
        return prod
    prod = ProductoInsumo(
        sku=fabric.codigo, nombre=fabric.nombre, categoria="TELA_PRINCIPAL",
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
    # Kardex reserva (auditable) tipo SALIDA_CONSUMO_TALLER con observacion
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
                            cantidad=cantidad, orden_venta_id=orden_venta_id,
                            usuario_id=usuario_id, observacion="Reserva venta"))
    db.commit()
    db.refresh(prod)
    return prod

def consumir_reserva(db: Session, sku_or_id, cantidad: float, orden_venta_id: int | None = None, usuario_id: int | None = None, fabric_id: int | None = None):
    """Al pasar a EN_CORTE: descontar de fisco y liberar reservado.

    Registra Kardex SALIDA_CONSUMO_TALLER valorizado a CPP + asiento de consumo
    a WIP (regla por categoría) en la misma transacción.
    """
    try:
        from datetime import date as _date

        from app.services.compras_kardex import asiento_consumo_flush
        from app.services.contabilidad import exigir_periodo_abierto
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
        if not cantidad or cantidad <= 0:
            raise ValueError("Cantidad debe ser positiva")
        if (prod.stock_fisico or 0) < cantidad - 1e-9:
            raise ValueError(f"Stock físico insuficiente ({prod.stock_fisico})")
        exigir_periodo_abierto(db, _date.today())
        # liberar reservado
        prod.stock_reservado = round(max(0, (prod.stock_reservado or 0) - cantidad), 2)
        prod.stock_fisico = round(max(0, (prod.stock_fisico or 0) - cantidad), 2)
        # Para telas legacy, físico ya fue descontado en reserva, no descontar de nuevo si ya descontado
        # Detecta si ProductoInsumo fue creado después de reserva: fisico aún no descontado, entonces descontamos
        cpp = prod.costo_promedio or prod.costo_unitario or 0.0
        valorizado = round(cantidad * float(cpp or 0), 2)
        folio = _folio_orden(db, orden_venta_id)
        asiento = asiento_consumo_flush(
            db, prod, cantidad, cpp, f"Consumo de almacén - Orden {folio}",
            _date.today())
        k = MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
                             cantidad=cantidad, costo_unitario=float(cpp or 0),
                             costo_total=valorizado, saldo_fisico=float(prod.stock_fisico),
                             saldo_valorizado=round(float(prod.stock_fisico) * float(cpp or 0), 2),
                             orden_venta_id=orden_venta_id,
                             asiento_id=asiento.id if asiento else None,
                             doc_ref=folio if folio != "—" else None,
                             usuario_id=usuario_id, observacion="Consumo EN_CORTE")
        db.add(k)
        db.commit()
        db.refresh(prod)
        return prod
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise

def registrar_merma(db: Session, producto_id: int, cantidad: float, usuario_id: int | None, observacion: str = "",
                    tipo: str = "SALIDA_MERMA"):
    try:
        if not cantidad or cantidad <= 0:
            raise ValueError("Cantidad debe ser positiva")
        prod = db.get(ProductoInsumo, producto_id)
        if not prod:
            raise ValueError("Producto no encontrado")
        if prod.stock_fisico < cantidad - 1e-9:
            raise ValueError(f"Stock físico insuficiente ({prod.stock_fisico})")
        from datetime import date as _date

        from app.services.compras_kardex import dims_consumo
        from app.services.contabilidad import exigir_periodo_abierto
        from app.services.motor_contable import post_regla
        exigir_periodo_abierto(db, _date.today())
        prod.stock_fisico = round(prod.stock_fisico - cantidad, 2)
        cpp = prod.costo_promedio or prod.costo_unitario or 0.0
        valorizado = round(cantidad * float(cpp or 0), 2)
        asiento = None
        if valorizado > 0:
            asiento = post_regla(
                db, "MERMA", [valorizado], [valorizado],
                dims_consumo(db, prod), f"Merma {prod.sku} x{cantidad}",
                "INVENTARIO", prod.id, _date.today())
        db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento=tipo or "SALIDA_MERMA",
                                cantidad=cantidad, costo_unitario=float(cpp or 0),
                                costo_total=valorizado, saldo_fisico=float(prod.stock_fisico),
                                saldo_valorizado=round(float(prod.stock_fisico) * float(cpp or 0), 2),
                                asiento_id=asiento.id if asiento else None,
                                usuario_id=usuario_id, observacion=observacion or "Merma"))
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
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise

def registrar_ingreso_compra(db: Session, producto_id: int, cantidad: float, usuario_id: int | None, observacion: str = ""):
    try:
        if not cantidad or cantidad <= 0:
            raise ValueError("Cantidad debe ser positiva")
        prod = db.get(ProductoInsumo, producto_id)
        if not prod:
            raise ValueError("Producto no encontrado")
        prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
        db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="ENTRADA_COMPRA",
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
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


# ---------- Reservas activas / pendientes de despacho ----------

# Prefijos de observación que identifican reservas y consumos en el Kardex.
# Las reservas nacen como SALIDA_CONSUMO_TALLER "Reserva venta" / "Reserva POS"
# (ventas, POS, fichas); los consumos como "Consumo ..."/"Despacho ...".
OBS_RESERVA_PREFIX = "Reserva"
OBS_CONSUMO_PREFIXES = ("Consumo", "Despacho")


def _folio_orden(db: Session, orden_venta_id: int | None) -> str:
    if not orden_venta_id:
        return "—"
    try:
        from app.models.order import Order
        o = db.get(Order, orden_venta_id)
        return o.folio if o and o.folio else f"OV#{orden_venta_id}"
    except Exception:
        return f"OV#{orden_venta_id}"


def resumen_reservas(db: Session) -> list[dict]:
    """Reservas vigentes derivadas del Kardex, agrupadas por (producto, orden).

    reservado  = Σ SALIDA_CONSUMO_TALLER "Reserva*" (apartado por pedido/ficha)
    consumido  = Σ "Consumo*"/"Despacho*" + otras salidas vinculadas a la orden
    devuelto   = Σ ENTRADA_DEVOLUCION vinculadas (reingresan a la reserva)
    pendiente  = reservado − consumido + devuelto
    estado     = ACTIVA si pendiente > 0 else DESPACHADA
    """
    movs = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id.is_not(None)).all()
    grupos: dict[tuple, dict] = {}
    for m in movs:
        key = (m.producto_id, m.orden_venta_id)
        g = grupos.setdefault(key, {"reservado": 0.0, "consumido": 0.0,
                                    "devuelto": 0.0})
        obs = (m.observacion or "")
        tipo = (m.tipo_movimiento or "").upper()
        qty = float(m.cantidad or 0)
        if obs.startswith(OBS_RESERVA_PREFIX):
            g["reservado"] = round(g["reservado"] + qty, 2)
        elif obs.startswith(OBS_CONSUMO_PREFIXES):
            g["consumido"] = round(g["consumido"] + qty, 2)
        elif tipo.startswith("DEVOLUCION") or tipo == "ENTRADA_DEVOLUCION":
            g["devuelto"] = round(g["devuelto"] + qty, 2)
        elif tipo in ("SALIDA_CONSUMO_TALLER", "SALIDA_MERMA",
                      "SALIDA_AJUSTE", "MUESTRA",
                      "SALIDA_TALLER", "SALIDA", "AJUSTE_MERMA",
                      "AJUSTE_INVENTARIO", "MERMA", "AJUSTE"):
            # Salida manual vinculada a la orden: consume la reserva
            # (evita duplicar el descuento al despachar después).
            g["consumido"] = round(g["consumido"] + qty, 2)
    out = []
    for (pid, oid), g in grupos.items():
        pendiente = round(g["reservado"] - g["consumido"] + g["devuelto"], 2)
        prod = db.get(ProductoInsumo, pid)
        if not prod:
            continue
        out.append({
            "producto_id": pid,
            "sku": prod.sku,
            "producto": prod.nombre,
            "categoria": prod.categoria,
            "unidad": prod.unidad_medida,
            "orden_venta_id": oid,
            "folio": _folio_orden(db, oid),
            "reservado": g["reservado"],
            "consumido": g["consumido"],
            "devuelto": g["devuelto"],
            "pendiente": pendiente,
            "stock_fisico": prod.stock_fisico,
            "stock_reservado": prod.stock_reservado or 0,
            "cpp": prod.costo_promedio or prod.costo_unitario or 0.0,
            "estado": "ACTIVA" if pendiente > 0.001 else "DESPACHADA",
        })
    out.sort(key=lambda r: (r["estado"] != "ACTIVA", r["folio"], r["sku"]))
    return out


def pendiente_reserva(db: Session, producto_id: int,
                      orden_venta_id: int) -> float:
    """Pendiente de despacho de una reserva (producto, orden). 0 si no existe."""
    for r in resumen_reservas(db):
        if r["producto_id"] == producto_id and r["orden_venta_id"] == orden_venta_id:
            return max(r["pendiente"], 0.0)
    return 0.0


def despachar_reserva(db: Session, producto_id: int, orden_venta_id: int,
                      cantidad: float, usuario_id: int | None = None,
                      fecha=None) -> dict:
    """Aprueba y despacha material reservado, todo en UNA transacción.

    a) stock_fisico −= cantidad; b) stock_reservado −= cantidad (piso 0);
    c) Kardex SALIDA_CONSUMO_TALLER valorizado a CPP ("Despacho reserva {folio}")
       con vínculo a la orden y al asiento; d) la reserva pasa a DESPACHADA
       cuando su pendiente llega a 0 (estado derivado del Kardex).
    Asiento por regla de consumo a WIP (CONSUMO_MPD/CONSUMO_AUX según
    categoría), glosa "Consumo de almacén - Orden {folio}". El WIP vive
    en 2311x: la salida bespoke a costo de ventas es 6921x/2311x directa.
    UN evento = UN db.commit() final; cualquier error => db.rollback() total.
    """
    from datetime import date as _date

    from app.services.compras_kardex import asiento_consumo_flush
    from app.services.contabilidad import exigir_periodo_abierto

    fecha = fecha or _date.today()
    if not cantidad or cantidad <= 0:
        raise ValueError("Cantidad debe ser positiva")
    try:
        exigir_periodo_abierto(db, fecha)
        prod = db.query(ProductoInsumo).with_for_update().filter(
            ProductoInsumo.id == producto_id).first()
        if not prod:
            raise ValueError("Producto no encontrado")
        folio = _folio_orden(db, orden_venta_id)
        if folio == "—":
            raise ValueError("Orden de venta no encontrada")
        pendiente = pendiente_reserva(db, producto_id, orden_venta_id)
        if pendiente <= 0:
            raise ValueError(
                f"Sin reserva pendiente para {prod.sku} en {folio}")
        if cantidad - pendiente > 1e-9:
            raise ValueError(
                f"Cantidad excede la reserva pendiente ({pendiente})")
        if (prod.stock_fisico or 0) - cantidad < -1e-9:
            raise ValueError(
                f"Stock físico insuficiente ({prod.stock_fisico})")
        qty = round(float(cantidad), 2)
        prod.stock_fisico = round(float(prod.stock_fisico) - qty, 2)
        prod.stock_reservado = round(
            max(0.0, float(prod.stock_reservado or 0) - qty), 2)
        cpp = float(prod.costo_promedio or prod.costo_unitario or 0)
        valorizado = round(qty * cpp, 2)
        asiento = asiento_consumo_flush(
            db, prod, qty, cpp, f"Consumo de almacén - Orden {folio}", fecha)
        k = MovimientoKardex(
            producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
            cantidad=qty, costo_unitario=cpp, costo_total=valorizado,
            saldo_fisico=float(prod.stock_fisico),
            saldo_valorizado=round(float(prod.stock_fisico) * cpp, 2),
            orden_venta_id=orden_venta_id,
            asiento_id=asiento.id if asiento else None,
            doc_ref=folio, usuario_id=usuario_id,
            observacion=f"Despacho reserva {folio}")
        db.add(k)
        db.flush()
        db.commit()
        return {"kardex_id": k.id,
                "asiento_id": asiento.id if asiento else None,
                "asiento_numero": asiento.numero if asiento else None,
                "valorizado": valorizado,
                "pendiente_restante": round(pendiente - qty, 2)}
    except Exception:
        db.rollback()
        raise
