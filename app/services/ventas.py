"""Front-office: turnos de caja, cobros con automatizaciones y entrega.

Mapeo al modelo existente (sin duplicar tablas):
- OrdenVenta -> Order (cotizado=COTIZACION, confirmado=VENTA_CONFIRMADA,
  entregado=COMPLETADA, cancelado=CANCELADA).
- PagoOrden -> Payment + CashMovement (con turno_id).
- ComprobanteVenta -> Invoice.
- OrdenTrabajo -> Garment (unidad de producción del taller).
"""
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.constants import CONSUMO_TELA_M
from app.models.billing import CajaTurno, CashMovement
from app.models.inventory import Fabric
from app.models.order import Garment, Order, Payment
from app.services import config as config_svc
from app.services.medios_pago import MEDIOS_PAGO, normalizar_medio

# Compat: el enum canónico vive en medios_pago (YAPE/PLIN → YAPE_PLIN).
METODOS_PAGO = MEDIOS_PAGO
METODOS_MAP = {"efectivo": "EFECTIVO", "yape": "YAPE_PLIN",
               "plin": "YAPE_PLIN", "tarjeta": "TARJETA",
               "transferencia": "TRANSFERENCIA"}


def anticipo_min_pct(db: Session) -> float:
    try:
        return float(config_svc.get(db, "anticipo_min_pct", "50"))
    except ValueError:
        return 50.0


def turno_abierto(db: Session, usuario_id: int) -> CajaTurno | None:
    return db.query(CajaTurno).filter(CajaTurno.usuario_id == usuario_id,
                                      CajaTurno.estado == "ABIERTA").first()


def abrir_turno(db: Session, usuario_id: int, saldo_apertura: float) -> CajaTurno:
    if turno_abierto(db, usuario_id):
        raise ValueError("Ya tienes un turno abierto")
    if saldo_apertura < 0:
        raise ValueError("El saldo de apertura no puede ser negativo")
    t = CajaTurno(usuario_id=usuario_id, saldo_apertura=round(saldo_apertura, 2))
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def teorico_turno(db: Session, turno_id: int) -> float:
    t = db.get(CajaTurno, turno_id)
    ing = db.query(func.coalesce(func.sum(CashMovement.monto), 0)).filter(
        CashMovement.turno_id == turno_id, CashMovement.tipo == "ingreso").scalar() or 0
    egr = db.query(func.coalesce(func.sum(CashMovement.monto), 0)).filter(
        CashMovement.turno_id == turno_id, CashMovement.tipo == "egreso").scalar() or 0
    return round((t.saldo_apertura if t else 0) + ing - egr, 2)


def cerrar_turno(db: Session, turno_id: int, saldo_real: float,
                 usuario_id: int) -> CajaTurno:
    t = db.get(CajaTurno, turno_id)
    if not t or t.estado != "ABIERTA":
        raise ValueError("Turno no abierto")
    if t.usuario_id != usuario_id:
        raise ValueError("Solo quien abrió el turno puede cerrarlo")
    if saldo_real < 0:
        raise ValueError("El saldo real no puede ser negativo")
    t.saldo_cierre_teorico = teorico_turno(db, turno_id)
    t.saldo_cierre_real = round(saldo_real, 2)
    t.fecha_cierre = datetime.now()
    t.estado = "CERRADA"
    db.commit()
    db.refresh(t)
    return t


def _reservar_telas(db: Session, order: Order) -> None:
    """Reserva metraje de las prendas con tela aún no reservada.
    Legacy: descuenta stock_metros. Spec: incrementa ProductoInsumo stock_reservado."""
    for g in db.query(Garment).filter(Garment.order_id == order.id).all():
        if g.tela_id and not g.tela_reservada:
            tela = db.get(Fabric, g.tela_id)
            consumo = CONSUMO_TELA_M.get(g.tipo, 1.5)
            if tela and tela.stock_metros >= consumo:
                tela.stock_metros = round(tela.stock_metros - consumo, 2)
                g.tela_reservada = True
                # spec mirror
                try:
                    from app.services.inventory import ensure_producto_for_fabric
                    from app.models.inventario import MovimientoKardex
                    prod = ensure_producto_for_fabric(db, tela)
                    # reserva no toca fisico spec, solo reservado
                    if prod.stock_disponible >= consumo - 1e-9:
                        prod.stock_reservado = round((prod.stock_reservado or 0) + consumo, 2)
                        db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
                                                cantidad=consumo, orden_venta_id=order.id, observacion="Reserva venta"))
                except Exception:
                    pass
    db.commit()


