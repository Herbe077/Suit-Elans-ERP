"""Servicio canónico COMPRAS + KARDEX VALORIZADO + LIBRO DIARIO (PCGE).

Cubre el spec pedido:

  OC: DRAFT -> APPROVED -> PARTIALLY_RECEIVED -> RECEIVED -> BILLED (+ CANCELLED)
  Kardex: ENTRADA / SALIDA / AJUSTE_MERMA / TRANSFERENCIA, valorizado a CPP.
  Diario (partida doble, una sola transacción ACID por operación):
    - Recepción compra : DEBE 2411 / HABER 6111  (destino existencias)
    - Provisión factura: DEBE 602 + DEBE 40111 / HABER 4212 (+ CxP)
    - Consumo taller   : DEBE 6111 / HABER 2411
    - Merma/desmedro   : DEBE 6591 / HABER 2411

Reglas:
  - DRAFT no toca stock ni contabilidad.
  - Toda operación compuesta (kardex + asiento [+ CxP]) hace UN solo db.commit()
    al final; ante cualquier error hace db.rollback() (atomicidad).
  - CPP: CPP_nuevo = (stock_prev * CPP_prev + qty * costo_unitario_final)
                      / (stock_prev + qty)

Espejo legacy: en la misma transacción se sincronizan Fabric/Supply
(cantidades), StockMovement (auditoría), PurchaseOrder/PurchaseLine
(estado + cantidad_recibida) por folio, sin commits intermedios.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finanzas import CuentaPorPagar
from app.models.inventario import (
    DetalleOrdenCompra,
    MovimientoKardex,
    OrdenCompra,
    ProductoInsumo,
    TRANSICIONES_OC,
    normalizar_estado_oc,
)
from app.services.contabilidad import exigir_periodo_abierto
from app.services.finanzas import (
    crear_asiento_flush,
    get_cuenta_by_codigo,
    seed_pcge_basico,
)

IGV_DEFAULT = Decimal("0.18")
# Divisor IGV: todo importe ingresado es TOTAL FINAL con IGV incluido.
DIV_IGV = Decimal("1.18")


def desglose_igv(total: float | Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Desglosa un total final: (base, igv, total) con base = total/1.18."""
    t = _d(total).quantize(Decimal("0.01"))
    base = (t / DIV_IGV).quantize(Decimal("0.01"))
    return base, t - base, t

# Cuentas PCGE usadas por este servicio (analíticas).
CTA_2411 = "2411"    # Materia prima - Telas y avíos (activo)
CTA_6111 = "6111"    # Variación de existencias (gasto)
CTA_6011 = "6011"    # Compras - Materia prima (gasto, provisión OC)
CTA_40111 = "40111"  # IGV crédito fiscal (activo/pasivo según plan)
CTA_4212 = "4212"    # Proveedores - Emitidas (pasivo)
CTA_6591 = "6591"    # Mermas y desmedros (gasto)


# ── Utilidades puras ────────────────────────────────────────────────
def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def prorratear_landed(detalles: list[DetalleOrdenCompra], total_landed: float | Decimal) -> dict[int, Decimal]:
    """Prorratea el landed total por valor de línea (qty * precio neto).

    Retorna {detalle_id: landed_unitario}. Si la base total es 0, reparte 0.
    """
    total_landed = _d(total_landed)
    if total_landed <= 0 or not detalles:
        return {d.id: Decimal("0") for d in detalles}
    bases = {d.id: _d(d.cantidad_solicitada) * (_d(d.precio_unitario) - _d(d.descuento_unitario)) for d in detalles}
    base_total = sum(bases.values())
    if base_total <= 0:
        return {d.id: Decimal("0") for d in detalles}
    out: dict[int, Decimal] = {}
    for d in detalles:
        parte = total_landed * bases[d.id] / base_total
        qty = _d(d.cantidad_solicitada)
        out[d.id] = (parte / qty) if qty > 0 else Decimal("0")
    return out


