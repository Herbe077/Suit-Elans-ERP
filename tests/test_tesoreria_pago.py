"""Tesorería unificada: pagar gasto sincroniza CxP + caja + diario 10."""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    f.ensure_gasto_retencion_column(db)
    f.ensure_cxp_tipo_comprobante_column(db)
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
    db.query(Supplier).filter(Supplier.nombre.like("TES-%")).delete(
        synchronize_session=False)
    db.commit()


def _gasto(db, numero, base=1000.0, ret=80.0):
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    sup = Supplier(nombre=f"TES-{numero}", ruc=f"R-{numero}")
    db.add(sup)
    db.flush()
    g, _a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="HONORARIOS_RXH", monto_base=base,
        tipo_comprobante="RECIBO_HONORARIOS", numero_comprobante=numero,
        proveedor_id=sup.id, ruc_proveedor=sup.ruc, retencion=ret)
    return g


def _fondear(db, codigo="1041", monto=5000.0):
    """Aporte de capital para fondear Caja/Bancos en pruebas (1011/1041).

    La regla estricta exige saldo disponible: sin fondeo, el pago por
    banco requiere permitir_sobregiro=True y la caja 1011 siempre bloquea.
    """
    from app.services.motor_contable import post_manual
    post_manual(db, [(codigo, float(monto))], [("5011", float(monto))],
                {}, f"Fondeo test {codigo}", "APERTURA", None, date.today())
    db.commit()


def test_pago_gasto_sincroniza_cxp_y_caja():
    from app.models.billing import CashMovement
    from app.models.finanzas import (CuentaPorPagar, GastoRegistrado,
                                     MovimientoFinanciero)
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "TES-001")
    gid = g.id
    r = tes.ejecutar_pago_proveedor(db, gid, medio_pago="banco", usuario_id=None,
                                      permitir_sobregiro=True)
    assert r["monto"] == 920.0 and r["cuenta_caja"] == "1041"
    assert r["cuenta_pasivo"] == "4241"  # pasivo RxH homogéneo
    assert r["gasto_estado"] == "PAGADO" and r["cxp_estado"] == "PAGADO"
    db.close()
    db = _db()
    assert db.get(GastoRegistrado, gid).estado == "PAGADO"
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "TES-001").first()
    assert cxp is not None and cxp.estado == "PAGADO"
    assert cxp.saldo_pendiente == 0.0
    movs = db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").all()
    assert len(movs) == 1 and movs[0].monto == 920.0
    assert db.query(CashMovement).filter(
        CashMovement.tipo == "egreso").count() == 1
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    lineas = db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == r["asiento_id"]).all()
    mapa = {db.get(CuentaContable, l.cuenta_id).codigo:
            (Decimal(str(l.debe)), Decimal(str(l.haber))) for l in lineas}
    assert mapa["4241"] == (Decimal("920"), Decimal("0"))
    assert mapa["1041"] == (Decimal("0"), Decimal("920"))
    _limpia(db)
    db.close()


def test_pago_parcial_deja_gasto_pendiente_y_salda_despues():
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "TES-PARC")
    gid = g.id
    _fondear(db, "1011", 5000.0)  # caja 1011 bloquea sin saldo disponible
    r1 = tes.ejecutar_pago_proveedor(db, gid, medio_pago="caja", monto=400.0)
    assert r1["cxp_estado"] == "PARCIAL" and r1["gasto_estado"] == "PENDIENTE"
    assert r1["cuenta_caja"] == "1011"
    db.close()
    db = _db()
    assert db.get(GastoRegistrado, gid).estado == "PENDIENTE"
    r2 = tes.ejecutar_pago_proveedor(db, gid, medio_pago="caja")
    assert r2["gasto_estado"] == "PAGADO" and r2["cxp_estado"] == "PAGADO"
    assert r2["monto"] == 520.0
    _limpia(db)
    db.close()


def test_pago_cxp_marca_gasto_pagado():
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    from app.services import contabilidad as C
    db = _db()
    _limpia(db)
    g = _gasto(db, "TES-CXP")
    gid = g.id
    C.sincronizar_cxp_desde_gastos(db)
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "TES-CXP").first()
    assert cxp is not None and cxp.estado == "POR_PAGAR"
    C.pagar_proveedor(db, cxp.id, cxp.saldo_pendiente, "1041",
                        permitir_sobregiro=True)
    db.close()
    db = _db()
    assert db.get(GastoRegistrado, gid).estado == "PAGADO"
    _limpia(db)
    db.close()


