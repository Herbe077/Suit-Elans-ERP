"""Provisión de Recibos por Honorarios (RxH) a demanda desde Rendimiento.

Flujo: los registros de destajo se aprueban/liquidan de forma puramente
operativa (LIQUIDADO_INTERNO, sin efecto financiero). Cuando el usuario lo
decide, esta provisión genera por el acumulado del operario:

  1. Gasto HONORARIOS_RXH (RECIBO_HONORARIOS, sin IGV, retención 8% opcional)
     con su asiento DEBE 6322 / HABER 424.
  2. CxP POR_PAGAR a nombre del sastre (proveedor por DNI/RUC) con la
     retención informada (saldo = total − retención).

NUNCA genera MovimientoFinanciero/Caja: el egreso nace solo al PAGAR en CxP.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finanzas import CuentaPorPagar, GastoRegistrado

RETENCION_IR_4TA = Decimal("0.08")
ESTADOS_PROVISIONABLES = ("APROBADO", "LIQUIDADO_INTERNO")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def resolver_proveedor_sastre(db: Session, operario_id: int,
                              ruc_dni: str | None = None):
    """Mapea operario → Supplier por DNI/RUC (o nombre); lo crea si falta."""
    from app.models.purchasing import Supplier
    from app.models.user import User

    ruc_dni = (ruc_dni or "").strip() or None
    if ruc_dni:
        sup = db.query(Supplier).filter(Supplier.ruc == ruc_dni).first()
        if sup:
            return sup
    user = db.get(User, operario_id)
    nombre = (user.full_name if user and user.full_name else f"Sastre {operario_id}").strip()
    sup = db.query(Supplier).filter(Supplier.nombre == nombre).first()
    if sup:
        return sup
    sup = Supplier(nombre=nombre[:160], ruc=ruc_dni)
    db.add(sup)
    db.flush()
    return sup


def acumulado_operario(db: Session, operario_id: int,
                       desde: date | None = None,
                       hasta: date | None = None) -> tuple[Decimal, list]:
    """Total destajo del operario en el rango (registros provisionables)."""
    from app.modules.rendimiento.models import DetalleJornada, RegistroJornada

    q = db.query(RegistroJornada).filter(
        RegistroJornada.operario_id == operario_id,
        RegistroJornada.estado.in_(ESTADOS_PROVISIONABLES))
    if desde:
        q = q.filter(RegistroJornada.fecha >= desde)
    if hasta:
        q = q.filter(RegistroJornada.fecha <= hasta)
    regs = q.all()
    if not regs:
        return Decimal("0"), []
    total = Decimal("0")
    for d in db.query(DetalleJornada).filter(
            DetalleJornada.registro_jornada_id.in_([r.id for r in regs])).all():
        total += _d(d.subtotal)
    return total, regs


def generar_provision_rxh(db: Session, operario_id: int,
                          numero_comprobante: str,
                          desde: date | None = None,
                          hasta: date | None = None,
                          ruc_dni: str | None = None,
                          con_retencion: bool = False,
                          usuario_id: int | None = None) -> dict:
    """Genera Gasto RxH + CxP por el acumulado del operario (sin flujo)."""
    from app.services import contabilidad as contab
    from app.services import finanzas as fin

    numero = (numero_comprobante or "").strip()
    if not numero:
        raise ValueError("numero_comprobante del RxH es obligatorio")
    total, regs = acumulado_operario(db, operario_id, desde, hasta)
    if total <= 0:
        raise ValueError("Sin destajo provisionable para el operario en el rango")
    if db.query(CuentaPorPagar).filter(
            CuentaPorPagar.numero_factura == numero).first():
        raise ValueError(f"RxH {numero} ya provisionado")
    if db.query(GastoRegistrado).filter(
            GastoRegistrado.numero_comprobante == numero).first():
        raise ValueError(f"RxH {numero} ya registrado como gasto")
    fin.seed_pcge_basico(db)
    sup = resolver_proveedor_sastre(db, operario_id, ruc_dni)
    ret = (total * RETENCION_IR_4TA).quantize(Decimal("0.01")) if con_retencion else Decimal("0")
    fecha = date.today()
    gasto, asiento = contab.registrar_gasto_atomico(
        db, fecha=fecha, categoria="HONORARIOS_RXH",
        monto_base=total, monto_igv=Decimal("0"),
        proveedor_id=sup.id, ruc_proveedor=sup.ruc,
        tipo_comprobante="RECIBO_HONORARIOS",
        numero_comprobante=numero,
        glosa=f"RxH destajo {sup.nombre} {numero}",
        retencion=ret)
    cxp = CuentaPorPagar(
        proveedor_id=sup.id, numero_factura=numero,
        monto_total=float(total), monto_pagado=0.0,
        saldo_pendiente=float(total - ret), retencion=float(ret),
        fecha_emision=fecha, estado="POR_PAGAR")
    db.add(cxp)
    db.flush()
    for r in regs:
        r.estado = "LIQUIDADO"
    db.commit()
    return {"cxp_id": cxp.id, "gasto_id": gasto.id,
            "asiento_id": asiento.id, "total": float(total),
            "retencion": float(ret), "neto": float(total - ret),
            "registros": len(regs), "proveedor_id": sup.id}
