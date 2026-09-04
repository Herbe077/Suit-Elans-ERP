"""Gastos operativos: alquiler S/2000 + IGV -> asiento 6311/40111/4212 + P&L."""
from datetime import date
from decimal import Decimal


def _limpia(db):
    from app.models.finanzas import AsientoContable, GastoRegistrado, LineaAsientoContable
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.query(GastoRegistrado).delete()
    db.commit()


def test_registro_alquiler_2000_mas_igv():
    from app.core.database import Base, SessionLocal, engine
    from app.services import finanzas as f
    from app.models.finanzas import AsientoContable, GastoRegistrado, LineaAsientoContable

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    _limpia(db)

    hoy = date.today()
    gasto, asiento = f.registrar_gasto_operativo(
        db, fecha=hoy, categoria="ALQUILER", monto_base=2000, monto_igv=360,
        tipo_comprobante="FACTURA", numero_comprobante="F001-ALQ-TEST",
        ruc_proveedor="20123456789",
    )
    # 1) gasto guardado
    assert gasto.id
    assert gasto.categoria == "ALQUILER"
    assert Decimal(str(gasto.monto_base)) == Decimal("2000")
    assert Decimal(str(gasto.monto_igv)) == Decimal("360")
    assert Decimal(str(gasto.monto_total)) == Decimal("2360")
    assert gasto.estado == "PENDIENTE"
    g2 = db.get(GastoRegistrado, gasto.id)
    assert g2 and g2.numero_comprobante == "F001-ALQ-TEST"

    # 2) asiento 6311 (Debe 2000) + 40111 (Debe 360) + 4212 (Haber 2360)
    lineas = db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == asiento.id).all()
    assert len(lineas) == 3
    c6311 = f.get_cuenta_by_codigo(db, "6311")
    c40111 = f.get_cuenta_by_codigo(db, "40111")
    c4212 = f.get_cuenta_by_codigo(db, "4212")
    por_cuenta = {l.cuenta_id: (Decimal(str(l.debe)), Decimal(str(l.haber))) for l in lineas}
    assert por_cuenta[c6311.id] == (Decimal("2000"), Decimal("0"))
    assert por_cuenta[c40111.id] == (Decimal("360"), Decimal("0"))
    assert por_cuenta[c4212.id] == (Decimal("0"), Decimal("2360"))
    assert sum(d for d, _h in por_cuenta.values()) == sum(h for _d, h in por_cuenta.values())
    assert asiento.origen_tipo == "COMPRA" and asiento.origen_id == gasto.id

    # 3) P&L refleja el gasto operativo
    er = f.obtener_estado_resultados(db)
    assert er["gastos_operativos"] >= Decimal("2000")
    bal = f.obtener_balance_comprobacion(db)
    codigos = {r["codigo"]: r for r in bal}
    assert codigos["6311"]["debe"] >= Decimal("2000")
    # cleanup
    _limpia(db)
    db.close()


def test_endpoints_gastos_web():
    from app.core.database import Base
    from app.main import app
    from fastapi.testclient import TestClient

    Base.metadata.create_all(bind=__import__("app.core.database", fromlist=["engine"]).engine)
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 303
    ck = {"suitelans_token": r.cookies.get("suitelans_token")}
    assert c.get("/finanzas/gastos", cookies=ck).status_code == 200
    r2 = c.post("/finanzas/gastos", data={
        "fecha": date.today().isoformat(), "proveedor_id": "", "ruc": "20123456789",
        "tipo_comprobante": "FACTURA", "numero_comprobante": "F001-WEB-TEST",
        "categoria": "HONORARIOS", "cuenta_codigo": "6322",
        "monto_base": "500", "igv": "0", "centro_costo_id": "",
    }, cookies=ck)
    assert r2.status_code in (200, 303)
    # limpia lo creado por web
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable, GastoRegistrado, LineaAsientoContable
    db = SessionLocal()
    g = db.query(GastoRegistrado).filter(GastoRegistrado.numero_comprobante == "F001-WEB-TEST").first()
    if g:
        db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(
                [a.id for a in db.query(AsientoContable).filter(AsientoContable.origen_id == g.id).all()]
            )
        ).delete(synchronize_session=False)
        db.query(AsientoContable).filter(AsientoContable.origen_id == g.id).delete(synchronize_session=False)
        db.query(GastoRegistrado).filter(GastoRegistrado.id == g.id).delete()
        db.commit()
    db.close()