def cpp_nuevo(stock_prev: float | Decimal, cpp_prev: float | Decimal,
              qty: float | Decimal, costo_unit: float | Decimal) -> Decimal:
    """Costo Promedio Ponderado tras una entrada."""
    stock_prev, cpp_prev, qty, costo_unit = _d(stock_prev), _d(cpp_prev), _d(qty), _d(costo_unit)
    if qty <= 0:
        raise ValueError("qty debe ser positiva")
    if stock_prev < 0:
        raise ValueError("stock_prev no puede ser negativo")
    denom = stock_prev + qty
    if denom <= 0:
        return costo_unit
    return (stock_prev * cpp_prev + qty * costo_unit) / denom


def _check_transicion(actual_canon: str, destino_canon: str):
    if destino_canon not in TRANSICIONES_OC.get(actual_canon, ()):
        raise ValueError(f"Transición OC no permitida: {actual_canon} -> {destino_canon}")


def _cuenta(db: Session, codigo: str):
    c = get_cuenta_by_codigo(db, codigo)
    if not c:
        raise ValueError(f"Falta cuenta PCGE {codigo} (ejecuta seed PCGE)")
    return c


def _lock_oc(db: Session, oc_id: int) -> OrdenCompra:
    # with_for_update es no-op en SQLite y lock real en PostgreSQL.
    oc = db.query(OrdenCompra).with_for_update().filter(OrdenCompra.id == oc_id).first()
    if not oc:
        raise ValueError("Orden de compra no encontrada")
    return oc


# ── Máquina de estados ──────────────────────────────────────────────
def aprobar_oc(db: Session, oc_id: int) -> OrdenCompra:
    """DRAFT -> APPROVED. No toca stock ni contabilidad."""
    try:
        oc = _lock_oc(db, oc_id)
        actual = normalizar_estado_oc(oc.estado)
        _check_transicion(actual, "APPROVED")
        oc.estado = "APPROVED"
        db.flush()
        _espejo_legacy_estado(db, oc)
        db.commit()
        db.refresh(oc)
        return oc
    except Exception:
        db.rollback()
        raise


def cancelar_oc(db: Session, oc_id: int) -> OrdenCompra:
    """Cualquier estado no terminal -> CANCELLED. No revierte kardex ya recibido
    (la mercadería en almacén se ajusta por merma/devolución, con su asiento)."""
    try:
        oc = _lock_oc(db, oc_id)
        actual = normalizar_estado_oc(oc.estado)
        if actual in ("BILLED", "CANCELLED"):
            raise ValueError(f"No se puede cancelar una OC en estado {actual}")
        _check_transicion(actual, "CANCELLED")
        oc.estado = "CANCELLED"
        db.flush()
        _espejo_legacy_estado(db, oc)
        db.commit()
        db.refresh(oc)
        return oc
    except Exception:
        db.rollback()
        raise