def test_api_tesoreria_pagar_gasto(api_token):
    from app.main import app
    from fastapi.testclient import TestClient
    db = _db()
    _limpia(db)
    g = _gasto(db, "TES-API")
    gid = g.id
    db.close()
    c = TestClient(app)
    r = c.post("/api/v1/tesoreria/pagar-gasto",
               json={"gasto_id": gid, "permitir_sobregiro": True},
               headers={"Authorization": f"Bearer {api_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gasto_estado"] == "PAGADO" and body["cxp_estado"] == "PAGADO"
    assert body["monto"] == 920.0
    db = _db()
    _limpia(db)
    db.close()


def test_gasto_contado_paga_al_instante(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    from app.models.purchasing import Supplier
    db = SessionLocal()
    sup = db.query(Supplier).filter(Supplier.nombre == "TES-CONTADO").first()
    if not sup:
        sup = Supplier(nombre="TES-CONTADO", ruc="TESCONTADO1")
        db.add(sup)
        db.commit()
        db.refresh(sup)
    sid = sup.id
    _fondear(db, "1041", 5000.0)  # CONTADO paga por banco: exige saldo
    db.close()
    r = client.post("/finanzas/gastos", data={
        "fecha": date.today().isoformat(), "proveedor_id": str(sid),
        "ruc": "", "tipo_comprobante": "FACTURA",
        "numero_comprobante": "TES-CONT-001", "categoria": "OTRO",
        "cuenta_codigo": "", "monto_total": "1180",
        "condicion_pago": "CONTADO", "medio_pago": "banco"},
        cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    g = db.query(GastoRegistrado).filter(
        GastoRegistrado.numero_comprobante == "TES-CONT-001").first()
    assert g is not None and g.estado == "PAGADO"
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "TES-CONT-001").first()
    assert cxp is not None and cxp.estado == "PAGADO"
    from app.models.finanzas import MovimientoFinanciero
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO",
        MovimientoFinanciero.comprobante_ref == "TES-CONT-001").count() == 1
    db.close()


def test_voucher_duplicado_no_duplica_egreso():
    from app.models.finanzas import AsientoContable, MovimientoFinanciero
    from app.services import tesoreria_service as tes
    db = _db()
    _limpia(db)
    g = _gasto(db, "TES-VOUCH")
    gid = g.id
    r1 = tes.ejecutar_pago_proveedor(db, gid, medio_pago="banco",
                                     usuario_id=None, voucher="V-001",
                                     permitir_sobregiro=True)
    assert r1.get("duplicado") is False
    n_mov = db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count()
    n_asi = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PAGO",
        AsientoContable.origen_id == gid).count()
    # reintento con mismo voucher: no crea nada nuevo
    db.close()
    db = _db()
    r2 = tes.ejecutar_pago_proveedor(db, gid, medio_pago="banco",
                                     usuario_id=None, voucher="V-001")
    assert r2.get("duplicado") is True
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count() == n_mov
    assert db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PAGO",
        AsientoContable.origen_id == gid).count() == n_asi
    _limpia(db)
    db.close()


def test_cobro_total_con_taller_listo_completa_orden():
    from app.core.database import SessionLocal
    from app.models.order import Garment, Order
    from app.services import contabilidad as C
    db = SessionLocal()
    o = Order(folio="TES-AUTO-001", estado="confirmado", canal="sastreria",
              total=500.0, anticipo=0.0)
    db.add(o)
    db.flush()
    db.add(Garment(order_id=o.id, tipo="saco", precio=500.0,
                   estado_taller="CALIDAD_OK"))
    db.commit()
    oid = o.id
    db.close()
    db = SessionLocal()
    r = C.cobrar_venta(db, oid, 500.0, "transferencia", "1041",
                       usuario_id=None)
    assert r["saldo"] == 0.0 and r["entregada"] is True
    db.close()
    db = SessionLocal()
    assert db.get(Order, oid).estado == "entregado"
    db.close()
