"""Tesorería: pago centralizado de obligaciones a proveedores (gastos + CxP).

`ejecutar_pago_proveedor` es el ÚNICO punto de salida real de dinero para
gastos: actualiza gasto y CxP espejo (PAGADO/PARCIAL), registra el EGRESO
(MovimientoFinanciero + Caja) y el asiento de cancelación
(DEBE pasivo 4212/4111/424/4241/4699/4654 por el neto / HABER 1011/1041),
todo en UNA transacción (un solo commit).

El pasivo se resuelve por la naturaleza provisionada (cuenta HABER del
asiento del gasto: 4241 para RxH de destajo, 424/4212/... para el resto),
nunca por la ficha contractual del empleado.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.billing import CashMovement
from app.models.finanzas import (
    CuentaPorPagar,
    GastoRegistrado,
    MovimientoFinanciero,
)

# Medio de pago (form/API) → cuenta de efectivo/bancos.
# Centralizado en app/services/medios_pago.py (única fuente de verdad).
from app.services.medios_pago import (
    MEDIO_CUENTA,
    MEDIOS_PAGO,
    cuenta_por_medio as _cuenta_por_medio_central,
    normalizar_medio,
)

CUENTAS_VALIDAS = ("1011", "1041", "101", "104")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def cuenta_por_medio(medio_pago: str | None,
                     cuenta_origen_id: str | None = None) -> str:
    """Cuenta 10 para el pago (delega al módulo central)."""
    return _cuenta_por_medio_central(medio_pago, cuenta_origen_id)


def cuenta_pasivo_gasto(db: Session, gasto: GastoRegistrado):
    """Pasivo donde vive la provisión: HABER del asiento del gasto.

    Excluye la 40172 (retención IR, se paga a SUNAT por otra vía): devuelve
    4241 para RxH de destajo y 424/4212/4111/4699/4654 para el resto.
    Fallback a la matriz por categoría si el gasto aún no tiene asiento.
    """
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    from app.services import finanzas as fin

    cand = db.query(AsientoContable).filter(
        AsientoContable.origen_id == gasto.id,
        AsientoContable.origen_tipo != "PAGO").order_by(
        AsientoContable.id).all()
    respaldo = None
    once = None
    for a in cand:
        for l in db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == a.id).all():
            c = db.get(fin.CuentaContable, l.cuenta_id)
            if c and _d(l.haber) > 0 and (c.tipo or "") == "PASIVO":
                if c.codigo == "40172":
                    respaldo = respaldo or c
                    continue
                # 4111 (neto a pagar, p.ej. planilla) prevalece sobre
                # tributos (4031/4032) que se cancelan por otra vía.
                if c.codigo == "4111":
                    return c
                once = once or c
    if once is not None:
        return once
    if respaldo is not None:
        return respaldo
    return db.query(fin.CuentaContable).filter(
        fin.CuentaContable.codigo == fin.pasivo_por_categoria(
            gasto.categoria)).first()


def cxp_espejo_gasto(db: Session, gasto: GastoRegistrado):
    """CxP espejo del gasto (clave: comprobante + proveedor), si existe."""
    clave = (gasto.numero_comprobante or "").strip() or f"GASTO-{gasto.id}"
    q = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == clave)
    if gasto.proveedor_id:
        q = q.filter(CuentaPorPagar.proveedor_id == gasto.proveedor_id)
    return q.first()


def asegurar_cxp_espejo(db: Session, gasto: GastoRegistrado):
    """Crea el espejo CxP del gasto si falta (sin asiento: ya provisionado)."""
    cxp = cxp_espejo_gasto(db, gasto)
    if cxp or not gasto.proveedor_id:
        return cxp
    total = float(_d(gasto.monto_total))
    ret = float(_d(getattr(gasto, "retencion", 0) or 0))
    cxp = CuentaPorPagar(
        proveedor_id=gasto.proveedor_id,
        numero_factura=(gasto.numero_comprobante or "").strip() or f"GASTO-{gasto.id}",
        tipo_comprobante=(gasto.tipo_comprobante or "FACTURA"),
        monto_total=total, monto_pagado=0.0,
        saldo_pendiente=round(max(total - ret, 0.0), 2),
        retencion=ret, fecha_emision=gasto.fecha_emision,
        estado="POR_PAGAR")
    db.add(cxp)
    db.flush()
    return cxp


def ejecutar_pago_proveedor(db: Session, gasto_id: int,
                            medio_pago: str = "banco",
                            cuenta_origen_id: str | None = None,
                            monto: float | Decimal | None = None,
                            usuario_id: int | None = None,
                            voucher: str | None = None,
                            fecha: date | None = None,
                            permitir_sobregiro: bool = False) -> dict:
    """Paga un gasto (total o abono) sincronizando gasto + CxP + caja + diario.

    - Gasto sin CxP: exige el neto completo (no hay dónde trackear parciales).
    - Con CxP: admite parciales; el gasto pasa a PAGADO solo al saldarse.
    - El asiento debita el pasivo provisionado (4111 planilla / 4241 RxH /
      4212 proveedores / …) por lo pagado y acredita 1011/1041. Un solo
      commit; rollback total al fallar.
    - El EGRESO se etiqueta por naturaleza (planilla / alquiler-servicios /
      insumos).
    - Sobregiro estricto: 1011 bloquea sin saldo (ValueError → 400); 1041
      exige permitir_sobregiro=True.
    """
    from app.services.contabilidad import exigir_periodo_abierto

    try:
        exigir_periodo_abierto(db, fecha)
        gasto = db.query(GastoRegistrado).with_for_update().filter(
            GastoRegistrado.id == gasto_id).first()
        if not gasto:
            raise ValueError("Gasto no encontrado")
        # Idempotencia por voucher (primero): el mismo comprobante + voucher
        # no genera un segundo EGRESO ni un segundo asiento, aunque el gasto
        # ya figure PAGADO (doble clic / reintento tras commit).
        vouchers = (voucher or "").strip()
        if vouchers:
            from app.models.finanzas import AsientoContable as _A
            dup = db.query(MovimientoFinanciero).filter(
                MovimientoFinanciero.tipo == "EGRESO",
                MovimientoFinanciero.comprobante_ref == gasto.numero_comprobante,
                MovimientoFinanciero.descripcion.like(f"%V:{vouchers}%")).order_by(
                MovimientoFinanciero.id.desc()).first()
            if dup is not None:
                as_dup = db.query(_A).filter(
                    _A.origen_tipo == "PAGO",
                    _A.origen_id == gasto.id).order_by(_A.id.desc()).first()
                espejo = cxp_espejo_gasto(db, gasto)
                return {"gasto_id": gasto.id,
                        "cxp_id": espejo.id if espejo else None,
                        "asiento_id": as_dup.id if as_dup else None,
                        "asiento_numero": as_dup.numero if as_dup else "",
                        "monto": float(dup.monto), "gasto_estado": gasto.estado,
                        "cxp_estado": None, "duplicado": True,
                        "cuenta_pasivo": "", "cuenta_caja": dup.cuenta_origen}
        if gasto.estado == "PAGADO":
            raise ValueError("El gasto ya está PAGADO")
        neto = _d(gasto.monto_total) - _d(getattr(gasto, "retencion", 0) or 0)
        if neto <= 0:
            raise ValueError("Neto a pagar inválido (revisa la retención)")
        cxp = asegurar_cxp_espejo(db, gasto)
        if cxp is None:
            pendiente = neto
        else:
            pendiente = _d(cxp.saldo_pendiente)
        monto_d = _d(monto) if monto else pendiente
        if monto_d <= 0 or monto_d - pendiente > Decimal("0.000001"):
            raise ValueError(f"Monto inválido (pendiente: {pendiente})")
        if cxp is None and monto_d < neto:
            raise ValueError(
                "Abono parcial sin CxP: el gasto exige el neto completo")
        from app.services import medios_pago as _mp
        medio = _mp.normalizar_medio(medio_pago)
        cuenta_codigo = _mp.cuenta_por_medio(medio, cuenta_origen_id)
        if (cuenta_origen_id or "").strip() in ("1011", "1041", "101", "104") \
                and _mp.cuenta_por_medio(medio) != cuenta_codigo:
            medio = "EFECTIVO" if cuenta_codigo == "1011" else "TRANSFERENCIA"
        id_cuenta = _mp.id_cuenta(db, cuenta_codigo)
        # Sobregiro estricto antes de mutar: 1011 bloquea; 1041 exige flag.
        # Se captura la advertencia pre-movimiento (el saldo post-flush ya
        # incluye el egreso y mostraría cifras inconsistentes).
        from app.services import finanzas as _fin_chk
        adv_sobregiro = _fin_chk.exigir_saldo_tesoreria(
            db, cuenta_codigo, monto_d,
            permitir_sobregiro=bool(permitir_sobregiro))
        c_prov = cuenta_pasivo_gasto(db, gasto)
        if not c_prov:
            raise ValueError("Pasivo del gasto no encontrado (plan operativo)")
        from app.services.motor_contable import post_manual, post_regla
        hoja = cuenta_codigo  # ya hoja 1011/1041 vía cuenta_por_medio central
        dims = {"proveedor_id": gasto.proveedor_id,
                "centro_costo_id": gasto.centro_costo_id}
        glosa = f"Pago gasto {gasto.numero_comprobante or gasto.id}"
        if (voucher or "").strip():
            glosa += f" V:{voucher.strip()}"
        if getattr(c_prov, "codigo", "") == "4212" and dims.get("proveedor_id"):
            regla_pago = "PAGO_CAJA" if hoja == "1011" else "PAGO_BANCO"
            asiento = post_regla(
                db, regla_pago, [monto_d], [monto_d], dims, glosa,
                "PAGO", gasto.id, fecha)
        else:
            asiento = post_manual(
                db, [(c_prov.codigo, monto_d)], [(hoja, monto_d)], dims,
                glosa, "PAGO", gasto.id, fecha)
        if cxp is not None:
            cxp.monto_pagado = float(_d(cxp.monto_pagado) + monto_d)
            cxp.saldo_pendiente = float(max(
                _d(cxp.monto_total) - _d(cxp.monto_pagado) - _d(cxp.retencion),
                Decimal("0")))
            cxp.estado = "PAGADO" if _d(cxp.saldo_pendiente) <= Decimal("0.01") else "PARCIAL"
        saldado = (cxp is None) or cxp.estado == "PAGADO"
        if saldado:
            gasto.estado = "PAGADO"
        es_banco = cuenta_codigo == "1041"
        from app.services import finanzas as _fin
        cat_flujo = _fin.categoria_flujo_pago(
            gasto.categoria, None, gasto.numero_comprobante)
        desc_flujo = _fin.descripcion_flujo_pago(
            cat_flujo, f"{gasto.categoria} {gasto.numero_comprobante or gasto.id}".strip(),
            vouchers or None)
        if adv_sobregiro:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "sobregiro %s: %s", glosa, adv_sobregiro)
        db.add(MovimientoFinanciero(
            tipo="EGRESO", categoria=cat_flujo, monto=float(monto_d),
            cuenta_origen="Banco" if es_banco else "Caja",
            medio_pago=medio, cuenta_contable_id=id_cuenta,
            comprobante_ref=gasto.numero_comprobante, usuario_id=usuario_id,
            descripcion=desc_flujo))
        db.add(CashMovement(
            tipo="egreso",
            concepto=desc_flujo,
            monto=float(monto_d), metodo=medio, medio_pago=medio,
            cuenta_contable_id=id_cuenta,
            usuario_id=usuario_id))
        db.commit()
        return {"gasto_id": gasto.id, "cxp_id": cxp.id if cxp else None,
                "asiento_id": asiento.id, "asiento_numero": asiento.numero,
                "monto": float(monto_d), "gasto_estado": gasto.estado,
                "cxp_estado": cxp.estado if cxp else None, "duplicado": False,
                "cuenta_pasivo": c_prov.codigo, "cuenta_caja": hoja,
                "categoria_flujo": cat_flujo,
                "advertencia_sobregiro": adv_sobregiro}
    except Exception:
        db.rollback()
        raise


def marcar_gasto_pagado_si_saldado(db: Session, cxp: CuentaPorPagar) -> None:
    """Sincroniza gasto espejo cuando su CxP queda saldada (sin commit)."""
    if not cxp or cxp.estado != "PAGADO":
        return
    num = (cxp.numero_factura or "").strip()
    gasto = None
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
    if gasto is not None and gasto.estado != "PAGADO":
        gasto.estado = "PAGADO"
