"""ContabilidadService — motor transaccional atómico del ERP (PCGE Perú).

CADA evento económico genera su asiento de partida doble en la MISMA
transacción que sus efectos de gestión (CxC/CxP, flujo, pagos). Regla:
UN evento = UN db.commit() final; cualquier error => db.rollback() total.
Nada de "contabilización best-effort" en transacción separada.

Mapa de eventos (cuentas analíticas):
  VENTA / FACTURA : DEBE 1212 (CxC) / HABER 40111 (IGV) + HABER 70 ventas
    - Bespoke / servicio a medida (pedido con prendas) → 7032
    - Ready-to-wear / stock (venta de variantes) → 7011
  COBRO          : DEBE 1011 (efectivo) o 1041 (transferencia/tarjeta/yape) / HABER 1212 + INGRESO flujo
  COMPRA / GASTO : DEBE 602 (materias primas) + DEBE 40111 / HABER 4212 (+ CxP)
  COSTO VENTAS   : DEBE 6911 / HABER 2111 (solo ready-to-wear, al vender)
  PAGO CxP       : DEBE 4212 / HABER 1011/1041 + EGRESO flujo
  KARDEX         : ver app/services/compras_kardex.py (2411/6111, 6591/2411)

Bloqueo de períodos: exigir_periodo_abierto() deniega cualquier evento con
fecha en período CERRADO/BLOQUEADO (los routers traducen a 400).
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.billing import CashMovement
from app.models.finanzas import (
    CuentaPorCobrar,
    CuentaPorPagar,
    GastoRegistrado,
    MovimientoFinanciero,
    PeriodoContable,
)
from app.models.order import Order, Payment
from app.services.finanzas import crear_asiento_flush, get_cuenta_by_codigo, seed_pcge_basico

# Cuentas analíticas del motor
CTA_CXC = "1212"     # Clientes — Emitidas en cartera
CTA_IGV = "40111"    # IGV crédito/débito fiscal
CTA_VENTAS = "7011"  # Ventas locales (fallback / ready-to-wear)
CTA_VENTAS_BESPOKE = "7032"  # Servicios prestados (sastrería a medida)
CTA_VENTAS_RTW = "7011"      # Productos terminados (colecciones stock)
CTA_COSTO_VTAS = "6911"  # Costo de ventas — productos terminados
CTA_PT = "2111"          # Productos terminados (activo)
CTA_CAJA = "1011"    # Caja operativa (solo efectivo)
CTA_BANCO = "1041"   # Cuentas corrientes (transferencia/tarjeta/yape)
CTA_ANTICIPOS = "1221"  # Anticipos de clientes
CTA_COMPRAS = "602"  # Compras de materias primas
CTA_PROV = "4212"    # Proveedores


def cuenta_cobro_por_metodo(metodo: str | None) -> str:
    """Cuenta 10 según medio de pago: efectivo → 1011, resto → 1041."""
    return CTA_CAJA if (metodo or "").strip().lower() == "efectivo" else CTA_BANCO


def cuenta_ingreso_por_pedido(db: Session, order_id: int) -> str:
    """Línea de negocio: con prendas a medida → 7032 (bespoke);
    venta de stock sin prendas → 7011 (ready-to-wear)."""
    from app.models.order import Garment
    n = db.query(Garment).filter(Garment.order_id == order_id).count()
    return CTA_VENTAS_BESPOKE if n > 0 else CTA_VENTAS_RTW

# Flujo de caja por actividad (sin cambio de esquema: mapea la categoría
# operativa del MovimientoFinanciero a actividad del estado de flujos).
ACTIVIDADES = ("OPERATIVA", "INVERSION", "FINANCIAMIENTO")
CATEGORIA_ACTIVIDAD: dict[str, str] = {
    "Venta de Trajes": "OPERATIVA",
    "Compra de Telas": "OPERATIVA",
    "Costos Operativos": "OPERATIVA",
    "Pago Servicios": "OPERATIVA",
    "Pago de Servicios": "OPERATIVA",
    "Planilla": "OPERATIVA",
    "Alquiler": "OPERATIVA",
    "Activo Fijo": "INVERSION",
    "Compra Activo Fijo": "INVERSION",
    "Préstamo": "FINANCIAMIENTO",
    "Aporte Capital": "FINANCIAMIENTO",
}
CUENTA_ORIGEN_CAJA = {"Caja", "Caja General", "101", "1011"}


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def exigir_periodo_abierto(db: Session, fecha: date | None) -> PeriodoContable:
    """Bloqueo duro: raise si la fecha cae en período CERRADO/BLOQUEADO.

    Períodos inexistentes se crean ABIERTOS (flush, sin commit: el commit lo
    hace la transacción del evento llamante).
    """
    fecha = fecha or date.today()
    p = db.query(PeriodoContable).filter(
        PeriodoContable.anio == fecha.year, PeriodoContable.mes == fecha.month).first()
    if not p:
        p = PeriodoContable(anio=fecha.year, mes=fecha.month, estado="ABIERTO")
        db.add(p)
        db.flush()
    if p.estado != "ABIERTO":
        raise ValueError(f"Período {p.anio}-{p.mes:02d} {p.estado}: transacción denegada")
    return p


def _cuenta(db: Session, codigo: str):
    c = get_cuenta_by_codigo(db, codigo)
    if not c:
        raise ValueError(f"Falta cuenta PCGE {codigo} (seed PCGE)")
    return c


def actividad_de(categoria: str | None) -> str:
    return CATEGORIA_ACTIVIDAD.get((categoria or "").strip(), "OPERATIVA")


def es_caja(cuenta_origen: str | None) -> bool:
    return (cuenta_origen or "Caja") in CUENTA_ORIGEN_CAJA


# ── VENTA: factura 1212 / 40111 + 70 (7032 bespoke / 7011 RTW) ──────
def anticipo_pendiente_facturar(db: Session, order: Order) -> float:
    """Anticipo cobrado aún no comprobantado (facturas ANTICIPO no anuladas)."""
    from app.models.billing import Invoice
    fact = sum(i.total for i in db.query(Invoice).filter(
        Invoice.order_id == order.id, Invoice.tipo == "ANTICIPO",
        Invoice.estado != "anulada").all())
    return round(max(float(order.anticipo or 0) - fact, 0.0), 2)


def base_anticipos_facturados(db: Session, order_id: int) -> float:
    """Base (1221) acumulada en facturas de anticipo: lo diferido a aplicar."""
    from app.models.billing import Invoice
    return round(sum(i.subtotal for i in db.query(Invoice).filter(
        Invoice.order_id == order_id, Invoice.tipo == "ANTICIPO",
        Invoice.estado != "anulada").all()), 2)


def emitir_factura_venta(db: Session, order_id: int, serie: str, igv_pct: float,
                         usuario_id: int | None, fecha: date | None = None,
                         modo: str = "TOTAL", monto: float | None = None) -> dict:
    """Emite comprobante + asiento de venta + CxC en UNA transacción.

    modos (montos finales con IGV incluido):
      TOTAL    : orden completa → 1212 / 40111 + 70 (línea de negocio).
      ANTICIPO : adelanto → 1212 / 40111 + 1221 Anticipos de Clientes.
      SALDO    : comprobante final → aplica 1221 previa, 1212 por el saldo y
                 base total a 70 (7032 bespoke).
    """
    from app.services import billing as billing_svc

    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        order = db.query(Order).with_for_update().filter(Order.id == order_id).first()
        if not order:
            raise ValueError("Pedido no encontrado")
        modo = (modo or "TOTAL").upper()
        if modo not in ("TOTAL", "ANTICIPO", "SALDO"):
            modo = "TOTAL"
        pend_anticipo = anticipo_pendiente_facturar(db, order)
        saldo = round(max(float(order.total or 0) - float(order.anticipo or 0), 0.0), 2)
        if modo == "ANTICIPO":
            if pend_anticipo <= 0:
                raise ValueError("Sin anticipo pendiente de facturar")
            m = float(monto) if monto else pend_anticipo
            m = round(min(max(m, 0.01), pend_anticipo), 2)
            inv = billing_svc.emit_invoice_flush(
                db, serie, order_id, order.client_id, order.company_id, igv_pct,
                usuario_id, lineas=[{"concepto": f"Anticipo pedido {order.folio}",
                                     "importe": m}])
            inv.tipo = "ANTICIPO"
            db.flush()
            base, igv = _d(inv.subtotal), _d(inv.igv)
            c1212, c40111 = _cuenta(db, CTA_CXC), _cuenta(db, CTA_IGV)
            c1221 = _cuenta(db, CTA_ANTICIPOS)
            lineas = [{"cuenta_id": c1212.id, "debe": base + igv, "haber": Decimal("0")},
                      {"cuenta_id": c40111.id, "debe": Decimal("0"), "haber": igv},
                      {"cuenta_id": c1221.id, "debe": Decimal("0"), "haber": base}]
        elif modo == "SALDO":
            if saldo <= 0:
                raise ValueError("Sin saldo pendiente de facturar")
            m = float(monto) if monto else saldo
            m = round(min(max(m, 0.01), saldo), 2)
            inv = billing_svc.emit_invoice_flush(
                db, serie, order_id, order.client_id, order.company_id, igv_pct,
                usuario_id, lineas=[{"concepto": f"Saldo pedido {order.folio}",
                                     "importe": m}])
            inv.tipo = "FINAL"
            db.flush()
            base_s, igv_s = _d(inv.subtotal), _d(inv.igv)
            # Base total de la orden (neta) y aplicación de anticipos previos.
            total_full = _d(order.total)
            div = Decimal("1") + _d(igv_pct) / Decimal("100")
            base_full = (total_full / div).quantize(Decimal("0.01"))
            aplic = min(_d(base_anticipos_facturados(db, order.id)),
                        max(base_full - base_s, Decimal("0")))
            base_70 = aplic + base_s  # cuadra por construcción
            c1221 = _cuenta(db, CTA_ANTICIPOS)
            c1212, c40111 = _cuenta(db, CTA_CXC), _cuenta(db, CTA_IGV)
            c_ing = _cuenta(db, cuenta_ingreso_por_pedido(db, order.id))
            lineas = []
            if aplic > 0:
                lineas.append({"cuenta_id": c1221.id, "debe": aplic, "haber": Decimal("0")})
            lineas += [{"cuenta_id": c1212.id, "debe": base_s + igv_s, "haber": Decimal("0")},
                       {"cuenta_id": c40111.id, "debe": Decimal("0"), "haber": igv_s},
                       {"cuenta_id": c_ing.id, "debe": Decimal("0"), "haber": base_70}]
            base, igv = base_s, igv_s
        else:
            inv = billing_svc.emit_invoice_flush(
                db, serie, order_id, order.client_id, order.company_id, igv_pct, usuario_id)
            base, igv = _d(inv.subtotal), _d(inv.igv)
            c1212, c40111 = _cuenta(db, CTA_CXC), _cuenta(db, CTA_IGV)
            c_ing = _cuenta(db, cuenta_ingreso_por_pedido(db, order.id))
            lineas = [{"cuenta_id": c1212.id, "debe": base + igv, "haber": Decimal("0")}]
            if igv > 0:
                lineas.append({"cuenta_id": c40111.id, "debe": Decimal("0"), "haber": igv})
            lineas.append({"cuenta_id": c_ing.id, "debe": Decimal("0"), "haber": base})
        asiento = crear_asiento_flush(
            db, fecha or date.today(), f"Factura {inv.serie}-{inv.numero} {order.folio}",
            "VENTA", inv.id, lineas)
        # CxC (crea o actualiza contra el total del pedido)
        cxc = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == order.id).first()
        total = _d(order.total)
        if not cxc:
            pagado = _d(order.anticipo)
            cxc = CuentaPorCobrar(
                order_id=order.id, orden_venta_id=order.id, cliente_id=order.client_id,
                company_id=order.company_id, monto_total=float(total),
                monto_pagado=float(pagado), saldo_pendiente=float(total - pagado),
                fecha_vencimiento=order.fecha_entrega,
                estado="COBRADO_PARCIAL" if pagado > 0 else "PENDIENTE")
            db.add(cxc)
            db.flush()
        db.commit()
        return {"invoice_id": inv.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "cxc_id": cxc.id}
    except Exception:
        db.rollback()
        raise


# ── COSTO DE VENTAS RTW: 6911 / 2111 ────────────────────────────────
def registrar_costo_ventas(db: Session, order_id: int, monto: float,
                           usuario_id: int | None = None,
                           fecha: date | None = None) -> dict:
    """Costo de ventas de productos en stock (ready-to-wear) valorizado a CPP.

    Solo para ventas sin prendas a medida. Atómico con el mismo commit final.
    """
    from app.services.finanzas import crear_asiento_flush

    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        order = db.get(Order, order_id)
        if not order:
            raise ValueError("Pedido no encontrado")
        monto_d = _d(monto)
        if monto_d <= 0:
            raise ValueError("Monto de costo inválido")
        c_costo = _cuenta(db, CTA_COSTO_VTAS)
        c_pt = _cuenta(db, CTA_PT)
        asiento = crear_asiento_flush(
            db, fecha or date.today(), f"Costo ventas {order.folio}", "COSTO_VENTAS",
            order.id,
            [{"cuenta_id": c_costo.id, "debe": monto_d, "haber": Decimal("0")},
             {"cuenta_id": c_pt.id, "debe": Decimal("0"), "haber": monto_d}])
        db.commit()
        return {"asiento_id": asiento.id, "asiento_numero": asiento.numero,
                "monto": float(monto_d)}
    except Exception:
        db.rollback()
        raise


# ── COBRO: 1011/1041 / 1212 + flujo ─────────────────────────────────
def cobrar_venta(db: Session, order_id: int, monto: float, metodo: str,
                 cuenta_codigo: str = "1011", usuario_id: int | None = None,
                 fecha: date | None = None, exigir_turno: bool = False,
                 turno_id: int | None = None) -> dict:
    """Cobra (total o abono): Payment + Order.anticipo + Cash/MovFin + CxC +
    asiento COBRO, todo en UNA transacción. Estados CxC: PENDIENTE /
    COBRADO_PARCIAL / COBRADO (=PAGADO)."""
    from app.services import ventas as ventas_svc

    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        order = db.query(Order).with_for_update().filter(Order.id == order_id).first()
        if not order:
            raise ValueError("Pedido no encontrado")
        if exigir_turno and turno_id is None:
            t = ventas_svc.turno_abierto(db, usuario_id or 0)
            if not t:
                raise ValueError("TURNO_CERRADO")
            turno_id = t.id
        monto_d = min(_d(monto), _d(order.total) - _d(order.anticipo))
        if monto_d <= 0:
            raise ValueError("La orden no tiene saldo pendiente")
        metodo_n = (metodo or "efectivo").lower()
        # Cuenta 10 según medio de pago (1011 solo efectivo, resto 1041).
        cuenta_codigo = cuenta_cobro_por_metodo(metodo_n)
        pago = Payment(order_id=order.id, monto=float(monto_d), metodo=metodo_n,
                       usuario_id=usuario_id)
        db.add(pago)
        db.flush()
        order.anticipo = float(_d(order.anticipo) + monto_d)
        db.add(CashMovement(tipo="ingreso", concepto=f"Cobro {order.folio}",
                            monto=float(monto_d), metodo=metodo_n, order_id=order.id,
                            turno_id=turno_id, usuario_id=usuario_id))
        db.add(MovimientoFinanciero(tipo="INGRESO", categoria="Venta de Trajes",
                                    monto=float(monto_d),
                                    cuenta_origen="Caja" if cuenta_codigo == "1011" else "Banco",
                                    comprobante_ref=order.folio, usuario_id=usuario_id,
                                    descripcion=f"Cobro {order.folio}"))
        # CxC amortizable
        cxc = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == order.id).first()
        if not cxc:
            cxc = CuentaPorCobrar(order_id=order.id, orden_venta_id=order.id,
                                  cliente_id=order.client_id, company_id=order.company_id,
                                  monto_total=float(_d(order.total)), fecha_vencimiento=order.fecha_entrega)
            db.add(cxc)
            db.flush()
        cxc.monto_total = float(_d(order.total))
        cxc.monto_pagado = float(_d(order.anticipo))
        cxc.saldo_pendiente = float(max(_d(order.total) - _d(order.anticipo), Decimal("0")))
        cxc.estado = "COBRADO" if _d(cxc.saldo_pendiente) <= Decimal("0.01") else (
            "COBRADO_PARCIAL" if _d(cxc.monto_pagado) > 0 else "PENDIENTE")
        # Asiento COBRO: DEBE caja/banco / HABER CxC
        c_caja = _cuenta(db, cuenta_codigo)
        c_cli = _cuenta(db, CTA_CXC)
        asiento = crear_asiento_flush(
            db, fecha or date.today(), f"Cobro {order.folio} {metodo_n}", "COBRO", pago.id,
            [{"cuenta_id": c_caja.id, "debe": monto_d, "haber": Decimal("0")},
             {"cuenta_id": c_cli.id, "debe": Decimal("0"), "haber": monto_d}])
        db.commit()
        return {"payment_id": pago.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "cxc_id": cxc.id,
                "monto": float(monto_d), "saldo": float(_d(order.total) - _d(order.anticipo))}
    except Exception:
        db.rollback()
        raise


# ── COMPRA manual: 602 + 40111 / 4212 + CxP ────────────────────────
def provisionar_compra(db: Session, proveedor_id: int, base: float, igv: float = 0.0,
                       numero_factura: str | None = None, fecha: date | None = None,
                       retencion: float = 0.0, fecha_vencimiento: date | None = None,
                       purchase_order_id: int | None = None,
                       orden_compra_id: int | None = None) -> dict:
    """Provisión de compra/gasto con factura: asiento + CxP amortizable."""
    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        base_d, igv_d = _d(base), _d(igv)
        if base_d <= 0:
            raise ValueError("base debe ser positiva")
        if igv_d < 0:
            raise ValueError("IGV no puede ser negativo")
        if _d(retencion) < 0 or _d(retencion) > base_d + igv_d:
            raise ValueError("retención inválida")
        total = base_d + igv_d
        c6011, c40111, c4212 = _cuenta(db, CTA_COMPRAS), _cuenta(db, CTA_IGV), _cuenta(db, CTA_PROV)
        lineas = [{"cuenta_id": c6011.id, "debe": base_d, "haber": Decimal("0")}]
        if igv_d > 0:
            lineas.append({"cuenta_id": c40111.id, "debe": igv_d, "haber": Decimal("0")})
        lineas.append({"cuenta_id": c4212.id, "debe": Decimal("0"), "haber": total})
        fecha = fecha or date.today()
        # CxP primero (origen del asiento)
        cxp = CuentaPorPagar(proveedor_id=proveedor_id, purchase_order_id=purchase_order_id,
                             orden_compra_id=orden_compra_id,
                             numero_factura=(numero_factura or None),
                             monto_total=float(total), monto_pagado=0.0,
                             saldo_pendiente=float(total - _d(retencion)),
                             retencion=float(_d(retencion)), fecha_emision=fecha,
                             fecha_vencimiento=fecha_vencimiento, estado="POR_PAGAR")
        db.add(cxp)
        db.flush()
        asiento = crear_asiento_flush(
            db, fecha, f"Compra {numero_factura or cxp.id}", "COMPRA", cxp.id, lineas)
        db.commit()
        return {"cxp_id": cxp.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "total": float(total)}
    except Exception:
        db.rollback()
        raise


# ── PAGO CxP: 4212 / 1011/1041 + flujo ──────────────────────────────
def pagar_proveedor(db: Session, cxp_id: int, monto: float, cuenta_codigo: str = "1041",
                    usuario_id: int | None = None, fecha: date | None = None) -> dict:
    """Abono a proveedor: CxP + MovFin EGRESO + Cash + asiento PAGO, atómico.
    Estados: POR_PAGAR / PARCIAL / PAGADO."""
    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        cxp = db.query(CuentaPorPagar).with_for_update().filter(
            CuentaPorPagar.id == cxp_id).first()
        if not cxp:
            raise ValueError("Cuenta por pagar no encontrada")
        monto_d = _d(monto)
        if monto_d <= 0 or monto_d - _d(cxp.saldo_pendiente) > Decimal("0.000001"):
            raise ValueError(f"Monto inválido (saldo: {cxp.saldo_pendiente})")
        cxp.monto_pagado = float(_d(cxp.monto_pagado) + monto_d)
        cxp.saldo_pendiente = float(max(
            _d(cxp.monto_total) - _d(cxp.monto_pagado) - _d(cxp.retencion), Decimal("0")))
        cxp.estado = "PAGADO" if _d(cxp.saldo_pendiente) <= Decimal("0.01") else "PARCIAL"
        ref = cxp.numero_factura or f"CxP-{cxp.id}"
        db.add(MovimientoFinanciero(tipo="EGRESO", categoria="Compra de Telas",
                                    monto=float(monto_d),
                                    cuenta_origen="Banco" if cuenta_codigo == "1041" else "Caja",
                                    comprobante_ref=ref, usuario_id=usuario_id,
                                    descripcion=f"Pago CxP {ref}"))
        db.add(CashMovement(tipo="egreso", concepto=f"Pago proveedor {ref}",
                            monto=float(monto_d), metodo="transferencia",
                            usuario_id=usuario_id))
        c_prov = _cuenta(db, CTA_PROV)
        c_caja = _cuenta(db, cuenta_codigo if cuenta_codigo in ("1011", "1041") else "1041")
        asiento = crear_asiento_flush(
            db, fecha or date.today(), f"Pago {ref}", "PAGO", cxp.id,
            [{"cuenta_id": c_prov.id, "debe": monto_d, "haber": Decimal("0")},
             {"cuenta_id": c_caja.id, "debe": Decimal("0"), "haber": monto_d}])
        db.commit()
        return {"cxp_id": cxp.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "estado": cxp.estado,
                "saldo": cxp.saldo_pendiente}
    except Exception:
        db.rollback()
        raise


# ── GASTO operativo atómico (provisión; el pago va por pagar_gasto) ──
def registrar_gasto_atomico(db: Session, fecha: date, categoria: str,
                            monto_base: float | Decimal, monto_igv: float | Decimal = 0,
                            cuenta_codigo: str | None = None, proveedor_id: int | None = None,
                            ruc_proveedor: str | None = None, tipo_comprobante: str | None = None,
                            numero_comprobante: str | None = None,
                            centro_costo_id: int | None = None,
                            variabilidad: str = "FIJO",
                            clasificacion: str | None = None) -> tuple[GastoRegistrado, object]:
    """Provisión de gasto con bloqueo de período, en UNA transacción."""
    from app.services import finanzas as fin

    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        gasto, asiento = fin.registrar_gasto_operativo(
            db, fecha=fecha, categoria=categoria, monto_base=monto_base,
            monto_igv=monto_igv, cuenta_codigo=cuenta_codigo, proveedor_id=proveedor_id,
            ruc_proveedor=ruc_proveedor, tipo_comprobante=tipo_comprobante,
            numero_comprobante=numero_comprobante, centro_costo_id=centro_costo_id,
            variabilidad=variabilidad, clasificacion=clasificacion)
        return gasto, asiento
    except Exception:
        # registrar_gasto_operativo commitea internamente; si el asiento falló
        # tras persistir el gasto, se revierte el gasto huérfano.
        try:
            db.rollback()
        except Exception:
            pass
        raise


def pagar_gasto_atomico(db: Session, gasto_id: int, cuenta_origen_codigo: str = "104",
                        usuario_id: int | None = None,
                        fecha: date | None = None) -> dict:
    """Pago de gasto: 4212 / caja-banco + MovFin EGRESO + Cash, atómico."""
    from app.services import finanzas as fin

    seed_pcge_basico(db)
    try:
        exigir_periodo_abierto(db, fecha)
        gasto = db.query(GastoRegistrado).with_for_update().filter(
            GastoRegistrado.id == gasto_id).first()
        if not gasto:
            raise ValueError("Gasto no encontrado")
        if gasto.estado == "PAGADO":
            raise ValueError("El gasto ya está PAGADO")
        total = _d(gasto.monto_total)
        if total <= 0:
            raise ValueError("Total inválido")
        c_prov = _cuenta(db, CTA_PROV)
        c_caja = _cuenta(db, cuenta_origen_codigo if cuenta_origen_codigo in ("101", "104") else "104")
        asientos_antes = {a.id for a in db.query(fin.AsientoContable).filter(
            fin.AsientoContable.origen_tipo == "PAGO",
            fin.AsientoContable.origen_id == gasto.id).all()}
        asiento = crear_asiento_flush(
            db, fecha or date.today(), f"Pago gasto {gasto.numero_comprobante or gasto.id}",
            "PAGO", gasto.id,
            [{"cuenta_id": c_prov.id, "debe": total, "haber": Decimal("0")},
             {"cuenta_id": c_caja.id, "debe": Decimal("0"), "haber": total}])
        gasto.estado = "PAGADO"
        db.add(MovimientoFinanciero(tipo="EGRESO", categoria="Costos Operativos",
                                    monto=float(total),
                                    cuenta_origen="Banco" if c_caja.codigo == "104" else "Caja",
                                    comprobante_ref=gasto.numero_comprobante,
                                    usuario_id=usuario_id,
                                    descripcion=f"Pago gasto {gasto.categoria}"))
        db.add(CashMovement(tipo="egreso",
                            concepto=f"Pago gasto {gasto.numero_comprobante or gasto.id}",
                            monto=float(total), metodo="transferencia",
                            usuario_id=usuario_id))
        db.commit()
        return {"gasto_id": gasto.id, "asiento_id": asiento.id,
                "nuevos_asientos": sorted(set(asientos_antes) | {asiento.id})}
    except Exception:
        db.rollback()
        raise


# ── Flujo de caja real por actividad ─────────────────────────────────
def flujo_por_actividad(db: Session, desde=None, hasta=None) -> dict:
    """Consolida MovimientoFinanciero liquidados: por actividad (operativa /
    inversión / financiamiento) y por origen (caja vs bancos)."""
    q = db.query(MovimientoFinanciero)
    if desde:
        q = q.filter(MovimientoFinanciero.fecha >= desde)
    if hasta:
        q = q.filter(MovimientoFinanciero.fecha <= hasta)
    movs = q.all()
    por_act: dict[str, dict[str, Decimal]] = {
        a: {"ingresos": Decimal("0"), "egresos": Decimal("0")} for a in ACTIVIDADES}
    caja = {"ingresos": Decimal("0"), "egresos": Decimal("0")}
    banco = {"ingresos": Decimal("0"), "egresos": Decimal("0")}
    for m in movs:
        act = actividad_de(m.categoria)
        key = "ingresos" if m.tipo == "INGRESO" else "egresos"
        por_act[act][key] += _d(m.monto)
        (caja if es_caja(m.cuenta_origen) else banco)[key] += _d(m.monto)
    neto = {a: v["ingresos"] - v["egresos"] for a, v in por_act.items()}
    return {"por_actividad": por_act, "neto": neto,
            "caja": caja, "bancos": banco,
            "total_ingresos": sum(v["ingresos"] for v in por_act.values()),
            "total_egresos": sum(v["egresos"] for v in por_act.values()),
            "n": len(movs)}