# ── Recepción de compra (kardex + CPP + asiento 241/611) ────────────
def recepcionar_oc(db: Session, oc_id: int, recepciones: dict[int, float],
                   usuario_id: int | None = None, fecha: date | None = None) -> dict:
    """Confirma recepción (parcial o total) de una OC aprobada.

    recepciones: {detalle_id: cantidad_a_recibir}.
    Efectos por línea, en UNA transacción:
      1. Valida estado (APPROVED/PARTIALLY_RECEIVED) y pendientes.
      2. Prorratea landed de la OC -> costo_unitario_final.
      3. Recalcula CPP del ProductoInsumo y suma stock_fisico.
      4. Inserta MovimientoKardex ENTRADA valorizado (saldo + asiento_id).
      5. Emite asiento INVENTARIO DEBE 2411 / HABER 6111 (agrupado por OC).
      6. Actualiza Detalle.cantidad_recibida y estado OC.
      7. Espejo legacy (Fabric/Supply + StockMovement + PO/Line) sin commits extra.
    """
    fecha = fecha or date.today()
    exigir_periodo_abierto(db, fecha)
    seed_pcge_basico(db)
    try:
        oc = _lock_oc(db, oc_id)
        actual = normalizar_estado_oc(oc.estado)
        if actual == "DRAFT":
            raise ValueError("OC en DRAFT: apruébala (APPROVED) antes de recibir")
        if actual in ("BILLED", "CANCELLED", "RECEIVED"):
            raise ValueError(f"OC en estado {actual}: recepción no permitida")
        if actual not in ("APPROVED", "PARTIALLY_RECEIVED"):
            raise ValueError(f"OC en estado {actual}: recepción no permitida")

        detalles = db.query(DetalleOrdenCompra).with_for_update().filter(
            DetalleOrdenCompra.orden_compra_id == oc.id).all()
        por_id = {d.id: d for d in detalles}
        if not detalles:
            raise ValueError("OC sin líneas")
        # Limpia y valida recepciones
        items: list[tuple[DetalleOrdenCompra, Decimal]] = []
        for det_id, qty in (recepciones or {}).items():
            d = por_id.get(det_id)
            if not d:
                raise ValueError(f"Detalle {det_id} no pertenece a la OC")
            qty_d = _d(qty)
            if qty_d <= 0:
                raise ValueError(f"Cantidad debe ser positiva (detalle {det_id})")
            pendiente = _d(d.cantidad_solicitada) - _d(d.cantidad_recibida)
            if qty_d - pendiente > Decimal("0.000001"):
                raise ValueError(f"Cantidad {qty_d} excede pendiente {pendiente} (detalle {det_id})")
            items.append((d, qty_d))
        if not items:
            raise ValueError("Sin líneas a recibir")

        landed_total = _d(oc.landed_flete) + _d(oc.landed_seguro) + _d(oc.landed_otros)
        prorrateo = prorratear_landed(detalles, landed_total)

        c2411 = _cuenta(db, CTA_2411)
        c6111 = _cuenta(db, CTA_6111)

        total_valorizado = Decimal("0")
        kardex_rows: list[MovimientoKardex] = []
        for d, qty in items:
            prod = db.query(ProductoInsumo).with_for_update().filter(
                ProductoInsumo.id == d.producto_id).first()
            if not prod:
                raise ValueError(f"Producto {d.producto_id} no encontrado")
            # Importes ingresados = TOTAL FINAL con IGV incluido.
            # El almacén (24/61) solo toma la base neta: / 1.18.
            precio_neto = _d(d.precio_unitario) - _d(d.descuento_unitario)
            if precio_neto < 0:
                raise ValueError(f"Precio neto negativo (detalle {d.id})")
            landed_u = prorrateo.get(d.id, Decimal("0"))
            precio_base_u = precio_neto / DIV_IGV
            landed_net_u = landed_u / DIV_IGV
            costo_final = precio_base_u + landed_net_u
            d.landed_unitario = float(landed_u)
            d.costo_unitario_final = float(costo_final)

            stock_prev = _d(prod.stock_fisico)
            cpp_prev = _d(prod.costo_promedio) if _d(prod.costo_promedio) > 0 else _d(prod.costo_unitario)
            nuevo_cpp = cpp_nuevo(stock_prev, cpp_prev, qty, costo_final)
            prod.costo_promedio = float(nuevo_cpp)
            prod.costo_unitario = float(nuevo_cpp)  # alias legacy sincronizado
            prod.ultimo_costo = float(costo_final)
            prod.stock_fisico = float(stock_prev + qty)

            costo_total = qty * costo_final
            total_valorizado += costo_total
            d.cantidad_recibida = float(_d(d.cantidad_recibida) + qty)

            k = MovimientoKardex(
                producto_id=prod.id, tipo_movimiento="ENTRADA",
                cantidad=float(qty), costo_unitario=float(costo_final),
                costo_total=float(costo_total),
                saldo_fisico=float(stock_prev + qty),
                saldo_valorizado=float((stock_prev + qty) * nuevo_cpp),
                orden_compra_id=oc.id, detalle_oc_id=d.id,
                doc_ref=oc.folio or f"OC-{oc.id}",
                usuario_id=usuario_id, fecha=fecha,
                observacion=f"Recepción OC {oc.folio or oc.id} + landed neto {float(landed_net_u):.2f}/u",
            )
            db.add(k)
            kardex_rows.append(k)

        # Asiento único por recepción: DEBE 2411 / HABER 6111
        asiento = crear_asiento_flush(
            db, fecha, f"Recepción OC {oc.folio or oc.id}", "INVENTARIO", oc.id,
            [{"cuenta_id": c2411.id, "debe": total_valorizado, "haber": Decimal("0")},
             {"cuenta_id": c6111.id, "debe": Decimal("0"), "haber": total_valorizado}],
        )
        for k in kardex_rows:
            k.asiento_id = asiento.id
        db.flush()

        # Estado OC según pendientes
        restantes = [_d(d.cantidad_solicitada) - _d(d.cantidad_recibida) for d in detalles]
        if all(r <= Decimal("0.000001") for r in restantes):
            oc.estado = "RECEIVED"
        else:
            oc.estado = "PARTIALLY_RECEIVED"
        db.flush()
        _espejo_legacy_estado(db, oc)
        _espejo_legacy_recepcion(db, oc, items, usuario_id)

        db.commit()
        return {"oc_id": oc.id, "estado": oc.estado, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "valorizado": float(total_valorizado),
                "kardex_ids": [k.id for k in kardex_rows]}
    except Exception:
        db.rollback()
        raise