def confirmar_si_corresponde(db: Session, order: Order) -> bool:
    """Al alcanzar el anticipo mínimo: confirma la venta y reserva telas.
    Retorna True si confirmó en esta llamada."""
    if order.estado != "cotizado" or not order.total:
        return False
    pct = order.anticipo / order.total * 100
    if pct + 1e-9 >= anticipo_min_pct(db):
        order.estado = "confirmado"  # = VENTA_CONFIRMADA
        db.commit()
        _reservar_telas(db, order)
        return True
    return False


def registrar_cobro(db: Session, order_id: int, monto: float, metodo: str,
                    tipo: str, usuario_id: int) -> tuple[Order, Payment]:
    """Cobra anticipo/saldo exigiendo turno abierto; dispara automatizaciones.

    Motor atómico (ContabilidadService): Payment + anticipo + caja/flujo +
    CxC + asiento COBRO (1011/1041 vs 1212) en UNA transacción; luego la
    confirmación a taller (reserva de telas) como paso posterior.
    """
    from app.services import contabilidad as contab

    order = db.get(Order, order_id)
    if not order:
        raise ValueError("Pedido no encontrado")
    try:
        metodo_norm = normalizar_medio(metodo, default="EFECTIVO")
    except ValueError:
        raise ValueError(f"Método inválido: {metodo}")
    if tipo not in ("ADELANTO", "SALDO_FINAL"):
        tipo = "ADELANTO" if (order.anticipo or 0) <= 0 else "SALDO_FINAL"
    res = contab.cobrar_venta(db, order_id, float(monto), metodo_norm,
                              cuenta_codigo="1011", usuario_id=usuario_id,
                              exigir_turno=True)
    order = db.get(Order, order_id)
    pago = db.get(Payment, res["payment_id"])
    confirmar_si_corresponde(db, order)
    db.refresh(pago)
    return order, pago


ESTADO_OV_MAP = {"cotizado": "COTIZACION", "confirmado": "VENTA_CONFIRMADA",
                 "entregado": "COMPLETADA", "cancelado": "CANCELADA"}


