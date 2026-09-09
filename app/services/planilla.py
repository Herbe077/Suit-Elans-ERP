"""Planilla mensual: cálculo por empleado + provisión agregada (un commit).

Por empleado activo con bruto > 0:
  bruto    = sueldo_básico + asignación familiar (parametrizada, si aplica)
  descuento = bruto × % fondo del empleado (parametrizado por sistema)
  neto     = bruto − descuento
  essalud  = bruto × % EsSalud (parametrizado)

Provisión agregada (origen PLANILLA):
  DEBE  6211 (bruto total) + DEBE 6271 (essalud total)
  HABER 4031 (essalud) + HABER 4032 (pensión) + HABER 4111 (neto total)
Destino: DEBE 921 / HABER 791 por el bruto total.
Más GastoRegistrado PLANILLA_PERSONAL + CxP (proveedor Planilla/Personal,
saldo = neto total) para el pago posterior por 4111.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finanzas import CuentaPorPagar, GastoRegistrado
from app.models.personnel import Empleado, PlanillaCabecera, PlanillaDetalle


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _proveedor_planilla(db: Session) -> int:
    from app.models.purchasing import Supplier
    sup = db.query(Supplier).filter(
        Supplier.nombre == "Planilla / Personal").first()
    if not sup:
        sup = Supplier(nombre="Planilla / Personal")
        db.add(sup)
        db.flush()
    return sup.id


def procesar_planilla(db: Session, anio: int, mes: int,
                      usuario_id: int | None = None,
                      fecha=None) -> dict:
    """Procesa la planilla del período. Idempotente por (anio, mes)."""
    from app.services import config as cfg
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.motor_contable import dim_centro, post_manual, post_regla

    fecha = fecha or date.today()
    if not (2020 <= int(anio) <= 2100 and 1 <= int(mes) <= 12):
        raise ValueError("Período inválido")
    anio, mes = int(anio), int(mes)
    ya = db.query(PlanillaCabecera).filter(
        PlanillaCabecera.anio == anio, PlanillaCabecera.mes == mes).first()
    if ya:
        raise ValueError(f"La planilla {anio}-{mes:02d} ya fue procesada")
    exigir_periodo_abierto(db, fecha)

    params = cfg.parametros_laborales(db)
    asig = round(float(params["asignacion_familiar"]), 2)
    essalud_rate = float(params["essalud_pct"]) / 100.0
    empleados = db.query(Empleado).filter(
        Empleado.activo.is_(True)).order_by(Empleado.apellidos).all()

    filas: list[dict] = []
    for e in empleados:
        bruto = round(float(e.sueldo_basico or 0)
                      + (asig if e.asignacion_familiar else 0.0), 2)
        if bruto <= 0:
            continue  # sin sueldo: nada que provisionar (destajo va por RxH)
        pct = float(cfg.fondo_pct(db, e.sistema_pensiones))
        desc = round(bruto * pct / 100.0, 2)
        neto = round(bruto - desc, 2)
        ess = round(bruto * essalud_rate, 2)
        filas.append({"empleado": e, "bruto": bruto, "pct": pct,
                      "descuento": desc, "neto": neto, "essalud": ess,
                      "asig": round(asig if e.asignacion_familiar else 0.0, 2)})
    if not filas:
        raise ValueError("Sin empleados con sueldo para procesar")

    tot_bruto = round(sum(f["bruto"] for f in filas), 2)
    tot_desc = round(sum(f["descuento"] for f in filas), 2)
    tot_neto = round(sum(f["neto"] for f in filas), 2)
    tot_ess = round(sum(f["essalud"] for f in filas), 2)
    numero = f"PL-{anio}-{mes:02d}"
    cc921 = dim_centro(db, "921")
    prov_id = _proveedor_planilla(db)

    try:
        gasto = GastoRegistrado(
            proveedor_id=prov_id, categoria="PLANILLA_PERSONAL",
            glosa=f"Planilla {anio}-{mes:02d} ({len(filas)} empleados)",
            monto_base=tot_bruto, monto_igv=0.0, monto_total=tot_bruto,
            retencion=tot_desc, clasificacion="GASTO_ADMINISTRATIVO",
            variabilidad="FIJO", centro_costo_id=cc921,
            tipo_comprobante="PLANILLA", numero_comprobante=numero,
            fecha_emision=fecha, fecha_vencimiento=fecha,
            actividad_flujo="OPERATIVO", estado="PENDIENTE")
        db.add(gasto)
        db.flush()
        # Provisión agregada: 6211+6271 vs 4031+4032+4111.
        asiento = post_manual(
            db, [("6211", tot_bruto), ("6271", tot_ess)],
            [("4031", tot_ess), ("4032", tot_desc), ("4111", tot_neto)],
            {"centro_costo_id": cc921}, f"Provisión planilla {numero}",
            "PLANILLA", gasto.id, fecha)
        post_regla(
            db, "DESTINO_921", [tot_bruto], [tot_bruto],
            {"centro_costo_id": cc921},
            f"Destino DESTINO_921 Planilla {numero}",
            "PLANILLA", gasto.id, fecha)
        cxp = CuentaPorPagar(
            proveedor_id=prov_id, numero_factura=numero,
            origen_tipo="GASTOS OPERATIVOS", actividad_flujo="OPERATIVO",
            tipo_comprobante="PLANILLA",
            monto_total=tot_bruto, monto_pagado=0.0,
            saldo_pendiente=tot_neto, retencion=tot_desc,
            fecha_emision=fecha, fecha_vencimiento=fecha,
            estado="POR_PAGAR")
        db.add(cxp)
        db.flush()
        cab = PlanillaCabecera(
            anio=anio, mes=mes, estado="PROCESADA",
            total_bruto=tot_bruto, total_descuento=tot_desc,
            total_neto=tot_neto, total_essalud=tot_ess,
            gasto_id=gasto.id, asiento_id=asiento.id)
        db.add(cab)
        db.flush()
        for f in filas:
            e = f["empleado"]
            db.add(PlanillaDetalle(
                cabecera_id=cab.id, empleado_id=e.id,
                nombres=e.nombre_completo,
                sueldo_basico=round(float(e.sueldo_basico or 0), 2),
                asignacion_familiar=f["asig"], total_bruto=f["bruto"],
                fondo_pension=e.sistema_pensiones or "ONP",
                pension_pct=f["pct"], descuento_pension=f["descuento"],
                neto_pagar=f["neto"], essalud=f["essalud"]))
        db.commit()
        return {"cabecera_id": cab.id, "gasto_id": gasto.id,
                "cxp_id": cxp.id, "asiento_id": asiento.id,
                "asiento_numero": asiento.numero,
                "empleados": len(filas), "total_bruto": tot_bruto,
                "total_descuento": tot_desc, "total_neto": tot_neto,
                "total_essalud": tot_ess}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
