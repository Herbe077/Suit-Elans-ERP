from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
import os
"""RxH simplificado: PENDIENTE → PROVISIONADO con botón único.

Sin estados intermedios: la provisión crea CxP RECIBO_HONORARIOS (4241/6322)
sin tocar MovimientoFinanciero; el pago en CxP genera 1 EGRESO.
"""
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
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    for m in (LineaAsientoContable, AsientoContable, MovimientoFinanciero,
              CashMovement, GastoRegistrado, CuentaPorPagar,
              DetalleJornada, RegistroJornada):
        db.query(m).delete()
    db.commit()


def _operario(db, tag):
    from app.core import security
    from app.models.user import User
    u = User(email=f"rxh-{tag}@t.pe", full_name=f"Sastre RxH {tag}",
             hashed_password=security.hash_password("x"),
             role="SASTRE-ASISTENTE", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _registro(db, operario_id, subtotal, estado="PENDIENTE"):
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    cat = CatalogoOperacion(codigo=f"RXH-{operario_id}-{subtotal}",
                            nombre_operacion="Op RxH",
                            tarifa_base=Decimal(str(subtotal)), activa=True)
    db.add(cat)
    db.flush()
    reg = RegistroJornada(operario_id=operario_id, fecha=date.today(),
                          estado=estado)
    db.add(reg)
    db.flush()
    db.add(DetalleJornada(registro_jornada_id=reg.id, operacion_id=cat.id,
                          cantidad=1, tarifa_aplicada=Decimal(str(subtotal)),
                          subtotal=Decimal(str(subtotal))))
    db.commit()
    db.refresh(reg)
    return reg


def _client_admin():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login",
               data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _egresos(db):
    from sqlalchemy import func
    from app.models.billing import CashMovement
    from app.models.finanzas import MovimientoFinanciero
    mf = db.query(func.coalesce(func.sum(MovimientoFinanciero.monto), 0)).filter(
        MovimientoFinanciero.tipo == "EGRESO").scalar() or 0
    cm = db.query(func.coalesce(func.sum(CashMovement.monto), 0)).filter(
        CashMovement.tipo == "egreso").scalar() or 0
    return float(mf), float(cm)


def test_registro_ingresa_pendiente_por_defecto():
    from app.modules.rendimiento.models import RegistroJornada
    db = _db()
    _limpia(db)
    op = _operario(db, "def")
    reg = RegistroJornada(operario_id=op.id, fecha=date.today())
    db.add(reg)
    db.commit()
    db.refresh(reg)
    assert reg.estado == "PENDIENTE"
    _limpia(db)
    db.close()


def test_generar_rxh_pendiente_a_provisionado_sin_flujo():
    from app.models.finanzas import (CuentaPorPagar, GastoRegistrado,
                                     MovimientoFinanciero)
    from app.modules.rendimiento.models import RegistroJornada
    db = _db()
    _limpia(db)
    op = _operario(db, "rxh")
    reg = _registro(db, op.id, 1000)
    assert reg.estado == "PENDIENTE"
    mf0, cm0 = _egresos(db)
    c, ck = _client_admin()
    r = c.post("/rendimiento/generar-rxh-cxp",
               data={"operario_id": str(op.id),
                     "numero_comprobante": "RXH-TEST-001",
                     "ruc_dni": "12345678", "con_retencion": "1"},
               cookies=ck)
    assert r.status_code in (200, 303)
    db.close()
    db = _db()
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "RXH-TEST-001").first()
    assert cxp is not None and cxp.estado == "POR_PAGAR"
    assert cxp.tipo_comprobante == "RECIBO_HONORARIOS"
    assert cxp.monto_total == 1000.0 and cxp.retencion == 80.0
    assert cxp.saldo_pendiente == 920.0 and cxp.monto_pagado == 0.0
    g = db.query(GastoRegistrado).filter(
        GastoRegistrado.numero_comprobante == "RXH-TEST-001").first()
    assert g is not None and g.categoria == "HONORARIOS_RXH"
    assert float(g.monto_igv or 0) == 0.0
    assert db.get(RegistroJornada, reg.id).estado == "PROVISIONADO"
    mf1, cm1 = _egresos(db)
    assert (mf1, cm1) == (mf0, cm0)
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count() == 0
    _limpia(db)
    db.close()


def test_provision_asienta_4241_contra_6322():
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    db = _db()
    _limpia(db)
    op = _operario(db, "cta")
    _registro(db, op.id, 500)
    c, ck = _client_admin()
    c.post("/rendimiento/generar-rxh-cxp",
           data={"operario_id": str(op.id),
                 "numero_comprobante": "RXH-TEST-4241",
                 "ruc_dni": "11223344"},
           cookies=ck)
    db.close()
    db = _db()
    from app.models.finanzas import AsientoContable, GastoRegistrado
    g = db.query(GastoRegistrado).filter(
        GastoRegistrado.numero_comprobante == "RXH-TEST-4241").first()
    assert g is not None
    asientos = db.query(AsientoContable).filter(
        AsientoContable.origen_id == g.id,
        AsientoContable.origen_tipo == "HONORARIOS").all()
    assert asientos, "sin asiento de provisión RxH"
    por_cuenta: dict = {}
    for a in asientos:
        for l in db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == a.id).all():
            ct = db.get(CuentaContable, l.cuenta_id)
            d, h = por_cuenta.setdefault(ct.codigo, [0.0, 0.0])
            por_cuenta[ct.codigo] = [d + float(l.debe or 0),
                                     h + float(l.haber or 0)]
    assert por_cuenta.get("6322", [0, 0])[0] >= 500.0
    assert por_cuenta.get("4241", [0, 0])[1] >= 500.0
    _limpia(db)
    db.close()


def test_pago_cxp_genera_un_egreso_por_lo_amortizado():
    from app.models.finanzas import CuentaPorPagar, MovimientoFinanciero
    from app.services import contabilidad as C
    db = _db()
    _limpia(db)
    op = _operario(db, "pag")
    _registro(db, op.id, 1000)
    c, ck = _client_admin()
    c.post("/rendimiento/generar-rxh-cxp",
           data={"operario_id": str(op.id),
                 "numero_comprobante": "RXH-TEST-002",
                 "ruc_dni": "87654321", "con_retencion": "1"},
           cookies=ck)
    db.close()
    db = _db()
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "RXH-TEST-002").first()
    assert cxp is not None
    n0 = db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count()
    p = C.pagar_proveedor(db, cxp.id, 500.0, "1041",
                            permitir_sobregiro=True)
    assert p["estado"] == "PARCIAL" and p["saldo"] == 420.0
    movs = db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").all()
    assert len(movs) == n0 + 1
    assert movs[-1].monto == 500.0  # lo amortizado, no el total
    db.refresh(cxp)
    assert cxp.monto_pagado == 500.0 and cxp.saldo_pendiente == 420.0
    _limpia(db)
    db.close()


def test_sin_estados_intermedios():
    c, ck = _client_admin()
    r1 = c.post("/rendimiento/calculadora/aprobar",
                data={"registro_id": "1"}, cookies=ck)
    r2 = c.post("/rendimiento/calculadora/liquidar",
                data={"registro_id": "1"}, cookies=ck)
    assert r1.status_code in (404, 405)
    assert r2.status_code in (404, 405)


def test_cxc_sin_proveedores():
    from app.models.finanzas import CuentaPorCobrar
    cols = set(CuentaPorCobrar.__table__.columns.keys())
    assert not {c for c in cols if "proveedor" in c or "supplier" in c}
