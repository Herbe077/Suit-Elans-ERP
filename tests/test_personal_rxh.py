import os
"""Personal y Contratos + automatización RxH por periodo desde destajo."""
from datetime import date
from decimal import Decimal


def _client_admin():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login",
               data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _limpia_personal(db):
    from app.models.personnel import Empleado
    from app.models.purchasing import Supplier
    db.query(Empleado).filter(Empleado.dni.like("RXHT%")).delete(
        synchronize_session=False)
    db.query(Supplier).filter(Supplier.ruc.like("RXHT%")).delete(
        synchronize_session=False)
    db.commit()


def test_alta_y_edicion_empleado_con_ruc_y_retencion():
    from app.core.database import SessionLocal
    from app.models.personnel import Empleado
    c, ck = _client_admin()
    assert c.get("/admin/personal", cookies=ck).status_code == 200
    r = c.post("/admin/personal", data={
        "nombres": "Luis", "apellidos": "Prado", "dni": "",
        "ruc": "RXHT20123456", "telefono": "999111222",
        "puesto": "SASTRE_MAESTRO", "tipo_contrato": "DESTAJO_4TA",
        "aplica_retencion_8": "1"}, cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    e = db.query(Empleado).filter(Empleado.ruc == "RXHT20123456").first()
    assert e is not None and e.aplica_retencion_8 is True
    assert e.puesto == "SASTRE_MAESTRO" and e.tipo_contrato == "DESTAJO_4TA"
    eid = e.id
    db.close()
    r2 = c.post(f"/admin/personal/{eid}", data={
        "nombres": "Luis", "apellidos": "Prado Vega", "dni": "RXHT87654321",
        "ruc": "RXHT20123456", "telefono": "", "puesto": "PANTALONERO",
        "tipo_contrato": "DESTAJO_4TA", "activo": "1"}, cookies=ck)
    assert r2.status_code in (200, 303)
    db = SessionLocal()
    e2 = db.get(Empleado, eid)
    assert e2.apellidos == "Prado Vega" and e2.puesto == "PANTALONERO"
    assert e2.aplica_retencion_8 is False  # checkbox ausente = False
    assert e2.dni == "RXHT87654321"
    _limpia_personal(db)
    db.close()


def _operario_y_registro(db, tag, subtotal):
    from app.core import security
    from app.models.user import User
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    u = User(email=f"rxhauto-{tag}@t.pe", full_name=f"Sastre Auto {tag}",
             hashed_password=security.hash_password("x"),
             role="SASTRE-ASISTENTE", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    cat = CatalogoOperacion(codigo=f"RXHA-{tag}", nombre_operacion="Op auto",
                            tarifa_base=Decimal(str(subtotal)), activa=True)
    db.add(cat)
    db.flush()
    reg = RegistroJornada(operario_id=u.id, fecha=date.today(),
                          estado="PENDIENTE")
    db.add(reg)
    db.flush()
    db.add(DetalleJornada(registro_jornada_id=reg.id, operacion_id=cat.id,
                          cantidad=1, tarifa_aplicada=Decimal(str(subtotal)),
                          subtotal=Decimal(str(subtotal))))
    db.commit()
    return u, reg


def test_boton_automatizar_visible_con_saldo_pendiente():
    from app.core.database import SessionLocal
    db = SessionLocal()
    u, _reg = _operario_y_registro(db, "btn", 54.55)
    uid = u.id
    db.close()
    c, ck = _client_admin()
    t = c.get("/rendimiento/calculadora", cookies=ck).text
    assert "Automatizar Provisi" in t and "54.55" in t
    db = SessionLocal()
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    from app.models.user import User
    db.query(DetalleJornada).filter(
        DetalleJornada.registro_jornada_id.in_(
            db.query(RegistroJornada.id).filter(
                RegistroJornada.operario_id == uid))).delete(
        synchronize_session=False)
    db.query(RegistroJornada).filter(
        RegistroJornada.operario_id == uid).delete()
    db.query(CatalogoOperacion).filter(
        CatalogoOperacion.codigo == "RXHA-btn").delete()
    db.query(User).filter(User.id == uid).delete()
    db.commit()
    db.close()


def test_automatizar_rxh_con_perfil_empleado_y_sin_flujo():
    from sqlalchemy import func
    from app.core.database import SessionLocal
    from app.models.billing import CashMovement
    from app.models.finanzas import (CuentaPorPagar, GastoRegistrado,
                                     MovimientoFinanciero)
    from app.models.personnel import Empleado
    from app.modules.rendimiento.models import RegistroJornada
    db = SessionLocal()
    db.add(Empleado(nombres="Auto", apellidos="Flag", dni="RXHT11111111",
                    puesto="CHALEQUERO", tipo_contrato="DESTAJO_4TA",
                    aplica_retencion_8=True))
    db.commit()
    u, reg = _operario_y_registro(db, "emp", 500)
    uid, rid = u.id, reg.id
    mf0 = db.query(func.count(MovimientoFinanciero.id)).scalar()
    cm0 = db.query(func.count(CashMovement.id)).scalar()
    c, ck = _client_admin()
    r = c.post("/rendimiento/automatizar-gasto-rxh",
               data={"operario_id": str(uid),
                     "numero_comprobante": "RXH-AUTO-001",
                     "ruc_dni": "RXHT11111111"},
               cookies=ck)
    assert r.status_code in (200, 303)
    db.close()
    db = SessionLocal()
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "RXH-AUTO-001").first()
    assert cxp is not None and cxp.estado == "POR_PAGAR"
    assert cxp.tipo_comprobante == "RECIBO_HONORARIOS"
    assert cxp.monto_total == 500.0 and cxp.retencion == 40.0
    assert cxp.saldo_pendiente == 460.0
    g = db.query(GastoRegistrado).filter(
        GastoRegistrado.numero_comprobante == "RXH-AUTO-001").first()
    assert g is not None and float(g.monto_igv or 0) == 0.0
    assert db.get(RegistroJornada, rid).estado == "PROVISIONADO"
    assert db.query(func.count(MovimientoFinanciero.id)).scalar() == mf0
    assert db.query(func.count(CashMovement.id)).scalar() == cm0
    egr = db.query(func.coalesce(func.sum(MovimientoFinanciero.monto), 0)).filter(
        MovimientoFinanciero.tipo == "EGRESO").scalar() or 0
    assert float(egr) >= 0.0
    _limpia_personal(db)
    db.close()


def test_automatizar_rxh_retencion_por_umbral_1500():
    from app.core.database import SessionLocal
    from app.models.finanzas import CuentaPorPagar
    db = SessionLocal()
    u, _reg = _operario_y_registro(db, "umbral", 2000)
    uid = u.id
    c, ck = _client_admin()
    r = c.post("/rendimiento/automatizar-gasto-rxh",
               data={"operario_id": str(uid),
                     "numero_comprobante": "RXH-AUTO-002"},
               cookies=ck)
    assert r.status_code in (200, 303)
    db.close()
    db = SessionLocal()
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "RXH-AUTO-002").first()
    assert cxp is not None and cxp.retencion == 160.0
    assert cxp.saldo_pendiente == 1840.0
    _limpia_personal(db)
    db.close()


def test_calculadora_y_personal_200_con_tablas_vacias():
    from app.core.database import SessionLocal
    from app.models.personnel import Empleado
    from app.modules.rendimiento.models import (DetalleJornada,
                                                RegistroJornada)
    db = SessionLocal()
    db.query(DetalleJornada).delete()
    db.query(RegistroJornada).delete()
    db.query(Empleado).delete()
    db.commit()
    db.close()
    c, ck = _client_admin()
    assert c.get("/rendimiento/calculadora", cookies=ck).status_code == 200
    assert c.get("/admin/personal", cookies=ck).status_code == 200
