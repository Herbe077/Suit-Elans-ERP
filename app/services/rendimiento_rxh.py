"""Provisión de Recibos por Honorarios (RxH) a demanda desde Rendimiento.

Flujo simplificado (2 estados): el destajo ingresa PENDIENTE y la calculadora
lo suma en tiempo real por operario. El botón único `Generar Provisión RxH /
CxP` crea por el acumulado:

  1. Gasto HONORARIOS_RXH (RECIBO_HONORARIOS, sin IGV, retención 8% opcional)
     con asiento DEBE 6322 / HABER 40172 (retención) + HABER 4241 (neto)
     y destino 921/791 (costo de taller, nunca 941).
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

RETENCION_IR_4TA = Decimal("0.08")  # fallback si no hay parámetro
UMBRAL_RETENCION_AUTOMATICA = Decimal("1500")


def _retencion_pct(db) -> Decimal:
    try:
        from app.services import config as _cfg
        return Decimal(str(_cfg.parametros_laborales(db)["retencion_4ta_pct"])) / Decimal("100")
    except Exception:
        return RETENCION_IR_4TA
# Flujo nuevo: solo PENDIENTE. Legacy (creados antes de la simplificación)
# también son provisionables una vez.
ESTADOS_PROVISIONABLES = ("PENDIENTE", "REGISTRADO", "APROBADO",
                          "LIQUIDADO_INTERNO")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def resolver_proveedor_sastre(db: Session, operario_id: int,
                              ruc_dni: str | None = None,
                              nombre_preferido: str | None = None):
    """Mapea operario → Supplier por DNI/RUC (o nombre); lo crea si falta."""
    from app.models.purchasing import Supplier
    from app.models.user import User

    ruc_dni = (ruc_dni or "").strip() or None
    if ruc_dni:
        sup = db.query(Supplier).filter(Supplier.ruc == ruc_dni).first()
        if sup:
            return sup
    user = db.get(User, operario_id)
    nombre = (nombre_preferido or "").strip() or (
        user.full_name if user and user.full_name else f"Sastre {operario_id}").strip()
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


def buscar_empleado(db: Session, ruc_dni: str | None):
    """Perfil de Personal y Contratos por DNI/RUC (o None si no existe)."""
    from app.models.personnel import Empleado
    doc = (ruc_dni or "").strip()
    if not doc:
        return None
    return db.query(Empleado).filter(
        (Empleado.dni == doc) | (Empleado.ruc == doc)).first()


def generar_provision_rxh(db: Session, operario_id: int,
                          numero_comprobante: str,
                          desde: date | None = None,
                          hasta: date | None = None,
                          ruc_dni: str | None = None,
                          con_retencion: bool = False,
                          usuario_id: int | None = None) -> dict:
    """Genera Gasto RxH (6322/4241) + CxP POR_PAGAR y marca PROVISIONADO."""
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.motor_contable import (
        dim_centro,
        post_regla,
        post_regla_extra,
    )

    numero = (numero_comprobante or "").strip()
    if not numero:
        raise ValueError("numero_comprobante del RxH es obligatorio")
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
    emp = buscar_empleado(db, ruc_dni)
    # Retención 8%: check explícito, o bandera del perfil, o suma > S/ 1500.
    aplica_ret = bool(con_retencion) or bool(emp and emp.aplica_retencion_8) \
        or total > UMBRAL_RETENCION_AUTOMATICA
    sup = resolver_proveedor_sastre(
        db, operario_id, ruc_dni,
        nombre_preferido=emp.nombre_completo if emp else None)
    ret = (total * _retencion_pct(db)).quantize(Decimal("0.01")) if aplica_ret else Decimal("0")
    fecha = date.today()
    try:
        neto = total - ret
        # Naturaleza por regla GASTO_RXH (6322/4241 + 40172 si retiene).
        gasto = GastoRegistrado(
            proveedor_id=sup.id, ruc_proveedor=sup.ruc,
            tipo_comprobante="RECIBO_HONORARIOS",
            numero_comprobante=numero, categoria="HONORARIOS_RXH",
            glosa=f"RxH destajo {sup.nombre} {numero}",
            monto_base=float(total), monto_igv=0.0, monto_total=float(total),
            retencion=float(ret), clasificacion="GASTO_ADMINISTRATIVO",
            variabilidad="VARIABLE",
            centro_costo_id=dim_centro(db, "921"),
            cuenta_id=None,
            fecha_emision=fecha, estado="PENDIENTE")
        db.add(gasto)
        db.flush()
        dims_rxh = {"proveedor_id": sup.id,
                    "centro_costo_id": gasto.centro_costo_id}
        if ret > 0:
            asiento = post_regla_extra(
                db, "GASTO_RXH", [total, Decimal("0")], [neto],
                extra_haber=[("40172", ret)], dims=dims_rxh,
                glosa=f"RxH destajo {numero}", origen_tipo="HONORARIOS",
                origen_id=gasto.id, fecha=fecha)
        else:
            asiento = post_regla(
                db, "GASTO_RXH", [total, Decimal("0")], [neto],
                dims=dims_rxh, glosa=f"RxH destajo {numero}",
                origen_tipo="HONORARIOS", origen_id=gasto.id, fecha=fecha)
        from app.services.motor_contable import expandir_patron
        gasto.cuenta_id = expandir_patron(db, "6322x").id
        # Destajo de taller → DESTINO_921 (MOD de confección),
        # nunca 941: la naturaleza es honorarios de taller, no gasto
        # administrativo ni planilla, sin importar la ficha del sastre.
        post_regla(
            db, "DESTINO_921", [total], [total], dims_rxh,
            f"Destino DESTINO_921 RxH {numero}", "HONORARIOS", gasto.id,
            fecha)
        from datetime import timedelta as _td
        from app.services.finanzas import ORIGEN_SERVICIOS
        cxp = CuentaPorPagar(
            proveedor_id=sup.id, numero_factura=numero,
            origen_tipo=ORIGEN_SERVICIOS, actividad_flujo="OPERATIVO",
            tipo_comprobante="RECIBO_HONORARIOS",
            monto_total=float(total), monto_pagado=0.0,
            saldo_pendiente=float(total - ret), retencion=float(ret),
            fecha_emision=fecha, fecha_vencimiento=fecha + _td(days=30),
            estado="POR_PAGAR")
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
            "registros": len(regs), "proveedor_id": sup.id,
            "empleado_id": emp.id if emp else None}
