"""ContabilidadService — motor transaccional atómico del ERP (Plan Operativo).

CADA evento económico genera su asiento de partida doble en la MISMA
transacción que sus efectos de gestión (CxC/CxP, flujo, pagos). Regla:
UN evento = UN db.commit() final; cualquier error => db.rollback() total.

Las cuentas NO están cableadas: cada asiento se resuelve vía
`regla_contable` (app/services/motor_contable.py::post_regla) con
validación estricta (imputable + dimensiones obligatorias).

Bloqueo de períodos: exigir_periodo_abierto() deniega cualquier evento con
fecha en período CERRADO/BLOQUEADO (los routers traducen a 400).
"""
from datetime import date, timedelta
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
from app.services.finanzas import crear_asiento_flush, get_cuenta_by_codigo


def cuenta_cobro_por_metodo(metodo: str | None) -> str:
    """Cuenta 10 según medio de pago (centralizado en medios_pago)."""
    from app.services import medios_pago as _mp
    return _mp.cuenta_por_medio(metodo)


def cliente_publico_general(db: Session) -> int:
    """Cliente genérico S/D (único) para boletas a Público General.

    Get-or-create por (nombre, apellidos): todas las ventas al paso
    comparten este registro sin tocar el pedido (que sigue con
    client_id NULL como marca de PG).
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.client import Client
    cli = db.query(Client).filter(
        Client.nombre == "Público General",
        Client.apellidos == "S/D").first()
    if cli:
        return cli.id
    sp = db.begin_nested()
    try:
        cli = Client(nombre="Público General", apellidos="S/D",
                     tipo_doc="DNI", nro_doc=None, clasificacion="Nuevo")
        db.add(cli)
        db.flush()
    except IntegrityError:
        sp.rollback()  # otro proceso lo creó: solo revierte el savepoint
        cli = db.query(Client).filter(
            Client.nombre == "Público General",
            Client.apellidos == "S/D").first()
        if not cli:
            raise
        return cli.id
    else:
        sp.commit()  # libera el savepoint; el commit final lo hace el llamante
        return cli.id


def resolver_cliente_orden(db: Session, order: Order) -> int | None:
    """cliente_id para dims de venta/cobro, con espejos B2B y PG.

    - Con client_id: directo.
    - Solo company_id: espejo B2B por empresa.
    - Sin ambos (Público General): cliente genérico S/D.
    Así ninguna venta queda sin trazabilidad y las reglas
    VENTA_PT/FACTURA_* (cliente_id obligatorio) siempre cierran.
    """
    if getattr(order, "client_id", None):
        return order.client_id
    company_id = getattr(order, "company_id", None)
    if not company_id:
        return cliente_publico_general(db)
    from app.models.client import Client
    from app.models.company import Company
    cli = db.query(Client).filter(Client.company_id == company_id).first()
    if cli:
        return cli.id
    comp = db.get(Company, company_id)
    if comp and (comp.ruc or "").strip():
        cli = db.query(Client).filter(
            Client.nro_doc == comp.ruc.strip()).first()
        if cli:
            return cli.id
    nombre = ((comp.razon_social or comp.nombre_comercial)
              if comp else "")[:80] or f"Empresa #{company_id}"
    cli = Client(nombre=nombre, apellidos="(B2B)",
                 tipo_doc="RUC" if comp and (comp.ruc or "").strip() else "DNI",
                 nro_doc=(comp.ruc.strip() if comp and comp.ruc else None),
                 company_id=company_id, clasificacion="Nuevo")
    db.add(cli)
    db.flush()
    return cli.id


# Flujo de caja por actividad (sin cambio de esquema: mapea la categoría
# operativa del MovimientoFinanciero a actividad del estado de flujos).
ACTIVIDADES = ("OPERATIVA", "INVERSION", "FINANCIAMIENTO")
CATEGORIA_ACTIVIDAD: dict[str, str] = {
    "Venta de Trajes": "OPERATIVA",
    "Compra de Telas": "OPERATIVA",
    "Compra de Telas y Avíos": "OPERATIVA",
    "Costos Operativos": "OPERATIVA",
    "Costos Operativos / Servicios": "OPERATIVA",
    "Pago Servicios": "OPERATIVA",
    "Pago de Servicios": "OPERATIVA",
    "Pago de Planilla / Personal": "OPERATIVA",
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


def actividad_de(categoria: str | None) -> str:
    return CATEGORIA_ACTIVIDAD.get((categoria or "").strip(), "OPERATIVA")


def es_caja(cuenta_origen: str | None) -> bool:
    return (cuenta_origen or "Caja") in CUENTA_ORIGEN_CAJA


# ── VENTA (reglas VENTA_PT / FACTURA_ANTICIPO / FACTURA_SALDO) ──
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


def saldo_pendiente_facturar(db: Session, order: Order) -> float:
    """Saldo real por facturar: total − Σ comprobantes B001/F001 activos.

    Saldo Pendiente = Monto Total del Pedido − Suma de montos de
    comprobantes B001/F001 emitidos previamente (no anulados).
    Cubre TOTAL/ANTICIPO/FINAL: evita la doble facturación aunque se
    combinen modos (p.ej. ANTICIPO 50% + TOTAL 100%).
    """
    from app.models.billing import Invoice
    fact = sum(i.total for i in db.query(Invoice).filter(
        Invoice.order_id == order.id,
        Invoice.serie.in_(["B001", "F001"]),
        Invoice.estado != "anulada").all())
    return round(max(float(order.total or 0) - fact, 0.0), 2)


def _salida_venta_cta23_idempotente(db: Session, order_id: int,
                                    usuario_id: int | None = None,
                                    fecha: date | None = None) -> dict | None:
    """SALIDA física Cta 23 (SALIDA_VENTA) + asiento Costo de Ventas 69 vs 23.

    Flujo obligatorio bespoke: se dispara al entregar O al facturar
    (TOTAL/FINAL). Antes del costo, absorbe la MOD del pedido terminado
    al WIP (2311 vs 9211); el costo es materiales + MOD absorbida, de modo
    que el WIP queda en cero al costo real. Idempotente por pedido: una
    sola absorción, una sola fila SALIDA_VENTA y un solo asiento
    COSTO_VENTAS por orden. Nunca bloquea al llamante (retorna None si no
    hay costo que reconocer, igual registra la salida física con costo 0
    para trazabilidad Cta 23).
    """
    from app.models.inventario import MovimientoKardex
    from app.models.order import Garment, Order
    order = db.get(Order, order_id)
    if not order:
        return None
    ya = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == order_id,
        MovimientoKardex.tipo_movimiento == "SALIDA_VENTA").first()
    res_costo = None
    try:
        # Absorción MOD primero: el costo usa materiales + lo absorbido
        # (idéntica base) para que WIP y costo cuadren exacto.
        abs_mod = absorber_mod_pedido(db, order_id, usuario_id, fecha)
        monto_cierre = round(
            materiales_pedido(db, order_id)
            + float((abs_mod or {}).get("monto") or 0), 2)
        res_costo = registrar_costo_ventas_bespoke(
            db, order_id, usuario_id, fecha, monto=monto_cierre)
    except Exception:
        res_costo = None
    try:
        db.expire_all()
    except Exception:
        pass
    if ya:
        return {"salida_id": ya.id, "existente": True,
                "costo": res_costo}
    try:
        garments = db.query(Garment).filter(
            Garment.order_id == order_id).all()
        n = len(garments) or 1
        monto = 0.0
        try:
            monto = float(costo_produccion_pedido(db, order_id) or 0)
        except Exception:
            monto = 0.0
        if res_costo and isinstance(res_costo, dict) and res_costo.get("monto"):
            try:
                monto = float(res_costo.get("monto") or monto)
            except (TypeError, ValueError):
                pass
        asiento_id = None
        try:
            asiento_id = (res_costo or {}).get("asiento_id")
        except Exception:
            asiento_id = None
        db.add(MovimientoKardex(
            producto_id=None, tipo_movimiento="SALIDA_VENTA",
            cantidad=float(n),
            costo_unitario=round(monto / n, 2) if n and monto else 0.0,
            costo_total=monto, saldo_fisico=0.0, saldo_valorizado=0.0,
            orden_venta_id=order_id, asiento_id=asiento_id,
            doc_ref=order.folio, usuario_id=usuario_id,
            observacion=f"Salida venta {order.folio} x{n} (Cta 23)"))
        db.commit()
        fila = db.query(MovimientoKardex).filter(
            MovimientoKardex.orden_venta_id == order_id,
            MovimientoKardex.tipo_movimiento == "SALIDA_VENTA").order_by(
            MovimientoKardex.id.desc()).first()
        return {"salida_id": fila.id if fila else None,
                "costo": res_costo}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {"salida_id": None, "costo": res_costo}


def comprobante_activo_cubriente(db: Session, order_id: int):
    """Comprobante activo que cubre la orden (TOTAL o FINAL no anulado).

    Un ANTICIPO solo cubre el adelanto: no bloquea el SALDO final.
    Retorna el Invoice bloqueante o None."""
    from app.models.billing import Invoice
    return db.query(Invoice).filter(
        Invoice.order_id == order_id,
        Invoice.estado != "anulada",
        Invoice.tipo.in_(["TOTAL", "FINAL"])).order_by(
        Invoice.id.desc()).first()


def emitir_factura_venta(db: Session, order_id: int, serie: str, igv_pct: float,
                          usuario_id: int | None, fecha: date | None = None,
                          modo: str = "TOTAL", monto: float | None = None) -> dict:
    """Emite comprobante + asiento de venta + CxC en UNA transacción.

    Modos (montos finales con IGV incluido), resueltos por regla_contable:
      TOTAL    : VENTA_PT (121x / 7021x+40111).
      ANTICIPO : FACTURA_ANTICIPO (121x / 40111+1221).
      SALDO    : FACTURA_SALDO (1221+121x / 40111+7021x; la pata 1221 se
                 omite si no hay anticipos previos aplicados).
    """
    from app.services import billing as billing_svc
    from app.services.motor_contable import post_regla

    try:
        exigir_periodo_abierto(db, fecha)
        order = db.query(Order).with_for_update().filter(Order.id == order_id).first()
        if not order:
            raise ValueError("Pedido no encontrado")
        modo = (modo or "TOTAL").upper()
        if modo not in ("TOTAL", "ANTICIPO", "SALDO"):
            modo = "TOTAL"
        # Tope SUNAT: boleta a Público General solo bajo S/ 700.00.
        from app.services import peru as peru_svc
        peru_svc.exigir_cliente_sunat(
            order.total, bool(order.client_id or order.company_id))
        # Emisión única: un comprobante activo que cubra la orden (TOTAL o
        # FINAL) bloquea cualquier re-facturación. Solo se puede emitir de
        # nuevo si el previo está ANULADA (o tras Nota de Crédito/extorno).
        previo = comprobante_activo_cubriente(db, order.id)
        if previo is not None:
            raise ValueError(
                f"⚠️ Esta orden ya cuenta con un comprobante emitido activo "
                f"({previo.serie}-{previo.numero}). Debe anular el anterior "
                f"antes de emitir uno nuevo.")
        # Saldo real por facturar (anti doble-facturación entre modos).
        saldo_fact = saldo_pendiente_facturar(db, order)
        if saldo_fact <= 0:
            raise ValueError(
                "⚠️ Pedido totalmente facturado (saldo S/ 0.00).")
        pend_anticipo = anticipo_pendiente_facturar(db, order)
        saldo = round(max(float(order.total or 0) - float(order.anticipo or 0), 0.0), 2)
        dims = {"cliente_id": resolver_cliente_orden(db, order)}
        if modo == "ANTICIPO":
            if pend_anticipo <= 0:
                raise ValueError("Sin anticipo pendiente de facturar")
            m = float(monto) if monto else pend_anticipo
            m = round(min(max(m, 0.01), pend_anticipo, saldo_fact), 2)
            inv = billing_svc.emit_invoice_flush(
                db, serie, order_id, order.client_id, order.company_id, igv_pct,
                usuario_id, lineas=[{"concepto": f"Anticipo pedido {order.folio}",
                                     "importe": m}])
            inv.tipo = "ANTICIPO"
            db.flush()
            base, igv = _d(inv.subtotal), _d(inv.igv)
            asiento = post_regla(
                db, "FACTURA_ANTICIPO", [base + igv], [igv, base],
                dims, f"Factura {inv.serie}-{inv.numero} {order.folio}",
                "VENTA", None, fecha)
            db.flush()
            inv_id = inv.id
            # el inv se vincula abajo (origen_id del asiento = invoice)
            asiento.origen_id = inv.id
        elif modo == "SALDO":
            if saldo <= 0:
                raise ValueError("Sin saldo pendiente de facturar")
            m = float(monto) if monto else saldo
            m = round(min(max(m, 0.01), saldo, saldo_fact), 2)
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
            asiento = post_regla(
                db, "FACTURA_SALDO", [aplic, base_s + igv_s],
                [igv_s, base_70], dims,
                f"Factura {inv.serie}-{inv.numero} {order.folio}",
                "VENTA", None, fecha)
            db.flush()
            inv_id = inv.id
            asiento.origen_id = inv.id
            base, igv = base_s, igv_s
        else:
            # TOTAL por el saldo restante (nunca el bruto si ya hubo anticipos).
            m = round(min(float(monto) if monto else saldo_fact, saldo_fact), 2)
            if m <= 0:
                raise ValueError(
                    "⚠️ Pedido totalmente facturado (saldo S/ 0.00).")
            if m >= float(order.total or 0) - 0.01:
                inv = billing_svc.emit_invoice_flush(
                    db, serie, order_id, order.client_id, order.company_id, igv_pct, usuario_id)
            else:
                inv = billing_svc.emit_invoice_flush(
                    db, serie, order_id, order.client_id, order.company_id, igv_pct,
                    usuario_id, lineas=[{"concepto": f"Saldo pedido {order.folio}",
                                         "importe": m}])
            base, igv = _d(inv.subtotal), _d(inv.igv)
            asiento = post_regla(
                db, "VENTA_PT", [base + igv], [base, igv], dims,
                f"Factura {inv.serie}-{inv.numero} {order.folio}",
                "VENTA", None, fecha)
            db.flush()
            inv_id = inv.id
            asiento.origen_id = inv.id
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
        res = {"invoice_id": inv_id, "asiento_id": asiento.id,
               "asiento_numero": asiento.numero, "cxc_id": cxc.id}
        # Flujo obligatorio Cta 23: al facturar (TOTAL/FINAL) se genera la
        # SALIDA física SALIDA_VENTA + asiento Costo de Ventas 69 vs 23.
        # El ANTICIPO no mueve almacén (solo 1221). Idempotente y nunca
        # bloquea la facturación ya comprometida.
        if modo in ("TOTAL", "SALDO"):
            try:
                _salida_venta_cta23_idempotente(
                    db, order.id, usuario_id, fecha)
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "salida Cta23 factura %s omitida",
                    getattr(order, "folio", order_id), exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
        return res
    except Exception:
        db.rollback()
        raise


# ── ANULACIÓN: comprobante + asiento de extorno ─────────────────────
def anular_factura_venta(db: Session, invoice_id: int,
                         fecha: date | None = None) -> dict:
    """Anula un comprobante de venta con su asiento de extorno, en UNA transacción.

    - Invoice.estado → "anulada" (idempotente: si ya está anulada retorna el
      extorno existente sin duplicar).
    - Localiza el asiento original del Libro Diario (origen_tipo VENTA +
      origen_id = invoice_id).
    - Crea el ASIENTO DE EXTORNO (origen_tipo EXTORNO, fecha actual) con las
      líneas espejadas (debe↔haber por cuenta): revierte exactamente el asiento
      original cualquiera sea su modo (TOTAL: 1212 / 40111 + 70; ANTICIPO:
      1212 / 40111 + 1221; SALDO: 1221 + 1212 / 40111 + 70).
    - Marca el asiento original como ANULADO (bandera informativa para el
      Diario; NO se excluye de los reportes: el extorno espejo ya netea el
      efecto a cero en Mayor y Balance, y excluirlo duplicaría el reverso).
    - Sincroniza el espejo ComprobanteVenta → "anulada".
    - Las facturas de ANTICIPO anuladas vuelven a liberar su monto:
      anticipo_pendiente_facturar / base_anticipos_facturados ya excluyen
      estado "anulada".

    UN evento = UN db.commit() final; cualquier error => db.rollback() total.
    """
    from app.models.billing import Invoice
    from app.models.finanzas import AsientoContable
    from app.services.finanzas import crear_asiento_flush

    try:
        exigir_periodo_abierto(db, fecha)
        inv = db.query(Invoice).with_for_update().filter(
            Invoice.id == invoice_id).first()
        if not inv:
            raise ValueError("Comprobante no encontrado")
        if (inv.estado or "") == "anulada":
            ext = db.query(AsientoContable).filter(
                AsientoContable.origen_tipo == "EXTORNO",
                AsientoContable.origen_id == inv.id).order_by(
                AsientoContable.id.desc()).first()
            return {"invoice_id": inv.id, "estado": "anulada",
                    "existente": True,
                    "extorno_id": ext.id if ext else None,
                    "extorno_numero": ext.numero if ext else None}
        originales = db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "VENTA",
            AsientoContable.origen_id == inv.id).order_by(
            AsientoContable.id).all()
        inv.estado = "anulada"
        hoy = fecha or date.today()
        extornos = []
        for orig in originales:
            if (orig.estado or "") == "ANULADO":
                continue
            espejo = [{"cuenta_id": l.cuenta_id,
                       "debe": _d(l.haber), "haber": _d(l.debe),
                       "descripcion": l.descripcion,
                       "centro_costo_id": l.centro_costo_id,
                       "centro_gestion_id": l.centro_gestion_id}
                      for l in (orig.lineas or [])]
            espejo = [x for x in espejo
                      if x["debe"] > 0 or x["haber"] > 0]
            if not espejo:
                orig.estado = "ANULADO"
                continue
            ext = crear_asiento_flush(
                db, hoy,
                f"Extorno {orig.numero} — Anulación {inv.serie}-{inv.numero}",
                "EXTORNO", inv.id, espejo)
            orig.estado = "ANULADO"
            extornos.append(ext)
        try:
            from app.models.ventas import ComprobanteVenta
            for cv in db.query(ComprobanteVenta).filter(
                    ComprobanteVenta.legacy_invoice_id == inv.id).all():
                cv.estado = "anulada"
        except Exception:
            pass
        db.commit()
        primero = extornos[0] if extornos else None
        return {"invoice_id": inv.id, "estado": "anulada",
                "extorno_id": primero.id if primero else None,
                "extorno_numero": primero.numero if primero else None,
                "asientos_extornados": len(extornos)}
    except Exception:
        db.rollback()
        raise


# ── INGRESO PT (regla INGRESO_PT: 2311x / 7111, Cta 23) ──────────
def registrar_ingreso_pt(db: Session, monto: float, doc_ref: str | None = None,
                         usuario_id: int | None = None,
                         fecha: date | None = None,
                         producto_id: int | None = None,
                         almacen_id: int | None = None) -> dict:
    """Ingreso de producción terminada a stock (base del costo de ventas)."""
    from app.services.motor_contable import post_regla

    try:
        monto_d = _d(monto)
        if monto_d <= 0:
            raise ValueError("Monto de ingreso inválido")
        asiento = post_regla(
            db, "INGRESO_PT", [monto_d], [monto_d],
            {"producto_id": producto_id, "almacen_id": almacen_id},
            f"Ingreso PT {doc_ref or ''}".strip(), "PRODUCCION", None, fecha)
        db.commit()
        return {"asiento_id": asiento.id, "asiento_numero": asiento.numero,
                "monto": float(monto_d)}
    except Exception:
        db.rollback()
        raise


# ── COSTO DE VENTAS COLECCIÓN (regla COSTO_VENTA_PT: 6921x / 2111x) ──
def registrar_costo_ventas(db: Session, order_id: int, monto: float,
                           usuario_id: int | None = None,
                           fecha: date | None = None,
                           producto_id: int | None = None,
                           almacen_id: int | None = None) -> dict:
    """Costo de ventas de PT en stock (ready-to-wear) valorizado a CPP."""
    from app.services.motor_contable import post_regla

    try:
        order = db.get(Order, order_id)
        if not order:
            raise ValueError("Pedido no encontrado")
        monto_d = _d(monto)
        if monto_d <= 0:
            raise ValueError("Monto de costo inválido")
        asiento = post_regla(
            db, "COSTO_VENTA_PT", [monto_d], [monto_d],
            {"producto_id": producto_id, "almacen_id": almacen_id},
            f"Costo ventas {order.folio}", "COSTO_VENTAS", order.id, fecha)
        db.commit()
        return {"asiento_id": asiento.id, "asiento_numero": asiento.numero,
                "monto": float(monto_d)}
    except Exception:
        db.rollback()
        raise


# ── COSTO BESPOKE (regla COSTO_VENTA_BESPOKE: 6921x / 2311x) ──
def materiales_pedido(db: Session, order_id: int) -> float:
    """Materiales del pedido: Kardex SALIDA_CONSUMO_TALLER valorizado a CPP.

    None-safe: registros nulos suman 0.0.
    """
    from sqlalchemy import func as _func

    from app.models.inventario import MovimientoKardex

    try:
        mat = db.query(_func.coalesce(_func.sum(MovimientoKardex.costo_total), 0)).filter(
            MovimientoKardex.orden_venta_id == order_id,
            MovimientoKardex.tipo_movimiento.in_(
                ["SALIDA_CONSUMO_TALLER", "SALIDA_TALLER"])).scalar() or 0
    except Exception:
        mat = 0
    try:
        return round(float(mat or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def mo_estimada_pedido(db: Session, order_id: int) -> tuple[float, float, str]:
    """M.O. estimada del pedido: (minutos, monto, fuente).

    Minutos por `minutos_mod_pedido` (tareo → sam → SAM estándar);
    monto = minutos × tarifa_minuto_taller (fallback 0.35). None-safe.
    """
    try:
        from app.services.finanzas import minutos_mod_pedido, tarifa_minuto_taller
        minutos, fuente = minutos_mod_pedido(db, order_id)
        try:
            tarifa = float(tarifa_minuto_taller(db) or 0.35)
        except Exception:
            tarifa = 0.35
        if not tarifa or tarifa <= 0:
            tarifa = 0.35
        return round(float(minutos or 0), 2), round(float(minutos or 0) * tarifa, 2), fuente
    except Exception:
        return 0.0, 0.0, "sin_datos"


def costo_produccion_pedido(db: Session, order_id: int) -> float:
    """Costo de producción del pedido: materiales Kardex + M.O. estimada.

    M.O. = minutos (tareo → sam → SAM estándar) × tarifa_minuto_taller.
    None-safe: registros nulos suman 0.0; sin prendas/insumos → 0.0.
    """
    try:
        _min, mo, _fte = mo_estimada_pedido(db, order_id)
    except Exception:
        mo = 0.0
    return round(materiales_pedido(db, order_id) + (mo or 0.0), 2)


def _existe_absorcion_mod(db: Session, order_id: int) -> bool:
    """True si el pedido ya tiene su absorción de MOD (origen ABSORCION_MOD)."""
    try:
        from app.models.finanzas import AsientoContable as _Asiento
        return db.query(_Asiento).filter(
            _Asiento.origen_tipo == "ABSORCION_MOD",
            _Asiento.origen_id == order_id).first() is not None
    except Exception:
        return False


def monto_mod_absorbido(db: Session, order_id: int) -> float:
    """MOD ya absorbida al WIP del pedido (suma HABER 9211 en ABSORCION_MOD)."""
    try:
        from app.models.finanzas import (
            AsientoContable as _Asiento,
            LineaAsientoContable as _Linea,
        )
        from app.services.finanzas import get_cuenta_by_codigo
        c9211 = get_cuenta_by_codigo(db, "9211")
        if c9211 is None:
            return 0.0
        aids = [a.id for a in db.query(_Asiento).filter(
            _Asiento.origen_tipo == "ABSORCION_MOD",
            _Asiento.origen_id == order_id).all()]
        if not aids:
            return 0.0
        from sqlalchemy import func as _func
        tot = db.query(_func.coalesce(_func.sum(_Linea.haber), 0)).filter(
            _Linea.asiento_id.in_(aids),
            _Linea.cuenta_id == c9211.id).scalar() or 0
        return round(float(tot or 0), 2)
    except Exception:
        return 0.0


def absorber_mod_pedido(db: Session, order_id: int,
                        usuario_id: int | None = None,
                        fecha: date | None = None) -> dict:
    """Absorbe la MOD del pedido terminado al WIP (2311 vs 9211).

    Asignación automática: minutos (tareo → sam → SAM estándar) × tarifa
    de planilla vigente. Idempotente por pedido (un ABSORCION_MOD).
    Retorna {"monto", "minutos", "fuente", "asiento_id", ...}; con MOD 0
    no postea nada (el 9211 no tiene qué aplicar a este pedido).
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError("Pedido no encontrado")
    if _existe_absorcion_mod(db, order_id):
        return {"monto": monto_mod_absorbido(db, order_id),
                "asiento_id": None, "existente": True}
    try:
        minutos, monto, fuente = mo_estimada_pedido(db, order_id)
    except Exception:
        minutos, monto, fuente = 0.0, 0.0, "sin_datos"
    if not monto or monto <= 0:
        return {"monto": 0.0, "minutos": minutos, "fuente": fuente,
                "asiento_id": None, "omitido": "sin_mod"}
    try:
        from app.services.motor_contable import dim_centro, post_manual
        cc921 = dim_centro(db, "921")
        asiento = post_manual(
            db, [("2311", _d(monto))], [("9211", _d(monto))],
            {"centro_costo_id": cc921,
             "cliente_id": resolver_cliente_orden(db, order)},
            f"Absorción MOD {order.folio} {minutos:g}min ({fuente})",
            "ABSORCION_MOD", order.id, fecha)
        db.commit()
        return {"monto": round(float(monto), 2), "minutos": minutos,
                "fuente": fuente, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def registrar_costo_ventas_bespoke(db: Session, order_id: int,
                                   usuario_id: int | None = None,
                                   fecha: date | None = None,
                                   monto: float | None = None) -> dict | None:
    """Asiento de costo de ventas al completar orden a medida (bespoke).

    Regla COSTO_VENTA_BESPOKE (6921x / 2311x directo del WIP).
    Idempotente por pedido (un asiento COSTO_VENTAS por orden).
    Sin `monto`: materiales Kardex + MOD (absorbida si existe, estimada si
    no). Con `monto`: usa el valor dado (el llamante absorbió primero para
    que WIP y costo cuadren exacto). Retorna None si el costo es 0.
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError("Pedido no encontrado")
    from app.models.finanzas import AsientoContable as _Asiento
    ya = db.query(_Asiento) \
        .filter(_Asiento.origen_tipo == "COSTO_VENTAS",
                _Asiento.origen_id == order.id).first()
    if ya:
        return {"asiento_id": ya.id, "asiento_numero": ya.numero,
                "monto": 0.0, "existente": True}
    if monto is None:
        if _existe_absorcion_mod(db, order.id):
            monto_d = _d(materiales_pedido(db, order.id)
                         + monto_mod_absorbido(db, order.id))
        else:
            monto_d = _d(costo_produccion_pedido(db, order.id))
    else:
        monto_d = _d(monto)
    if monto_d <= 0:
        return None
    try:
        from app.services.motor_contable import post_regla
        asiento = post_regla(
            db, "COSTO_VENTA_BESPOKE", [monto_d], [monto_d],
            {"cliente_id": resolver_cliente_orden(db, order)},
            f"Costo ventas {order.folio}", "COSTO_VENTAS", order.id, fecha)
        db.commit()
        return {"asiento_id": asiento.id, "asiento_numero": asiento.numero,
                "monto": float(monto_d), "haber": "2311x"}
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
    COBRADO_PARCIAL / COBRADO (=PAGADO). Si el cobro salda la orden y el
    taller está listo, la entrega automáticamente (no se estanca)."""
    from app.services import ventas as ventas_svc

    try:
        exigir_periodo_abierto(db, fecha)
        order = db.query(Order).with_for_update().filter(Order.id == order_id).first()
        if not order:
            raise ValueError("Pedido no encontrado")
        # Tope SUNAT: no cobrar boleta a Público General desde S/ 700.00.
        from app.services import peru as peru_svc
        peru_svc.exigir_cliente_sunat(
            order.total, bool(order.client_id or order.company_id))
        if exigir_turno and turno_id is None:
            t = ventas_svc.turno_abierto(db, usuario_id or 0)
            if not t:
                raise ValueError("TURNO_CERRADO")
            turno_id = t.id
        monto_d = min(_d(monto), _d(order.total) - _d(order.anticipo))
        if monto_d <= 0:
            raise ValueError("La orden no tiene saldo pendiente")
        from app.services import medios_pago as _mp
        metodo_n = _mp.normalizar_medio(metodo, default="EFECTIVO")
        # La cuenta la decide el medio (mapeo canónico); cuenta_codigo es
        # legado y se ignora para no contradecir el medio elegido.
        cuenta_codigo = _mp.cuenta_por_medio(metodo_n)
        id_cuenta = _mp.id_cuenta(db, cuenta_codigo)
        pago = Payment(order_id=order.id, monto=float(monto_d), metodo=metodo_n,
                       cuenta_contable_id=id_cuenta, usuario_id=usuario_id)
        db.add(pago)
        db.flush()
        order.anticipo = float(_d(order.anticipo) + monto_d)
        db.add(CashMovement(tipo="ingreso", concepto=f"Cobro {order.folio}",
                            monto=float(monto_d), metodo=metodo_n,
                            medio_pago=metodo_n,
                            cuenta_contable_id=id_cuenta,
                            order_id=order.id,
                            turno_id=turno_id, usuario_id=usuario_id))
        db.add(MovimientoFinanciero(tipo="INGRESO", categoria="Venta de Trajes",
                                    monto=float(monto_d),
                                    cuenta_origen="Caja" if cuenta_codigo == "1011" else "Banco",
                                    medio_pago=metodo_n,
                                    cuenta_contable_id=id_cuenta,
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
        # Asiento COBRO por regla (caja o banco según medio).
        from app.services.motor_contable import post_regla
        regla_cobro = ("COBRO_CAJA" if cuenta_codigo == "1011"
                       else "COBRO_BANCO")
        asiento = post_regla(
            db, regla_cobro, [monto_d], [monto_d],
            {"cliente_id": resolver_cliente_orden(db, order)},
            f"Cobro {order.folio} {metodo_n}", "COBRO", pago.id, fecha)
        db.commit()
        # Flujo de venta: si el cobro salda la orden Y el taller está listo,
        # se completa sola (no queda estancada en VENTA_CONFIRMADA). Nunca
        # bloquea el cobro: cualquier fallo se ignora silenciosamente.
        entregada = False
        try:
            _oid, _saldo = order.id, float(_d(order.total) - _d(order.anticipo))
            if _saldo <= 0.01:
                _ent = ventas_svc.entregar(db, _oid)
                entregada = _ent.estado == "entregado"
        except Exception:
            pass
        return {"payment_id": pago.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "cxc_id": cxc.id,
                "monto": float(monto_d), "saldo": float(_d(order.total) - _d(order.anticipo)),
                "entregada": entregada}
    except Exception:
        db.rollback()
        raise


# ── COMPRA manual (regla COMPRA_MP: 602x+40111 / 4212) + CxP ──────
def provisionar_compra(db: Session, proveedor_id: int, base: float, igv: float = 0.0,
                       numero_factura: str | None = None, fecha: date | None = None,
                       retencion: float = 0.0, fecha_vencimiento: date | None = None,
                       purchase_order_id: int | None = None,
                       orden_compra_id: int | None = None) -> dict:
    """Provisión de compra/gasto con factura: asiento + CxP amortizable."""
    from app.services.motor_contable import post_regla

    try:
        base_d, igv_d = _d(base), _d(igv)
        if base_d <= 0:
            raise ValueError("base debe ser positiva")
        if igv_d < 0:
            raise ValueError("IGV no puede ser negativo")
        if _d(retencion) < 0 or _d(retencion) > base_d + igv_d:
            raise ValueError("retención inválida")
        total = base_d + igv_d
        fecha = fecha or date.today()
        # CxP primero (origen MATERIA PRIMA, amortizable; sin flujo hasta el pago)
        from app.services.finanzas import ORIGEN_MAT_PRIMA
        cxp = CuentaPorPagar(proveedor_id=proveedor_id, purchase_order_id=purchase_order_id,
                             orden_compra_id=orden_compra_id,
                             origen_tipo=ORIGEN_MAT_PRIMA,
                             actividad_flujo="OPERATIVO",
                             numero_factura=(numero_factura or None),
                             monto_total=float(total), monto_pagado=0.0,
                             saldo_pendiente=float(total - _d(retencion)),
                             retencion=float(_d(retencion)), fecha_emision=fecha,
                             fecha_vencimiento=fecha_vencimiento or (
                                 fecha + timedelta(days=30)),
                             estado="POR_PAGAR")
        db.add(cxp)
        db.flush()
        from app.services.motor_contable import post_regla
        asiento = post_regla(
            db, "COMPRA_MP", [base_d, igv_d], [total],
            {"proveedor_id": proveedor_id},
            f"Compra {numero_factura or cxp.id}", "COMPRA", cxp.id, fecha)
        db.commit()
        return {"cxp_id": cxp.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "total": float(total)}
    except Exception:
        db.rollback()
        raise


# ── PAGO CxP (pasivo real + flujo por naturaleza) + caja ──────────
def _pasivo_para_cxp(db: Session, cxp) -> tuple[str, str | None]:
    """Pasivo real de la CxP y categoría del gasto espejo (si existe).

    Retorna (codigo_pasivo, categoria_gasto|None). La planilla (proveedor
    'Planilla / Personal' o comprobante PL-*) vive en 4111, no en 4212;
    el RxH/destajo en 4241; el resto en 4212 u otro pasivo provisionado.
    """
    # Gasto espejo por clave (comprobante + proveedor) o GASTO-{id}.
    gasto = None
    try:
        from app.models.finanzas import GastoRegistrado
        num = (cxp.numero_factura or "").strip()
        if num.startswith("GASTO-"):
            try:
                cand = db.get(GastoRegistrado, int(num.split("-", 1)[1]))
                if cand is not None and (not cxp.proveedor_id or not cand.proveedor_id or cand.proveedor_id == cxp.proveedor_id):
                    gasto = cand
            except Exception:
                gasto = None
        if gasto is None and num:
            q = db.query(GastoRegistrado).filter(
                GastoRegistrado.numero_comprobante == num)
            if cxp.proveedor_id:
                q = q.filter(GastoRegistrado.proveedor_id == cxp.proveedor_id)
            gasto = q.first()
    except Exception:
        gasto = None
    if gasto is not None:
        try:
            from app.services import tesoreria_service as _tes
            c_prov = _tes.cuenta_pasivo_gasto(db, gasto)
            if c_prov is not None and getattr(c_prov, "codigo", None):
                return c_prov.codigo, gasto.categoria
        except Exception:
            pass
        # Fallback por categoría si el asiento aún no existe.
        try:
            from app.services.finanzas import pasivo_por_categoria
            return pasivo_por_categoria(gasto.categoria), gasto.categoria
        except Exception:
            pass
    # Sin gasto espejo: marcadores de planilla (nunca 4212).
    try:
        num = (cxp.numero_factura or "").strip().upper()
        if num.startswith("PL-"):
            return "4111", "PLANILLA_PERSONAL"
        if cxp.proveedor_id:
            from app.models.purchasing import Supplier as _Supplier
            sup = db.get(_Supplier, cxp.proveedor_id)
            if sup is not None and (sup.nombre or "").strip() == "Planilla / Personal":
                return "4111", "PLANILLA_PERSONAL"
    except Exception:
        pass
    return "4212", None


def pagar_proveedor(db: Session, cxp_id: int, monto: float, cuenta_codigo: str = "1041",
                    usuario_id: int | None = None, fecha: date | None = None,
                    voucher: str | None = None,
                    permitir_sobregiro: bool = False) -> dict:
    """Abono a CxP: pasivo real + MovFin EGRESO + Cash + asiento PAGO, atómico.

    - El asiento debita el pasivo provisionado: 4111 planilla, 4241 RxH,
      4212 proveedores, etc. (nunca 4212 para planilla).
    - El EGRESO se etiqueta por naturaleza (planilla/alquiler/insumos).
    - Sobregiro estricto: 1011 bloquea sin saldo (ValueError → 400); 1041
      exige permitir_sobregiro=True.
    Estados: POR_PAGAR / PARCIAL / PAGADO. Si la CxP salda, marca PAGADO su
    gasto espejo (sincronía CxP→Gastos, sin commit extra)."""
    from app.services.motor_contable import post_manual, post_regla

    try:
        exigir_periodo_abierto(db, fecha)
        cxp = db.query(CuentaPorPagar).with_for_update().filter(
            CuentaPorPagar.id == cxp_id).first()
        if not cxp:
            raise ValueError("Cuenta por pagar no encontrada")
        if (cxp.estado or "") == "POR_FACTURAR":
            raise ValueError(
                "Deuda aún sin factura fiscal: exige el BILLED antes de pagar")
        monto_d = _d(monto)
        if monto_d <= 0 or monto_d - _d(cxp.saldo_pendiente) > Decimal("0.000001"):
            raise ValueError(f"Monto inválido (saldo: {cxp.saldo_pendiente})")
        cxp.monto_pagado = float(_d(cxp.monto_pagado) + monto_d)
        cxp.saldo_pendiente = float(max(
            _d(cxp.monto_total) - _d(cxp.monto_pagado) - _d(cxp.retencion), Decimal("0")))
        cxp.estado = "PAGADO" if _d(cxp.saldo_pendiente) <= Decimal("0.01") else "PARCIAL"
        from app.services import tesoreria_service as _tes
        _tes.marcar_gasto_pagado_si_saldado(db, cxp)
        ref = cxp.numero_factura or f"CxP-{cxp.id}"
        glosa_v = f" V:{voucher.strip()}" if (voucher or "").strip() else ""
        from app.services import medios_pago as _mp
        hoja = _mp.cuenta_por_medio(None, cuenta_codigo)
        medio = "EFECTIVO" if hoja == "1011" else "TRANSFERENCIA"
        id_cuenta = _mp.id_cuenta(db, hoja)
        from app.services import finanzas as _fin_chk
        # Advertencia pre-movimiento (el saldo post-flush ya incluye el
        # egreso y mostraría cifras inconsistentes).
        adv_sobregiro = _fin_chk.exigir_saldo_tesoreria(
            db, hoja, monto_d, permitir_sobregiro=bool(permitir_sobregiro))
        pasivo_cod, cat_gasto = _pasivo_para_cxp(db, cxp)
        from app.services import finanzas as _fin
        cat_flujo = _fin.categoria_flujo_pago(
            cat_gasto, getattr(cxp, "origen_tipo", None), ref)
        desc_flujo = _fin.descripcion_flujo_pago(
            cat_flujo, f"CxP {ref}", (voucher or "").strip() or None)
        if adv_sobregiro:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "sobregiro pago %s: %s", ref, adv_sobregiro)
        db.add(MovimientoFinanciero(tipo="EGRESO", categoria=cat_flujo,
                                    monto=float(monto_d),
                                    cuenta_origen="Banco" if hoja == "1041" else "Caja",
                                    medio_pago=medio,
                                    cuenta_contable_id=id_cuenta,
                                    comprobante_ref=ref, usuario_id=usuario_id,
                                    descripcion=desc_flujo))
        db.add(CashMovement(tipo="egreso", concepto=desc_flujo,
                            monto=float(monto_d), metodo=medio,
                            medio_pago=medio,
                            cuenta_contable_id=id_cuenta,
                            usuario_id=usuario_id))
        dims = {"proveedor_id": cxp.proveedor_id}
        if pasivo_cod == "4212":
            asiento = post_regla(
                db, "PAGO_CAJA" if hoja == "1011" else "PAGO_BANCO",
                [monto_d], [monto_d], dims,
                f"Pago {ref}{glosa_v}", "PAGO", cxp.id, fecha)
        else:
            asiento = post_manual(
                db, [(pasivo_cod, monto_d)], [(hoja, monto_d)], dims,
                f"Pago {ref}{glosa_v}", "PAGO", cxp.id, fecha)
        db.commit()
        return {"cxp_id": cxp.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero, "estado": cxp.estado,
                "saldo": cxp.saldo_pendiente, "cuenta_pasivo": pasivo_cod,
                "cuenta_caja": hoja, "categoria_flujo": cat_flujo,
                "advertencia_sobregiro": adv_sobregiro}
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
                            clasificacion: str | None = None,
                            glosa: str | None = None,
                            retencion: float | Decimal = 0,
                            fecha_vencimiento: date | None = None,
                            actividad_flujo: str | None = None) -> tuple[GastoRegistrado, object]:
    """Provisión de gasto con bloqueo de período, en UNA transacción."""
    from app.services import finanzas as fin

    try:
        exigir_periodo_abierto(db, fecha)
        gasto, asiento = fin.registrar_gasto_operativo(
            db, fecha=fecha, categoria=categoria, monto_base=monto_base,
            monto_igv=monto_igv, cuenta_codigo=cuenta_codigo, proveedor_id=proveedor_id,
            ruc_proveedor=ruc_proveedor, tipo_comprobante=tipo_comprobante,
            numero_comprobante=numero_comprobante, centro_costo_id=centro_costo_id,
            variabilidad=variabilidad, clasificacion=clasificacion, glosa=glosa,
            retencion=retencion, fecha_vencimiento=fecha_vencimiento,
            actividad_flujo=actividad_flujo)
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
                        fecha: date | None = None,
                        medio_pago: str | None = None,
                        voucher: str | None = None,
                        monto: float | Decimal | None = None,
                        permitir_sobregiro: bool = False) -> dict:
    """Pago de gasto vía Tesorería centralizada (gasto + CxP + caja + diario).

    Mantiene el contrato anterior (gasto_id/asiento_id/nuevos_asientos).
    Delega en `tesoreria_service.ejecutar_pago_proveedor` (sobregiro
    estricto: 1011 bloquea, 1041 exige permitir_sobregiro).
    """
    from app.models.finanzas import AsientoContable as _Asiento
    from app.services import tesoreria_service as _tes

    try:
        exigir_periodo_abierto(db, fecha)
        asientos_antes = {a.id for a in db.query(_Asiento).filter(
            _Asiento.origen_tipo == "PAGO",
            _Asiento.origen_id == gasto_id).all()}
    except Exception:
        asientos_antes = set()
    medio = medio_pago or ("caja" if cuenta_origen_codigo == "101" else "banco")
    res = _tes.ejecutar_pago_proveedor(
        db, gasto_id, medio_pago=medio, cuenta_origen_id=cuenta_origen_codigo,
        monto=monto, usuario_id=usuario_id, voucher=voucher, fecha=fecha,
        permitir_sobregiro=permitir_sobregiro)
    return {"gasto_id": res["gasto_id"], "asiento_id": res["asiento_id"],
            "nuevos_asientos": sorted(asientos_antes | {res["asiento_id"]})}