def sincronizar_espejo_ventas(db: Session) -> dict:
    """Espeja Order->OrdenVenta, Payment->PagoOrden, Invoice->ComprobanteVenta (idempotente)."""
    from app.models.ventas import ComprobanteVenta, OrdenVenta, PagoOrden
    from app.models.billing import Invoice
    n = {"ov": 0, "po": 0, "cv": 0}
    for o in db.query(Order).all():
        ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == o.id).first()
        if not ov and o.folio:
            ov = db.query(OrdenVenta).filter(OrdenVenta.folio == o.folio).first()
        if not ov:
            ov = OrdenVenta(folio=o.folio, cliente_id=o.client_id, company_id=o.company_id,
                            total=o.total or 0, monto_adelantado=o.anticipo or 0,
                            saldo_pendiente=round((o.total or 0) - (o.anticipo or 0), 2),
                            estado=ESTADO_OV_MAP.get((o.estado or "").lower(), "COTIZACION"),
                            legacy_order_id=o.id, sastre_id=o.sastre_id)
            db.add(ov); n["ov"] += 1
        else:
            if ov.legacy_order_id is None:
                ov.legacy_order_id = o.id
            ov.total = o.total or 0; ov.monto_adelantado = o.anticipo or 0
            ov.saldo_pendiente = round((o.total or 0) - (o.anticipo or 0), 2)
            ov.estado = ESTADO_OV_MAP.get((o.estado or "").lower(), ov.estado)
    try:
        db.flush()
    except Exception:
        db.rollback()
        # reintenta lectura si hubo colisión con espejos concurrentes
        pass
    for p in db.query(Payment).all():
        if not db.query(PagoOrden).filter(PagoOrden.legacy_payment_id == p.id).first():
            o = db.get(Order, p.order_id) if p.order_id else None
            ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == p.order_id).first() if p.order_id else None
            if not ov and o and o.folio:
                ov = db.query(OrdenVenta).filter(OrdenVenta.folio == o.folio).first()
            if not ov and o:
                ov = OrdenVenta(folio=o.folio, cliente_id=o.client_id, company_id=o.company_id,
                                total=o.total or 0, monto_adelantado=o.anticipo or 0,
                                saldo_pendiente=round((o.total or 0) - (o.anticipo or 0), 2),
                                estado=ESTADO_OV_MAP.get((o.estado or "").lower(), "COTIZACION"),
                                legacy_order_id=o.id, sastre_id=o.sastre_id)
                db.add(ov); db.flush(); n["ov"] += 1
            if not ov:
                continue  # pago huérfano sin orden: no se espeja
            db.add(PagoOrden(orden_venta_id=ov.id,
                             legacy_payment_id=p.id, legacy_order_id=p.order_id,
                             monto=p.monto, metodo_pago=(p.metodo or "EFECTIVO").upper()))
            n["po"] += 1
    try:
        db.flush()
    except Exception:
        db.rollback()
        pass
    for inv in db.query(Invoice).all():
        if not db.query(ComprobanteVenta).filter(ComprobanteVenta.legacy_invoice_id == inv.id).first():
            ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == inv.order_id).first() if inv.order_id else None
            db.add(ComprobanteVenta(orden_venta_id=ov.id if ov else None, legacy_invoice_id=inv.id,
                                    legacy_order_id=inv.order_id, tipo="FACTURA" if (inv.serie or "").startswith("F") else "BOLETA",
                                    serie=inv.serie, numero=inv.numero))
            n["cv"] += 1
    try:
        db.commit()
    except Exception:
        db.rollback()
    return n


def puede_entregar(db: Session, order: Order) -> tuple[bool, str]:
    if order.estado == "entregado":
        return False, "El pedido ya fue entregado"
    if round(order.total - order.anticipo, 2) > 0:
        return False, f"Bloqueado: saldo pendiente S/ {order.total - order.anticipo:.2f}"
    # Validación taller: todas las prendas deben estar CALIDAD_OK (listo para entregar)
    garments = db.query(Garment).filter(Garment.order_id == order.id).all()
    if garments:
        for g in garments:
            if g.estado_taller not in ("CALIDAD_OK", "entregado"):
                return False, f"Bloqueado: prenda {g.tipo} #{g.id} no está lista (estado {g.estado_taller}, requiere CALIDAD_OK)"
    return True, ""


def entregar(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise ValueError("Pedido no encontrado")
    ok, msg = puede_entregar(db, order)
    if not ok:
        raise ValueError(msg)
    order.estado = "entregado"  # = COMPLETADA
    # Propaga a producción: archiva las prendas del pedido (sacos listos desaparecen del Kanban)
    for g in db.query(Garment).filter(Garment.order_id == order.id).all():
        g.estado_taller = "entregado"
    db.commit()
    db.refresh(order)
    # Costo de ventas automático (69 vs 23) al completar + SALIDA física
    # Cta 23 (SALIDA_VENTA). Idempotente por pedido (si ya se generó al
    # facturar, no se duplica). No bloquea la entrega si no hay costo o
    # el período está cerrado.
    try:
        from app.services import contabilidad as contab
        contab._salida_venta_cta23_idempotente(db, order.id, None)
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "costo ventas %s omitido", getattr(order, "folio", order_id),
            exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        order = db.get(Order, order_id)
    db.refresh(order)
    return order
