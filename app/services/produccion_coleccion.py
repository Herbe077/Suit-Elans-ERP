"""Finalización de Órdenes de Producción de colección / stock comercial.

Cuando una OP de prendas para catálogo comercial pasa a COMPLETADA o
FINALIZADA, en UNA sola transacción SQL:
  a) descuenta del almacén los insumos directos (telas, avíos) según la
     Ficha Técnica / Receta (BOM) asignada a la prenda;
  b) costo total de fabricación = insumos directos (valorizados a CPP) +
     mano de obra directa (destajo registrado al operario);
  c) costo unitario = costo total / unidades producidas;
  d) actualiza el costo unitario de la variante/SKU del Producto Terminado;
  e) incrementa el stock del Producto Terminado (sección comercial, Cta 23);
  f) registra los movimientos de Kardex asociando la OP (doc_ref = código OP);
  g) liquida la MOD del centro 9211 a PT vía 7111 (LIQUIDACION_9211),
     dejando el WIP 2311 de la OP en cero al costo real.

Regla del motor: UN evento = UN db.commit() final; cualquier error =>
db.rollback() total. Por eso este servicio solo usa variantes *flush* de los
asientos por regla (motor_contable.post_regla) y jamás llama a
helpers que commiteen por dentro (apply_movement, registrar_ingreso_pt, etc.).
"""
from datetime import date as _date

from sqlalchemy.orm import Session

from app.models.catalog import ProductVariant
from app.models.inventario import MovimientoKardex, ProductoInsumo
from app.models.inventory import StockMovement
from app.models.produccion import OrdenProduccion, RecetaBOM, RecetaBOMLinea

ESTADOS_FINALES_OP = ("COMPLETADA", "FINALIZADA")


def codigo_op(op: OrdenProduccion) -> str:
    """Referencia trazable de la OP para Kardex/doc_ref."""
    return op.codigo_qr or f"OP-{op.id}"


def crear_receta(db: Session, product_id: int | None, nombre: str | None,
                 lineas: list[dict]) -> RecetaBOM:
    """Crea una receta BOM con sus líneas. Commitea (gestión de catálogo)."""
    receta = RecetaBOM(product_id=product_id, nombre=nombre, activa=True)
    db.add(receta)
    db.flush()
    for lin in lineas:
        pid = int(lin.get("producto_insumo_id") or 0)
        qpu = float(lin.get("cantidad_por_unidad") or 0)
        if not db.get(ProductoInsumo, pid):
            raise ValueError(f"Insumo #{pid} no encontrado")
        if qpu <= 0:
            raise ValueError("cantidad_por_unidad debe ser positiva")
        db.add(RecetaBOMLinea(receta_id=receta.id, producto_insumo_id=pid,
                              cantidad_por_unidad=round(qpu, 2)))
    db.commit()
    db.refresh(receta)
    return receta


def lineas_desde_receta(db: Session, receta_id: int) -> list[dict]:
    """Líneas de una receta como dicts {producto_insumo_id, cantidad_por_unidad}."""
    receta = db.get(RecetaBOM, receta_id)
    if not receta:
        raise ValueError("Receta no encontrada")
    return [{"producto_insumo_id": l.producto_insumo_id,
             "cantidad_por_unidad": l.cantidad_por_unidad}
            for l in db.query(RecetaBOMLinea).filter(
                RecetaBOMLinea.receta_id == receta_id).all()]


def mod_destajo_op(db: Session, op: OrdenProduccion) -> float:
    """Mano de obra directa devengada en la OP (destajo del operario).

    1) Suma de cierres de jornada (TallerCierreJornada.total_pago) cuya
       producto_referencia cita el código de la OP.
    2) Fallback: minutos WorkLog terminados de las prendas de la orden
       vinculada × tarifa_minuto_taller.
    """
    code = codigo_op(op)
    try:
        from app.models.taller import TallerCierreJornada
        cierres = db.query(TallerCierreJornada).filter(
            TallerCierreJornada.producto_referencia == code).all()
        if not cierres:
            cierres = db.query(TallerCierreJornada).filter(
                TallerCierreJornada.producto_referencia.contains(code)).all()
        total = round(sum(float(c.total_pago or 0) for c in cierres), 2)
        if total > 0:
            return total
    except Exception:
        pass
    try:
        if op.orden_venta_id:
            from sqlalchemy import func as _func
            from app.models.order import Garment, WorkLog
            gids = [g.id for g in db.query(Garment).filter(
                Garment.order_id == op.orden_venta_id).all()]
            minutos = 0.0
            if gids:
                minutos = float(db.query(
                    _func.coalesce(_func.sum(WorkLog.minutos_reales), 0)).filter(
                    WorkLog.garment_id.in_(gids),
                    WorkLog.estado == "terminado").scalar() or 0)
            try:
                from app.services.finanzas import tarifa_minuto_taller
                tarifa = float(tarifa_minuto_taller(db) or 0.35)
            except Exception:
                tarifa = 0.35
            if not tarifa or tarifa <= 0:
                tarifa = 0.35
            return round(minutos * tarifa, 2)
    except Exception:
        pass
    return 0.0