# ── Facturación / provisión (601 + 4011 / 421 + CxP) ────────────────
def facturar_oc(db: Session, oc_id: int, numero_factura: str,
                fecha_factura: date | None = None) -> dict:
    """RECEIVED (o PARTIALLY_RECEIVED) -> BILLED.

    Genera en la misma transacción:
      - provisión DEBE 6011 (base neta) + DEBE 40111 (IGV) / HABER 4212 (total)
      - CuentaPorPagar FACTURA vinculada a la OC (idempotente por numero_factura).
    La base es lo efectivamente RECIBIDO (cantidad_recibida * precio neto +
    landed prorrateado), no lo solicitado.
    """
    if not (numero_factura or "").strip():
        raise ValueError("numero_factura es obligatorio")
    numero_factura = numero_factura.strip()
    exigir_periodo_abierto(db, fecha_factura)
    seed_pcge_basico(db)
    try:
        oc = _lock_oc(db, oc_id)
        actual = normalizar_estado_oc(oc.estado)
        if actual == "DRAFT":
            raise ValueError("OC en DRAFT: no se puede facturar")
        if actual == "BILLED":
            raise ValueError("OC ya facturada (BILLED)")
        if actual == "CANCELLED":
            raise ValueError("OC cancelada: no se puede facturar")
        if actual not in ("RECEIVED", "PARTIALLY_RECEIVED", "APPROVED"):
            raise ValueError(f"OC en estado {actual}: facturación no permitida")

        # Idempotencia: mismo comprobante no se provisiona dos veces.
        dup = db.query(CuentaPorPagar).filter(
            CuentaPorPagar.proveedor_id == oc.proveedor_id,
            CuentaPorPagar.numero_factura == numero_factura).first()
        if dup:
            raise ValueError(f"Factura {numero_factura} ya provisionada (CxP {dup.id})")

        detalles = db.query(DetalleOrdenCompra).filter(
            DetalleOrdenCompra.orden_compra_id == oc.id).all()
        if not detalles:
            raise ValueError("OC sin líneas")
        landed_total = _d(oc.landed_flete) + _d(oc.landed_seguro) + _d(oc.landed_otros)
        # Importes ingresados = TOTALES FINALES con IGV incluido.
        total_lineas = Decimal("0")
        for d in detalles:
            precio_neto = _d(d.precio_unitario) - _d(d.descuento_unitario)
            total_lineas += _d(d.cantidad_recibida) * precio_neto
        if total_lineas <= 0:
            raise ValueError("Sin cantidades recibidas: nada que facturar")
        base_lineas, igv_lineas, _ = desglose_igv(total_lineas)
        base_landed, igv_landed, _ = desglose_igv(landed_total)
        base = base_lineas + base_landed
        igv = igv_lineas + igv_landed
        total = base + igv  # == total_lineas + landed_total, sin recargos

        c6011 = _cuenta(db, CTA_6011)
        c40111 = _cuenta(db, CTA_40111)
        c4212 = _cuenta(db, CTA_4212)
        fecha = fecha_factura or date.today()
        lineas = [{"cuenta_id": c6011.id, "debe": base, "haber": Decimal("0")}]
        if igv > 0:
            lineas.append({"cuenta_id": c40111.id, "debe": igv, "haber": Decimal("0")})
        lineas.append({"cuenta_id": c4212.id, "debe": Decimal("0"), "haber": total})
        asiento = crear_asiento_flush(
            db, fecha, f"Provisión {numero_factura} OC {oc.folio or oc.id}",
            "COMPRA", oc.id, lineas)

        cxp = db.query(CuentaPorPagar).filter(
            CuentaPorPagar.orden_compra_id == oc.id).first()
        if cxp is None:
            cxp = CuentaPorPagar(
                proveedor_id=oc.proveedor_id, orden_compra_id=oc.id,
                tipo_comprobante="FACTURA", origen_tipo="COMPRAS",
                numero_factura=numero_factura, monto_total=float(total),
                monto_pagado=0.0, saldo_pendiente=float(total),
                fecha_emision=fecha, estado="POR_PAGAR",
            )
            db.add(cxp)
            db.flush()
        else:
            # Actualiza el espejo creado en RECEIVED (folio) con la factura:
            # conserva lo ya amortizado y re-calcula el saldo.
            cxp.proveedor_id = oc.proveedor_id
            cxp.tipo_comprobante = "FACTURA"
            cxp.origen_tipo = "COMPRAS"
            cxp.numero_factura = numero_factura
            cxp.monto_total = float(total)
            cxp.saldo_pendiente = float(max(
                _d(total) - _d(cxp.monto_pagado) - _d(cxp.retencion),
                Decimal("0")))
            if _d(cxp.saldo_pendiente) <= Decimal("0.01"):
                cxp.estado = "PAGADO"
            elif _d(cxp.monto_pagado) > 0:
                cxp.estado = "PARCIAL"
            else:
                cxp.estado = "POR_PAGAR"

        oc.numero_factura = numero_factura
        oc.fecha_factura = fecha
        oc.subtotal = float(base)
        oc.igv = float(igv)
        oc.monto_total = float(total)
        oc.estado = "BILLED"
        db.flush()
        _espejo_legacy_estado(db, oc)

        db.commit()
        return {"oc_id": oc.id, "estado": oc.estado, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "cxp_id": cxp.id,
                "base": float(base), "igv": float(igv), "total": float(total)}
    except Exception:
        db.rollback()
        raise


