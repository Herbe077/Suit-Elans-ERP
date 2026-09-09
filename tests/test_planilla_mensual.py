"""Planilla mensual: parámetros dinámicos, provisión y pago por 4111."""
import os
from datetime import date
from decimal import Decimal


def _admin():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={
        "username": "admin@suitelans.mx",
        "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _mapa(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _empleado(tag, sueldo=2000.0, asigfam=True, sistema="ONP"):
    from app.core.database import SessionLocal
    from app.models.personnel import Empleado
    db = SessionLocal()
    e = Empleado(nombres=f"Planilla{tag}", apellidos="Test",
                 dni=f"90{tag}1111", sueldo_basico=sueldo,
                 asignacion_familiar=asigfam, sistema_pensiones=sistema,
                 regimen_laboral="General", activo=True)
    db.add(e)
    db.commit()
    eid = e.id
    db.close()
    return eid


def _limpia_planilla(db, anio, mes, eids):
    from app.models.finanzas import (
        AsientoContable,
        CuentaPorPagar,
        GastoRegistrado,
        LineaAsientoContable,
    )
    from app.models.personnel import (
        Empleado,
        PlanillaCabecera,
        PlanillaDetalle,
    )
    cab = db.query(PlanillaCabecera).filter(
        PlanillaCabecera.anio == anio,
        PlanillaCabecera.mes == mes).first()
    if cab:
        aids = [a.id for a in db.query(AsientoContable).filter(
            AsientoContable.origen_id == cab.gasto_id).all()]
        cxps = db.query(CuentaPorPagar).filter(
            CuentaPorPagar.numero_factura == f"PL-{anio}-{mes:02d}").all()
        for cx in cxps:
            aids += [a.id for a in db.query(AsientoContable).filter(
                AsientoContable.origen_tipo == "PAGO",
                AsientoContable.origen_id == cx.id).all()]
        if aids:
            db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id.in_(aids)).delete(
                synchronize_session=False)
            db.query(AsientoContable).filter(
                AsientoContable.id.in_(aids)).delete(synchronize_session=False)
        db.query(PlanillaDetalle).filter(
            PlanillaDetalle.cabecera_id == cab.id).delete()
        db.query(CuentaPorPagar).filter(
            CuentaPorPagar.numero_factura == f"PL-{anio}-{mes:02d}").delete()
        db.query(GastoRegistrado).filter(
            GastoRegistrado.id == cab.gasto_id).delete()
        db.query(PlanillaCabecera).filter(PlanillaCabecera.id == cab.id).delete()
    for eid in eids:
        db.query(Empleado).filter(Empleado.id == eid).delete()
    db.commit()


def test_onp_dinamica_135_y_provision():
    """ONP al 13.5% en Administración => la planilla lo usa (no hardcoded)."""
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable
    from app.models.personnel import PlanillaCabecera, PlanillaDetalle
    from app.services import config as cfg
    from app.services import planilla as pl
    db = SessionLocal()
    anterior = cfg.get(db, "fondo_onp_pct", "13.00")
    cfg.set(db, "fondo_onp_pct", "13.50")
    eid = _empleado("ONP", sueldo=2000.0, asigfam=True, sistema="ONP")
    try:
        r = pl.procesar_planilla(db, 2026, 3)
        # bruto 2102.50, desc 13.5% = 283.84, neto 1818.66, essalud 189.23
        assert r["total_bruto"] == 2102.5
        assert r["total_descuento"] == 283.84
        assert r["total_neto"] == 1818.66
        assert r["total_essalud"] == 189.22
        mapa = _mapa(db, r["asiento_id"])
        assert mapa["6211"] == (Decimal("2102.50"), Decimal("0"))
        assert mapa["6271"] == (Decimal("189.22"), Decimal("0"))
        assert mapa["4031"] == (Decimal("0"), Decimal("189.22"))
        assert mapa["4032"] == (Decimal("0"), Decimal("283.84"))
        assert mapa["4111"] == (Decimal("0"), Decimal("1818.66"))
        # destino 921/791 por el bruto
        dest = [a for a in db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "PLANILLA",
            AsientoContable.origen_id == r["gasto_id"]).all()
            if a.id != r["asiento_id"]]
        assert len(dest) == 1
        mapad = _mapa(db, dest[0].id)
        assert mapad["9211"] == (Decimal("2102.50"), Decimal("0"))
        assert mapad["791"] == (Decimal("0"), Decimal("2102.50"))
        # detalle por empleado
        det = db.query(PlanillaDetalle).filter(
            PlanillaDetalle.cabecera_id == r["cabecera_id"]).all()
        assert len(det) == 1 and det[0].pension_pct == 13.5
        assert db.query(PlanillaCabecera).filter(
            PlanillaCabecera.id == r["cabecera_id"]).one().estado == "PROCESADA"
    finally:
        _limpia_planilla(db, 2026, 3, [eid])
        cfg.set(db, "fondo_onp_pct", anterior)
        db.close()


