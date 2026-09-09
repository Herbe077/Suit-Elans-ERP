"""Regla estricta de sobregiro en Tesorería.

- 1011 Caja Operativa: bloquea sin saldo suficiente (ValueError → HTTP 400).
  La caja física no puede quedar en negativo.
- 1041 Bancos: exige flag explícito permitir_sobregiro=True.
"""
from datetime import date
from decimal import Decimal

import pytest


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services.plan_operativo import cargar_plan_operativo
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    return db


def _limpia(db):
    from app.models.billing import CashMovement
    from app.models.finanzas import (AsientoContable, CuentaPorPagar,
                                     GastoRegistrado, LineaAsientoContable,
                                     MovimientoFinanciero)
    from app.models.purchasing import Supplier
    for m in (LineaAsientoContable, AsientoContable, MovimientoFinanciero,
              CashMovement, GastoRegistrado, CuentaPorPagar):
        db.query(m).delete()
    db.query(Supplier).filter(Supplier.nombre.like("SOB-%")).delete(
        synchronize_session=False)
    db.commit()


def _gasto(db, numero, base=1000.0):
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    sup = Supplier(nombre=f"SOB-{numero}", ruc=f"SOB-{numero}")
    db.add(sup)
    db.flush()
    g, _a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="SERVICIOS_BASICOS",
        monto_base=base, tipo_comprobante="FACTURA",
        numero_comprobante=numero, proveedor_id=sup.id)
    return g


def _fondear(db, codigo, monto):
    from app.services.motor_contable import post_manual
    post_manual(db, [(codigo, float(monto))], [("5011", float(monto))],
                {}, f"Fondeo sobregiro {codigo}", "APERTURA", None,
                date.today())
    db.commit()


def test_caja_1011_bloquea_sin_saldo():
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "SOB-CAJA-1", base=25_000_000.0)
    with pytest.raises(ValueError, match="Caja Operativa"):
        tes.ejecutar_pago_proveedor(db, g.id, medio_pago="caja")
    db.close()


def test_caja_1011_permite_con_saldo_fondeado():
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "SOB-CAJA-2")
    _fondear(db, "1011", 5_000.0)
    r = tes.ejecutar_pago_proveedor(db, g.id, medio_pago="caja",
                                    monto=1000.0)
    assert r["gasto_estado"] == "PAGADO" and r["cuenta_caja"] == "1011"
    assert r.get("advertencia_sobregiro") is None
    _limpia(db)
    db.close()


def test_banco_1041_exige_flag():
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "SOB-BANCO-1", base=25_000_000.0)
    with pytest.raises(ValueError, match="permitir_sobregiro"):
        tes.ejecutar_pago_proveedor(db, g.id, medio_pago="banco")
    db.close()


def test_banco_1041_con_flag_autoriza_y_advierte():
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "SOB-BANCO-2")
    r = tes.ejecutar_pago_proveedor(db, g.id, medio_pago="banco",
                                    permitir_sobregiro=True)
    assert r["gasto_estado"] == "PAGADO"
    assert r.get("advertencia_sobregiro")
    _limpia(db)
    db.close()


def test_pagar_proveedor_cxp_aplica_misma_regla():
    from app.services import contabilidad as C
    db = _db()
    _limpia(db)
    g = _gasto(db, "SOB-CXP-1")
    C.sincronizar_cxp_desde_gastos(db)
    from app.models.finanzas import CuentaPorPagar
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "SOB-CXP-1").first()
    with pytest.raises(ValueError, match="permitir_sobregiro"):
        C.pagar_proveedor(db, cxp.id, cxp.saldo_pendiente, "1041")
    r = C.pagar_proveedor(db, cxp.id, cxp.saldo_pendiente, "1041",
                          permitir_sobregiro=True)
    assert r["estado"] == "PAGADO" and r["cuenta_pasivo"] == "4212"
    assert r.get("advertencia_sobregiro")
    _limpia(db)
    db.close()


def test_web_pagar_exige_flag_y_retorna_400(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    sup = None
    from app.models.purchasing import Supplier
    sup = Supplier(nombre="SOB-WEB", ruc="SOB-WEB-1")
    db.add(sup)
    db.commit()
    db.refresh(sup)
    from app.services import finanzas as f
    g, _a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="SERVICIOS_BASICOS",
        monto_base=20_000_000.0, tipo_comprobante="FACTURA",
        numero_comprobante="SOB-WEB-001", proveedor_id=sup.id)
    gid = g.id
    db.close()
    r = client.post("/finanzas/tesoreria/pagar",
                    data={"gasto_id": str(gid), "monto": "20000000",
                          "medio": "banco", "back": "/finanzas/gastos"},
                    cookies=auth_cookies)
    assert r.status_code == 400 and "permitir_sobregiro" in r.text
    r2 = client.post("/finanzas/tesoreria/pagar",
                     data={"gasto_id": str(gid), "monto": "20000000",
                           "medio": "banco", "permitir_sobregiro": "1",
                           "back": "/finanzas/gastos"},
                     cookies=auth_cookies, follow_redirects=False)
    assert r2.status_code == 303
    db = SessionLocal()
    from app.models.finanzas import GastoRegistrado
    assert db.get(GastoRegistrado, gid).estado == "PAGADO"
    # Limpieza: el gasto de 20M no debe contaminar balances de otros tests.
    from app.models.billing import CashMovement
    from app.models.finanzas import (AsientoContable, CuentaPorPagar,
                                     LineaAsientoContable,
                                     MovimientoFinanciero)
    from app.models.purchasing import Supplier
    aids = [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_id == gid).all()]
    aids += [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PAGO").all()
        if (a.glosa or "").find("SOB-WEB-001") >= 0]
    if aids:
        db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(aids)).delete(
            synchronize_session=False)
        db.query(AsientoContable).filter(
            AsientoContable.id.in_(aids)).delete(synchronize_session=False)
    db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.comprobante_ref == "SOB-WEB-001").delete()
    db.query(CashMovement).filter(
        CashMovement.concepto.contains("SOB-WEB-001")).delete(
        synchronize_session=False)
    db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "SOB-WEB-001").delete()
    db.query(GastoRegistrado).filter(GastoRegistrado.id == gid).delete()
    db.query(Supplier).filter(Supplier.nombre == "SOB-WEB").delete()
    db.commit()
    db.close()