# ── Reparación: OCs sin espejo en CxP ─────────────────────────────
def reparar_cxp_compras(db: Session) -> dict:
    """Recorre OCs RECEIVED/PARTIALLY_RECEIVED/BILLED y asegura su CxP.

    - BILLED con factura: crea o actualiza el espejo (FACTURA/COMPRAS) con
      el total de la OC, conservando lo ya amortizado.
    - RECEIVED sin factura: crea el espejo con el folio como comprobante.
    No genera asientos (la provisión vive en facturar_oc). Idempotente.
    Retorna {"creadas": n, "actualizadas": n, "omitidas": [(folio, motivo)]}.
    """
    from app.services.finanzas import (ensure_cxp_origen_tipo_column,
                                       ensure_cxp_tipo_comprobante_column)
    ensure_cxp_tipo_comprobante_column(db)
    ensure_cxp_origen_tipo_column(db)
    rep = {"creadas": 0, "actualizadas": 0, "omitidas": []}
    for oc in db.query(OrdenCompra).all():
        try:
            est = normalizar_estado_oc(oc.estado)
        except Exception:
            continue
        if est not in ("RECEIVED", "PARTIALLY_RECEIVED", "BILLED"):
            continue
        try:
            cxp = db.query(CuentaPorPagar).filter(
                CuentaPorPagar.orden_compra_id == oc.id).first()
            if est == "BILLED" and (oc.numero_factura or "").strip():
                numero, tipo = oc.numero_factura.strip(), "FACTURA"
                total = _d(oc.monto_total)
            else:
                numero, tipo = oc.folio or f"OC-{oc.id}", "OTROS"
                total = _d(oc.monto_total)
            if total <= 0:
                rep["omitidas"].append((oc.folio or oc.id, "sin monto"))
                continue
            if cxp is None:
                db.add(CuentaPorPagar(
                    proveedor_id=oc.proveedor_id, orden_compra_id=oc.id,
                    origen_tipo="COMPRAS", tipo_comprobante=tipo,
                    numero_factura=numero, monto_total=float(total),
                    monto_pagado=0.0, saldo_pendiente=float(total),
                    fecha_emision=oc.fecha_emision, estado="POR_PAGAR"))
                rep["creadas"] += 1
            else:
                cxp.origen_tipo = "COMPRAS"
                if est == "BILLED" and (oc.numero_factura or "").strip():
                    cxp.tipo_comprobante = "FACTURA"
                    cxp.numero_factura = oc.numero_factura.strip()
                    cxp.monto_total = float(total)
                    cxp.saldo_pendiente = float(max(
                        total - _d(cxp.monto_pagado) - _d(cxp.retencion),
                        Decimal("0")))
                    if _d(cxp.saldo_pendiente) <= Decimal("0.01"):
                        cxp.estado = "PAGADO"
                    elif _d(cxp.monto_pagado) > 0:
                        cxp.estado = "PARCIAL"
                    rep["actualizadas"] += 1
        except Exception as e:
            rep["omitidas"].append((getattr(oc, "folio", oc.id), str(e)[:80]))
            continue
    db.commit()
    return rep