def test_pago_planilla_salda_4111():
    """Pagar la planilla: CxP PAGADO + asiento DEBE 4111 / HABER 1041."""
    from app.core.database import SessionLocal
    from app.models.finanzas import CuentaPorPagar
    from app.services import planilla as pl
    from app.services import tesoreria_service as tes
    db = SessionLocal()
    eid = _empleado("PAGO", sueldo=1500.0, asigfam=False, sistema="AFP_INTEGRA")
    try:
        r = pl.procesar_planilla(db, 2026, 4)
        cxp = db.get(CuentaPorPagar, r["cxp_id"])
        assert cxp.estado == "POR_PAGAR" and cxp.saldo_pendiente > 0
        p = tes.ejecutar_pago_proveedor(db, r["gasto_id"],
                                        medio_pago="banco",
                                        monto=cxp.saldo_pendiente,
                                        permitir_sobregiro=True)
        db.refresh(cxp)
        assert cxp.estado == "PAGADO" and cxp.saldo_pendiente == 0.0
        mapa = _mapa(db, p["asiento_id"])
        assert mapa["4111"] == (Decimal(str(r["total_neto"])), Decimal("0"))
        assert mapa["1041"] == (Decimal("0"), Decimal(str(r["total_neto"])))
    finally:
        _limpia_planilla(db, 2026, 4, [eid])
        db.close()


def test_admin_guarda_parametros(client=None):
    c, ck = _admin()
    r = c.post("/admin/configuracion",
               data={"sede_nombre": "X", "asignacion_familiar": "110.00",
                     "essalud_pct": "9.00", "retencion_4ta_pct": "8.00",
                     "fondo_onp_pct": "13.00",
                     "fondo_afp_integra_pct": "12.80",
                     "fondo_afp_habitat_pct": "12.87",
                     "fondo_afp_prima_pct": "12.82",
                     "fondo_afp_profuturo_pct": "12.89"},
               cookies=ck)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.services import config as cfg
    db = SessionLocal()
    try:
        assert cfg.get(db, "asignacion_familiar") == "110.00"
        assert cfg.fondo_pct(db, "AFP_HABITAT") == 12.87
    finally:
        cfg.set(db, "asignacion_familiar", "102.50")
        db.close()
    t = c.get("/admin/configuracion", cookies=ck).text
    assert "Parámetros Laborales" in t
    t = c.get("/admin/personal", cookies=ck).text
    assert "Procesar Planilla Mensual" in t and "Sistema de Pensiones" in t


def test_endpoint_procesar_planilla_sin_422():
    """POST form /admin/personal/planilla (anio/mes como texto de form).

    Regresión: la ruta específica debe matchear antes que /personal/{eid}
    (antes devolvía 422 int_parsing en 'eid' con valor 'planilla').
    """
    c, ck = _admin()
    from app.core.database import SessionLocal
    from app.models.personnel import Empleado, PlanillaCabecera
    db = SessionLocal()
    e = Empleado(nombres="Endpoint", apellidos="Planilla",
                 dni="90888777", sueldo_basico=1200.0,
                 asignacion_familiar=False, sistema_pensiones="ONP",
                 regimen_laboral="General", activo=True)
    db.add(e)
    db.commit()
    eid = e.id
    db.close()
    try:
        # strings como los envía un <form> real (no JSON)
        r = c.post("/admin/personal/planilla",
                   data={"anio": "2026", "mes": "9"}, cookies=ck,
                   follow_redirects=False)
        assert r.status_code == 303, r.text
        assert "planilla" not in r.headers.get("location", "") or \
            "error" not in r.headers.get("location", "")
        db = SessionLocal()
        cab = db.query(PlanillaCabecera).filter(
            PlanillaCabecera.anio == 2026,
            PlanillaCabecera.mes == 9).one()
        assert cab.total_bruto == 1200.0 and cab.total_neto > 0
        assert "422" not in (r.text or "")
        db.close()
        # duplicado: redirige con error visible, sin 500/422
        r2 = c.post("/admin/personal/planilla",
                    data={"anio": "2026", "mes": "9"}, cookies=ck,
                    follow_redirects=False)
        assert r2.status_code == 303
        assert "error=planilla_existe" in r2.headers.get("location", "")
    finally:
        db = SessionLocal()
        _limpia_planilla(db, 2026, 9, [eid])
        db.close()