def finalizar_op_coleccion(db: Session, orden_produccion_id: int,
                           variant_id: int, cantidad: float,
                           insumos: list[dict] | None = None,
                           receta_id: int | None = None,
                           mano_obra_directa: float | None = None,
                           estado_final: str = "COMPLETADA",
                           usuario_id: int | None = None,
                           fecha=None,
                           trabajador_id: int | None = None) -> dict:
    """Finaliza una OP de colección. Toda la operación = UN commit final.

    insumos: [{producto_insumo_id, cantidad_por_unidad}] — BOM explícita.
    Si no se pasa, se usa receta_id o la receta activa del producto de la
    variante. mano_obra_directa: destajo explícito S/; si es None se calcula
    con mod_destajo_op().
    """
    from app.services.compras_kardex import (
        _espejo_legacy_stock,
        asiento_consumo_flush,
    )
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.motor_contable import dim_centro, post_regla
    from decimal import Decimal

    fecha = fecha or _date.today()
    estado_final = (estado_final or "COMPLETADA").upper()
    if estado_final not in ESTADOS_FINALES_OP:
        raise ValueError("estado_final debe ser COMPLETADA o FINALIZADA")
    if not cantidad or float(cantidad) <= 0:
        raise ValueError("Cantidad producida debe ser positiva")

    try:
        exigir_periodo_abierto(db, fecha)
        op = db.query(OrdenProduccion).with_for_update().filter(
            OrdenProduccion.id == orden_produccion_id).first()
        if not op:
            raise ValueError("Orden de producción no encontrada")
        if (op.estado or "").upper() in ESTADOS_FINALES_OP:
            raise ValueError(f"La OP {codigo_op(op)} ya está finalizada ({op.estado})")
        var = db.query(ProductVariant).with_for_update().filter(
            ProductVariant.id == variant_id).first()
        if not var:
            raise ValueError("Variante/SKU de producto terminado no encontrada")

        # --- BOM: explícita > receta_id > receta activa del producto ---
        bom = list(insumos or [])
        if not bom and receta_id:
            bom = lineas_desde_receta(db, receta_id)
        if not bom and var.product_id:
            rec = db.query(RecetaBOM).filter(
                RecetaBOM.product_id == var.product_id,
                RecetaBOM.activa.is_(True)).order_by(RecetaBOM.id.desc()).first()
            if rec:
                bom = [{"producto_insumo_id": l.producto_insumo_id,
                        "cantidad_por_unidad": l.cantidad_por_unidad}
                       for l in db.query(RecetaBOMLinea).filter(
                           RecetaBOMLinea.receta_id == rec.id).all()]
        if not bom:
            raise ValueError("Sin BOM: pasa insumos, receta_id o asigna una receta activa al producto")

        # --- Valida y bloquea insumos (fail-fast antes de mutar) ---
        qty_prod = round(float(cantidad), 2)
        lineas: list[tuple[ProductoInsumo, float]] = []
        for lin in bom:
            pid = int(lin.get("producto_insumo_id") or 0)
            qpu = float(lin.get("cantidad_por_unidad") or 0)
            if qpu <= 0:
                raise ValueError("cantidad_por_unidad debe ser positiva")
            prod = db.query(ProductoInsumo).with_for_update().filter(
                ProductoInsumo.id == pid).first()
            if not prod:
                raise ValueError(f"Insumo #{pid} no encontrado")
            need = round(qpu * qty_prod, 2)
            if float(prod.stock_fisico or 0) - need < -1e-9:
                raise ValueError(
                    f"Stock insuficiente de {prod.sku} "
                    f"(físico {prod.stock_fisico}, requiere {need})")
            lineas.append((prod, need))

        code = codigo_op(op)

        # --- a) Descuenta insumos + Kardex SALIDA_CONSUMO_TALLER de la OP ---
        # Consumo a WIP por regla (2311x/2411x o 2311x/2521x según insumo).
        cc_taller = dim_centro(db, "921")
        costo_insumos = 0.0
        kardex_ids: list[int] = []
        asientos_salida: list[int] = []
        for prod, need in lineas:
            cpp = float(prod.costo_promedio or prod.costo_unitario or 0)
            valorizado = round(need * cpp, 2)
            prod.stock_fisico = round(float(prod.stock_fisico) - need, 2)
            asiento = asiento_consumo_flush(
                db, prod, need, cpp,
                f"Consumo OP {code} - {prod.sku}", fecha, usuario_id,
                orden_produccion_id=op.id, centro_costo_id=cc_taller)
            k = MovimientoKardex(
                producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
                cantidad=need, costo_unitario=cpp, costo_total=valorizado,
                saldo_fisico=float(prod.stock_fisico),
                saldo_valorizado=round(float(prod.stock_fisico) * cpp, 2),
                orden_venta_id=op.orden_venta_id,
                asiento_id=asiento.id if asiento else None,
                doc_ref=code, usuario_id=usuario_id,
                observacion=f"Consumo OP {code} - {prod.sku}")
            db.add(k)
            db.flush()
            kardex_ids.append(k.id)
            if asiento:
                asientos_salida.append(asiento.id)
            costo_insumos = round(costo_insumos + valorizado, 2)
            _espejo_legacy_stock(db, prod, Decimal(str(-need)), "salida",
                                 f"Consumo OP {code}", usuario_id)

        # --- b) MOD + c) costo unitario ---
        mod = round(float(mano_obra_directa)
                    if mano_obra_directa is not None
                    else mod_destajo_op(db, op), 2)
        if mod < 0:
            raise ValueError("mano_obra_directa no puede ser negativa")
        costo_total = round(costo_insumos + mod, 2)
        costo_unitario = round(costo_total / qty_prod, 2)

        # --- d+e) Costo del SKU + stock PT (sección comercial, Cta 23) ---
        var.costo_unitario = costo_unitario
        var.stock = round(float(var.stock or 0) + qty_prod, 2)
        db.add(StockMovement(item_tipo="variant", item_id=var.id,
                             cantidad=qty_prod, tipo="ingreso_produccion",
                             motivo=f"OP {code} x{qty_prod:g} costo S/ {costo_unitario:.2f}",
                             usuario_id=usuario_id))

        # --- b2) Imputa la MOD al WIP (regla IMPUTACION_MOD) ---
        asiento_mod_id = None
        if mod > 0:
            trabajador = (trabajador_id or op.sastre_asignado_id)
            if not trabajador:
                raise ValueError(
                    "MOD > 0 sin operario: asigna sastre a la OP o pasa "
                    "trabajador_id")
            a_mod = post_regla(
                db, "IMPUTACION_MOD", [mod], [mod],
                {"orden_produccion_id": op.id, "trabajador_id": trabajador,
                 "centro_costo_id": cc_taller},
                f"MOD OP {code} S/ {mod:.2f}", "PRODUCCION", op.id, fecha)
            asiento_mod_id = a_mod.id

        # --- f) Cierre de OP (regla CIERRE_OP: 2111x / 2311x) ---
        # Alta del PT comercial por el costo total de fabricación. El WIP
        # 2311 queda en cero por OP: +insumos (consumo) +MOD (imputación)
        # −costo_total (cierre) = 0.
        a_pt = post_regla(
            db, "CIERRE_OP", [costo_total], [costo_total],
            {"orden_produccion_id": op.id, "producto_id": var.id},
            f"Cierre OP {code} - {var.sku} x{qty_prod:g}",
            "PRODUCCION", op.id, fecha)
        asiento_pt_id = a_pt.id if a_pt else None

        # --- g) Liquidación analítica 9211 → 2111 vía 7111 ---
        # La MOD aplicada a la OP se acumuló en el centro 9211 (destino de
        # planilla/destajo). Al cerrar se transfiere a PT mediante 7111
        # (regla LIQUIDACION_9211: 7111 / 9211x por la MOD), cerrando el
        # centro de costo por lo aplicado. El 2111 ya capitalizó el costo
        # total en (f); el 7111 es el puente analítico. Sin MOD no hay nada
        # que liquidar.
        asiento_liq_id = None
        if mod > 0:
            a_liq = post_regla(
                db, "LIQUIDACION_9211", [mod], [mod],
                {"centro_costo_id": cc_taller},
                f"Liquidación 9211 OP {code} S/ {mod:.2f}",
                "PRODUCCION", op.id, fecha)
            asiento_liq_id = a_liq.id if a_liq else None
        # Entrada física en Almacén Cta 23: fila de Kardex del PT ingresado.
        k_pt = MovimientoKardex(
            producto_id=None, tipo_movimiento="ENTRADA_PRODUCTO_TERMINADO",
            cantidad=qty_prod, costo_unitario=costo_unitario,
            costo_total=costo_total,
            saldo_fisico=float(var.stock or 0),
            saldo_valorizado=round(float(var.stock or 0) * costo_unitario, 2),
            orden_venta_id=op.orden_venta_id,
            asiento_id=asiento_pt_id, doc_ref=var.sku,
            usuario_id=usuario_id,
            observacion=f"Ingreso PT OP {code} - {var.sku} x{qty_prod:g}")
        db.add(k_pt)
        db.flush()

        op.estado = estado_final
        db.flush()
        db.commit()
        return {"op_id": op.id, "codigo": code, "estado": estado_final,
                "variant_id": var.id, "sku": var.sku, "cantidad": qty_prod,
                "costo_insumos": costo_insumos, "mano_obra_directa": mod,
                "costo_total": costo_total, "costo_unitario": costo_unitario,
                "stock_pt": float(var.stock),
                "kardex_ids": kardex_ids,
                "asientos_salida_ids": asientos_salida,
                "asiento_mod_id": asiento_mod_id,
                "asiento_pt_id": asiento_pt_id,
                "asiento_liquidacion_id": asiento_liq_id,
                "kardex_pt_id": k_pt.id}
    except Exception:
        db.rollback()
        raise