# ── Consumo taller (611 / 241) y merma (659 / 241) ──────────────────
def consumir_taller(db: Session, producto_id: int, cantidad: float,
                    usuario_id: int | None = None, doc_ref: str | None = None,
                    fecha: date | None = None) -> dict:
    """Salida a producción valorizada a CPP: DEBE 6111 / HABER 2411."""
    if not cantidad or _d(cantidad) <= 0:
        raise ValueError("Cantidad debe ser positiva")
    fecha = fecha or date.today()
    exigir_periodo_abierto(db, fecha)
    seed_pcge_basico(db)
    try:
        prod = db.query(ProductoInsumo).with_for_update().filter(
            ProductoInsumo.id == producto_id).first()
        if not prod:
            raise ValueError("Producto no encontrado")
        qty = _d(cantidad)
        if _d(prod.stock_fisico) - qty < Decimal("-0.000001"):
            raise ValueError(f"Stock insuficiente ({prod.stock_fisico})")
        cpp = _d(prod.costo_promedio) if _d(prod.costo_promedio) > 0 else _d(prod.costo_unitario)
        valorizado = qty * cpp
        prod.stock_fisico = float(_d(prod.stock_fisico) - qty)

        c6111 = _cuenta(db, CTA_6111)
        c2411 = _cuenta(db, CTA_2411)
        asiento = crear_asiento_flush(
            db, fecha, f"Consumo taller {prod.sku} x{qty} {doc_ref or ''}".strip(),
            "INVENTARIO", prod.id,
            [{"cuenta_id": c6111.id, "debe": valorizado, "haber": Decimal("0")},
             {"cuenta_id": c2411.id, "debe": Decimal("0"), "haber": valorizado}])
        k = MovimientoKardex(
            producto_id=prod.id, tipo_movimiento="SALIDA", cantidad=float(qty),
            costo_unitario=float(cpp), costo_total=float(valorizado),
            saldo_fisico=float(prod.stock_fisico),
            saldo_valorizado=float(_d(prod.stock_fisico) * cpp),
            asiento_id=asiento.id, doc_ref=doc_ref,
            usuario_id=usuario_id, fecha=fecha, observacion="Consumo taller",
        )
        db.add(k)
        db.flush()
        _espejo_legacy_stock(db, prod, -qty, "salida", f"Consumo {doc_ref or ''}", usuario_id)
        db.commit()
        return {"kardex_id": k.id, "asiento_id": asiento.id, "valorizado": float(valorizado)}
    except Exception:
        db.rollback()
        raise


