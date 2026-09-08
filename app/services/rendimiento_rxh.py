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

RETENCION_IR_4TA = Decimal("0.08")
UMBRAL_RETENCION_AUTOMATICA = Decimal("1500")
# Flujo nuevo: solo PENDIENTE. Legacy (creados antes de la simplificación)
# también son provisionables una vez.
ESTADOS_PROVISIONABLES = ("PENDIENTE", "REGISTRADO", "APROBADO",
                          "LIQUIDADO_INTERNO")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def ensure_cuenta_4241(db: Session):
    """Atajo local (canónico en finanzas): analítica 4241 bajo la 424."""
    from app.services.finanzas import ensure_cuenta_4241 as _ensure
    return _ensure(db)


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
    from app.models.personnel import Empleado, ensure_empleados_table
    ensure_empleados_table(db)
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
    from app.services.finanzas import (crear_asiento_flush,
                                       ensure_cuenta_40172,
                                       ensure_cxp_origen_tipo_column,
                                       ensure_cxp_tipo_comprobante_column,
                                       get_cuenta_by_codigo, seed_pcge_basico)

    numero = (numero_comprobante or "").strip()
    if not numero:
        raise ValueError("numero_comprobante del RxH es obligatorio")
    seed_pcge_basico(db)
    ensure_cxp_tipo_comprobante_column(db)
    ensure_cxp_origen_tipo_column(db)
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
    ret = (total * RETENCION_IR_4TA).quantize(Decimal("0.01")) if aplica_ret else Decimal("0")
    fecha = date.today()
    try:
        c_gasto = get_cuenta_by_codigo(db, "6322")
        c_pasivo = ensure_cuenta_4241(db)
        if not c_gasto or not c_pasivo:
            raise ValueError("Faltan cuentas 6322/4241 (seed PCGE)")
        neto = total - ret
        # Naturaleza con retención: DEBE 6322 (bruto) / HABER 40172 (ret 8%)
        # / HABER 4241 (neto en CxP). Sin retención: 6322 / 4241 por el total.
        lineas = [{"cuenta_id": c_gasto.id, "debe": total, "haber": Decimal("0")}]
        if ret > 0:
            c_ret = ensure_cuenta_40172(db)
            if not c_ret:
                raise ValueError("Falta cuenta 40172 (seed PCGE)")
            lineas.append({"cuenta_id": c_ret.id, "debe": Decimal("0"), "haber": ret})
        lineas.append({"cuenta_id": c_pasivo.id, "debe": Decimal("0"), "haber": neto})
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
            db, fecha, f"RxH destajo {numero}", "HONORARIOS", gasto.id, lineas)
        # Destajo de taller → destino 9211/7911 (MOD de confección),
        # nunca 941: la naturaleza es honorarios de taller, no gasto
        # administrativo ni planilla, sin importar la ficha del sastre.
        from app.services.finanzas import ensure_cuenta_7911, ensure_cuenta_9211
        c_dest = ensure_cuenta_9211(db)
        c_79 = ensure_cuenta_7911(db)
        if c_dest and c_79:
            crear_asiento_flush(
                db, fecha, f"Destino 9211 RxH {numero}", "HONORARIOS", gasto.id,
                [{"cuenta_id": c_dest.id, "debe": total, "haber": Decimal("0")},
                 {"cuenta_id": c_79.id, "debe": Decimal("0"), "haber": total}])
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
