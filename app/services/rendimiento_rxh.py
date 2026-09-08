"""Provisión de Recibos por Honorarios (RxH) a demanda desde Rendimiento.

Flujo simplificado (2 estados): el destajo ingresa PENDIENTE y la calculadora
lo suma en tiempo real por operario. El botón único `Generar Provisión RxH /
CxP` crea por el acumulado:

  1. Gasto HONORARIOS_RXH (RECIBO_HONORARIOS, sin IGV, retención 8% opcional)
     con asiento DEBE 6322 / HABER 4241 (honorarios por pagar).
  2. CxP POR_PAGAR con tipo_comprobante RECIBO_HONORARIOS a nombre del
     sastre (proveedor por DNI/RUC), saldo = total − retención.
  3. Los registros pasan de PENDIENTE a PROVISIONADO (no se recontabilizan).

REGLA DE ORO: nunca genera MovimientoFinanciero/Caja; el egreso nace solo
al PAGAR en la bandeja de CxP.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finanzas import CuentaPorPagar, GastoRegistrado

RETENCION_IR_4TA = Decimal("0.08")
# Flujo nuevo: solo PENDIENTE. Legacy (creados antes de la simplificación)
# también son provisionables una vez.
ESTADOS_PROVISIONABLES = ("PENDIENTE", "REGISTRADO", "APROBADO",
                          "LIQUIDADO_INTERNO")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def ensure_cuenta_4241(db: Session):
    """Asegura la analítica 4241 (honorarios RxH por pagar) bajo la 424."""
    from app.services.finanzas import get_or_create_cuenta, seed_pcge_basico
    seed_pcge_basico(db)
    return get_or_create_cuenta(
        db, "4241", "Honorarios por pagar - RxH destajo", "PASIVO",
        nivel=3, padre_codigo="424", elemento=4, es_analitica=True)


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
    """Total destajo PENDIENTE del operario en el rango (tiempo real)."""
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
    """Genera Gasto RxH (6322/4241) + CxP POR_PAGAR y marca PROVISIONADO."""
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.finanzas import (CLASIF_DESTINO_MAP,
                                       crear_asiento_flush,
                                       ensure_cxp_tipo_comprobante_column,
                                       get_cuenta_by_codigo, seed_pcge_basico)

    numero = (numero_comprobante or "").strip()
    if not numero:
        raise ValueError("numero_comprobante del RxH es obligatorio")
    seed_pcge_basico(db)
    ensure_cxp_tipo_comprobante_column(db)
    total, regs = acumulado_operario(db, operario_id, desde, hasta)
    if total <= 0:
        raise ValueError("Sin destajo PENDIENTE para el operario en el rango")
    if db.query(CuentaPorPagar).filter(
            CuentaPorPagar.numero_factura == numero).first():
        raise ValueError(f"RxH {numero} ya provisionado")
    if db.query(GastoRegistrado).filter(
            GastoRegistrado.numero_comprobante == numero).first():
        raise ValueError(f"RxH {numero} ya registrado como gasto")
    exigir_periodo_abierto(db, date.today())
    sup = resolver_proveedor_sastre(db, operario_id, ruc_dni)
    ret = (total * RETENCION_IR_4TA).quantize(Decimal("0.01")) if con_retencion else Decimal("0")
    fecha = date.today()
    try:
        c_gasto = get_cuenta_by_codigo(db, "6322")
        c_pasivo = ensure_cuenta_4241(db)
        if not c_gasto or not c_pasivo:
            raise ValueError("Faltan cuentas 6322/4241 (seed PCGE)")
        gasto = GastoRegistrado(
            proveedor_id=sup.id, ruc_proveedor=sup.ruc,
            tipo_comprobante="RECIBO_HONORARIOS",
            numero_comprobante=numero, categoria="HONORARIOS_RXH",
            glosa=f"RxH destajo {sup.nombre} {numero}",
            monto_base=float(total), monto_igv=0.0, monto_total=float(total),
            retencion=float(ret), clasificacion="GASTO_ADMINISTRATIVO",
            variabilidad="VARIABLE", cuenta_id=c_gasto.id,
            fecha_emision=fecha, estado="PENDIENTE")
        db.add(gasto)
        db.flush()
        asiento = crear_asiento_flush(
            db, fecha, f"RxH destajo {numero}", "HONORARIOS", gasto.id,
            [{"cuenta_id": c_gasto.id, "debe": total, "haber": Decimal("0")},
             {"cuenta_id": c_pasivo.id, "debe": Decimal("0"), "haber": total}])
        # Destino analítico 941/791 (gasto administrativo, sin centro).
        c_dest = get_cuenta_by_codigo(db, CLASIF_DESTINO_MAP["GASTO_ADMINISTRATIVO"])
        c_79 = get_cuenta_by_codigo(db, "791")
        if c_dest and c_79:
            crear_asiento_flush(
                db, fecha, f"Destino 941 RxH {numero}", "HONORARIOS", gasto.id,
                [{"cuenta_id": c_dest.id, "debe": total, "haber": Decimal("0")},
                 {"cuenta_id": c_79.id, "debe": Decimal("0"), "haber": total}])
        cxp = CuentaPorPagar(
            proveedor_id=sup.id, numero_factura=numero,
            tipo_comprobante="RECIBO_HONORARIOS",
            monto_total=float(total), monto_pagado=0.0,
            saldo_pendiente=float(total - ret), retencion=float(ret),
            fecha_emision=fecha, estado="POR_PAGAR")
        db.add(cxp)
        db.flush()
        for r in regs:
            r.estado = "PROVISIONADO"
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"cxp_id": cxp.id, "gasto_id": gasto.id,
            "asiento_id": asiento.id, "total": float(total),
            "retencion": float(ret), "neto": float(total - ret),
            "registros": len(regs), "proveedor_id": sup.id}