def registrar_merma(db: Session, producto_id: int, cantidad: float,
                    usuario_id: int | None = None, observacion: str = "",
                    fecha: date | None = None) -> dict:
    """Ajuste por merma/desmedro valorizado a CPP: DEBE 6591 / HABER 2411."""
    if not cantidad or _d(cantidad) <= 0:
        raise ValueError("Cantidad debe ser positiva")
    fecha = fecha or date.today()
    exigir_periodo_abierto(db, fecha)
    seed_pcge_basico(db)
    try:
        prod = db.query(ProductoInsumo).with_for_update().filter(
            ProductoInsumo.id == producto_id).first()
        if not prod:
            raise ValueError("Producto no encontrado")
        qty = _d(cantidad)
        if _d(prod.stock_fisico) - qty < Decimal("-0.000001"):
            raise ValueError(f"Stock físico insuficiente ({prod.stock_fisico})")
        cpp = _d(prod.costo_promedio) if _d(prod.costo_promedio) > 0 else _d(prod.costo_unitario)
        valorizado = qty * cpp
        prod.stock_fisico = float(_d(prod.stock_fisico) - qty)

        c6591 = _cuenta(db, CTA_6591)
        c2411 = _cuenta(db, CTA_2411)
        asiento = crear_asiento_flush(
            db, fecha, f"Merma {prod.sku} x{qty}", "INVENTARIO", prod.id,
            [{"cuenta_id": c6591.id, "debe": valorizado, "haber": Decimal("0")},
             {"cuenta_id": c2411.id, "debe": Decimal("0"), "haber": valorizado}])
        k = MovimientoKardex(
            producto_id=prod.id, tipo_movimiento="AJUSTE_MERMA", cantidad=float(qty),
            costo_unitario=float(cpp), costo_total=float(valorizado),
            saldo_fisico=float(prod.stock_fisico),
            saldo_valorizado=float(_d(prod.stock_fisico) * cpp),
            asiento_id=asiento.id, usuario_id=usuario_id, fecha=fecha,
            observacion=observacion or "Merma",
        )
        db.add(k)
        db.flush()
        _espejo_legacy_stock(db, prod, -qty, "salida", f"Merma {observacion or ''}", usuario_id)
        db.commit()
        return {"kardex_id": k.id, "asiento_id": asiento.id, "valorizado": float(valorizado)}
    except Exception:
        db.rollback()
        raise


# ── Espejos legacy (misma transacción, sin commit) ──────────────────
def _espejo_legacy_estado(db: Session, oc: OrdenCompra):
    """Sincroniza estado canónico -> PurchaseOrder legacy por folio (flush, no commit)."""
    try:
        from app.models.purchasing import PurchaseOrder as LegacyPO
        if not oc.folio:
            return
        po = db.query(LegacyPO).filter(LegacyPO.folio == oc.folio).first()
        if not po:
            return
        mapping = {"DRAFT": "borrador", "APPROVED": "enviada",
                   "PARTIALLY_RECEIVED": "recibida_parcial", "RECEIVED": "recibida",
                   "BILLED": "recibida", "CANCELLED": "cancelada"}
        po.estado = mapping.get(normalizar_estado_oc(oc.estado), "borrador")
        po.subtotal, po.igv, po.total = oc.subtotal, oc.igv, oc.monto_total
        db.flush()
    except Exception:
        pass