# ── Sync gastos PENDIENTES → CxP (bandeja única de tesorería) ──────
def sincronizar_cxp_desde_gastos(db: Session, commit: bool = True) -> int:
    """Espeja cada gasto PENDIENTE en una CxP POR_PAGAR (idempotente).

    La CxP es solo el espejo de tesorería: NO genera asiento (el gasto ya
    provisionó su pasivo 4212/4111/424/4699/4654) y NUNCA genera
    MovimientoFinanciero/Caja (el egreso nace solo al pagar).
    Clave idempotente: numero_comprobante (o GASTO-{id}) + proveedor.
    Retorna espejos creados.
    """
    from datetime import timedelta as _td
    from app.services.finanzas import origen_por_categoria_gasto
    creados = 0
    gastos = db.query(GastoRegistrado).filter(
        GastoRegistrado.estado == "PENDIENTE").all()
    for g in gastos:
        try:
            total = float(g.monto_total or 0)
            if total <= 0:
                continue
            pid = g.proveedor_id
            if not pid and (g.ruc_proveedor or "").strip():
                # Mapea persona por DNI/RUC: reutiliza o crea el proveedor.
                from app.models.purchasing import Supplier as _Supplier
                ruc = g.ruc_proveedor.strip()
                sup = db.query(_Supplier).filter(
                    _Supplier.ruc == ruc).first()
                if not sup:
                    sup = _Supplier(
                        nombre=((g.glosa or "").strip() or f"Proveedor {ruc}")[:160],
                        ruc=ruc)
                    db.add(sup)
                    db.flush()
                g.proveedor_id = sup.id
                pid = sup.id
            if not pid:
                continue  # sin contraparte no hay espejo en tesorería
            clave = (g.numero_comprobante or "").strip() or f"GASTO-{g.id}"
            ya = db.query(CuentaPorPagar).filter(
                CuentaPorPagar.numero_factura == clave,
                CuentaPorPagar.proveedor_id == pid).first()
            if ya:
                # Normaliza espejos legacy al estándar de 4 orígenes.
                try:
                    from app.services.finanzas import ORIGEN_LEGACY_MAP
                    if (ya.origen_tipo or "") in ORIGEN_LEGACY_MAP:
                        ya.origen_tipo = ORIGEN_LEGACY_MAP[ya.origen_tipo]
                except Exception:
                    pass
                continue
            ret = float(getattr(g, "retencion", 0) or 0)
            es_activo = (g.categoria or "").upper() == "ACTIVO_FIJO"
            fv = g.fecha_vencimiento or (
                g.fecha_emision + _td(days=30) if g.fecha_emision else None)
            db.add(CuentaPorPagar(
                proveedor_id=pid, numero_factura=clave,
                origen_tipo=origen_por_categoria_gasto(g.categoria),
                actividad_flujo=(getattr(g, "actividad_flujo", None) or ("INVERSION" if es_activo else "OPERATIVO")),
                tipo_comprobante=(g.tipo_comprobante or "FACTURA"),
                monto_total=total, monto_pagado=0.0,
                saldo_pendiente=round(max(total - ret, 0.0), 2),
                retencion=ret, fecha_emision=g.fecha_emision,
                fecha_vencimiento=fv,
                estado="POR_PAGAR"))
            creados += 1
        except Exception:
            continue
    if commit:
        db.commit()
    else:
        db.flush()
    return creados


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