def _espejo_legacy_recepcion(db: Session, oc: OrdenCompra,
                             items: list[tuple[DetalleOrdenCompra, Decimal]],
                             usuario_id: int | None):
    """Replica cantidades a Fabric/Supply + StockMovement + PurchaseLine (flush)."""
    try:
        from app.models.inventory import Fabric, StockMovement, Supply
        from app.models.inventario import ProductoInsumo as _P
        from app.models.purchasing import PurchaseLine, PurchaseOrder as LegacyPO
        for d, qty in items:
            prod = db.get(_P, d.producto_id)
            if not prod:
                continue
            fab = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
            if fab:
                fab.stock_metros = round((fab.stock_metros or 0) + float(qty), 2)
            else:
                sup = db.query(Supply).filter(Supply.codigo == prod.sku).first()
                if sup:
                    sup.stock = round((sup.stock or 0) + float(qty), 2)
            db.add(StockMovement(item_tipo="fabric" if fab else "supply",
                                 item_id=(fab.id if fab else sup.id) if (fab or sup) else 0,
                                 cantidad=float(qty), tipo="entrada",
                                 motivo=f"OC {oc.folio or oc.id}", usuario_id=usuario_id))
        if oc.folio:
            po = db.query(LegacyPO).filter(LegacyPO.folio == oc.folio).first()
            if po:
                for d, qty in items:
                    prod = db.get(_P, d.producto_id)
                    if not prod:
                        continue
                    for ln in db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).all():
                        hit = False
                        if ln.item_tipo == "fabric":
                            f = db.get(Fabric, ln.item_id)
                            hit = bool(f and f.codigo == prod.sku)
                        elif ln.item_tipo == "supply":
                            s = db.get(Supply, ln.item_id)
                            hit = bool(s and s.codigo == prod.sku)
                        if hit:
                            resto = _d(ln.cantidad) - _d(ln.cantidad_recibida)
                            add = min(qty, resto)
                            if add > 0:
                                ln.cantidad_recibida = round(float(_d(ln.cantidad_recibida) + add), 2)
                                qty -= add
                            break
                lines = db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).all()
                if lines:
                    if all(_d(l.cantidad) - _d(l.cantidad_recibida) <= Decimal("0.000001") for l in lines):
                        po.estado = "recibida"
                    else:
                        po.estado = "recibida_parcial"
        db.flush()
    except Exception:
        pass


def _espejo_legacy_stock(db: Session, prod: ProductoInsumo, qty_signed: Decimal,
                         tipo: str, motivo: str, usuario_id: int | None):
    """Replica un movimiento spec a Fabric/Supply + StockMovement (flush)."""
    try:
        from app.models.inventory import Fabric, StockMovement, Supply
        fab = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
        if fab:
            fab.stock_metros = round(max(0, (fab.stock_metros or 0) + float(qty_signed)), 2)
            db.add(StockMovement(item_tipo="fabric", item_id=fab.id, cantidad=float(qty_signed),
                                 tipo=tipo, motivo=motivo, usuario_id=usuario_id))
        else:
            sup = db.query(Supply).filter(Supply.codigo == prod.sku).first()
            if sup:
                sup.stock = round(max(0, (sup.stock or 0) + float(qty_signed)), 2)
                db.add(StockMovement(item_tipo="supply", item_id=sup.id, cantidad=float(qty_signed),
                                     tipo=tipo, motivo=motivo, usuario_id=usuario_id))
        db.flush()
    except Exception:
        pass
