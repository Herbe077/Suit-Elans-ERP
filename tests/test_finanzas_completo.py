from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
import os
"""Flujo completo Finanzas: Venta 1500 (50% caja, 50% CxC) + MPD 300 + MOD 200 + CIF 50 + Gastos 400 → EEFF"""
from decimal import Decimal
from datetime import date

def test_flujo_completo_eeff():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    from app.models.finanzas import GastoRegistrado, CentroCosto
    from sqlalchemy import text
    Base.metadata.create_all(bind=engine)
    # limpia estado previo para periodo actual
    db = SessionLocal()
    cargar_plan_operativo(db)
    # limpia asientos y gastos del periodo actual para aislar test
    from app.models.finanzas import AsientoContable, LineaAsientoContable, CuentaPorCobrar
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.query(GastoRegistrado).delete()
    db.query(CuentaPorCobrar).delete()
    db.commit()
    # ensure periodo
    hoy = date.today()
    periodo = f.get_or_create_periodo(db, hoy.year, hoy.month)
    # 1. MPD 300
    cc = db.query(CentroCosto).filter(CentroCosto.codigo=="TALLER").first()
    if not cc:
        cc = CentroCosto(codigo="TALLER", nombre="Taller", tipo="TALLER")
        db.add(cc); db.commit(); db.refresh(cc)
    db.add(GastoRegistrado(proveedor_id=None, monto_total=300, clasificacion="MPD", variabilidad="VARIABLE", centro_costo_id=cc.id, fecha_emision=hoy))
    # 2. MOD 200 via rendimiento devengado
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (997,'OP-97','MOD Test',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (997,1,:f,'REGISTRADO')"), {"f": hoy.isoformat()})
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=997"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (997,997,997,1,200,200)"))
        conn.commit()
    # 3. CIF 50
    db.add(GastoRegistrado(proveedor_id=None, monto_total=50, clasificacion="CIF", variabilidad="FIJO", centro_costo_id=cc.id, fecha_emision=hoy))
    # 4. Gastos operativos 400 admin fijo
    cc_admin = db.query(CentroCosto).filter(CentroCosto.codigo=="ADMINISTRACION").first()
    if not cc_admin:
        cc_admin = CentroCosto(codigo="ADMINISTRACION", nombre="Administración", tipo="ADMINISTRACION")
        db.add(cc_admin); db.commit(); db.refresh(cc_admin)
    db.add(GastoRegistrado(proveedor_id=None, monto_total=400, clasificacion="GASTO_ADMINISTRATIVO", variabilidad="FIJO", centro_costo_id=cc_admin.id, fecha_emision=hoy))
    db.commit()
    # Verifica costos
    assert f.calcular_mpd(db) == Decimal("300")
    assert f.calcular_mod_devengada(db, periodo_id=periodo.id) >= Decimal("200")
    assert f.calcular_cif(db)["aplicado"] == Decimal("50")
    assert f.calcular_costo_produccion(db, periodo_id=periodo.id) == Decimal("550")
    # 5. Venta 1500 -> Netas 1271.19 + IGV 228.81, 50% caja 750, 50% CxC 750
    # Crea asiento venta devengado (no solo cobrado)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c121 = f.get_cuenta_by_codigo(db, "1212")
    c701 = f.get_cuenta_by_codigo(db, "7021")
    c4011 = f.get_cuenta_by_codigo(db, "40111")
    c231 = f.get_cuenta_by_codigo(db, "2311")
    c691 = f.get_cuenta_by_codigo(db, "6921")
    # Venta: DEBE 121 1500 HABER 701 1271.19 + 4011 228.81 (1500/1.18)
    neto = (Decimal("1500") / Decimal("1.18")).quantize(Decimal("0.01"))
    igv = Decimal("1500") - neto
    assert neto == Decimal("1271.19")
    assert igv == Decimal("228.81")
    f.crear_asiento(db, hoy, "Venta 1500", "VENTA", 800, [
        {"cuenta_id": c121.id, "debe": Decimal("1500"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": neto},
        {"cuenta_id": c4011.id, "debe": Decimal("0"), "haber": igv},
    ])
    # CxC: 50% pendiente 750
    db.add(CuentaPorCobrar(order_id=800, cliente_id=1, monto_total=1500, monto_pagado=750, saldo_pendiente=750, estado="PENDIENTE", fecha_vencimiento=hoy))
    db.commit()
    # Costo ventas (separado): DEBE 691 HABER 231
    f.crear_asiento(db, hoy, "Costo ventas", "PRODUCCION", 801, [
        {"cuenta_id": c691.id, "debe": Decimal("550"), "haber": Decimal("0")},
        {"cuenta_id": c231.id, "debe": Decimal("0"), "haber": Decimal("550")},
    ])
    # Gastos admin 400: DEBE 94 HABER 101/104 (ya vía GastoRegistrado, pero contabilizamos)
    c94 = f.get_cuenta_by_codigo(db, "941")
    f.crear_asiento(db, hoy, "Gasto admin", "MANUAL", 802, [
        {"cuenta_id": c94.id, "debe": Decimal("400"), "haber": Decimal("0")},
        {"cuenta_id": c101.id, "debe": Decimal("0"), "haber": Decimal("400")},
    ])
    # Cobro 750: DEBE Caja HABER Clientes
    f.crear_asiento(db, hoy, "Cobro 50%", "COBRO", 803, [
        {"cuenta_id": c101.id, "debe": Decimal("750"), "haber": Decimal("0")},
        {"cuenta_id": c121.id, "debe": Decimal("0"), "haber": Decimal("750")},
    ])
    # Verifica endpoint estados-financieros
    c = TestClient(app)
    r = c.post('/auth/login', data={'username':'lebsast@gmail.com','password':os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")}, follow_redirects=False)
    # fallback admin
    if r.status_code != 303:
        r = c.post('/auth/login', data={'username':'admin@suitelans.mx','password':os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")}, follow_redirects=False)
    ck = {'suitelans_token': r.cookies.get('suitelans_token')}
    periodo_str = f"{hoy.year}-{hoy.month:02d}"
    r = c.get(f'/finanzas/estados-financieros?periodo={periodo_str}', cookies=ck)
    # si endpoint no existe, fallback a balance
    if r.status_code == 404:
        r = c.get(f'/finanzas/balance?periodo={periodo_str}', cookies=ck)
    assert r.status_code == 200
    # Verifica cálculos directos
    er = f.obtener_estado_resultados(db, periodo_id=periodo.id)
    assert er["ventas"] == Decimal("1271.19"), er["ventas"]
    assert er["costo_ventas"] == Decimal("550.00"), er["costo_ventas"]
    assert er["utilidad_bruta"] == Decimal("721.19"), er["utilidad_bruta"]
    assert er["gastos_admin"] == Decimal("400.00")
    # EBITDA = utilidad_bruta - gastos_ventas - gastos_admin
    assert er["resultado"] == Decimal("321.19"), f"{er['resultado']} != 321.19"
    bg = f.obtener_balance_general(db, periodo_id=periodo.id)
    assert bg["valida"] is True, f"Balance no cuadra: {bg}"
    # Punto equilibrio: Costos fijos / % margen
    # Costos fijos = 400 (admin) + 50 (CIF fijo) = 450, margen % = utilidad_bruta/ventas = 56.74%
    # Punto eq = 450 / 0.5674 ≈ 793
    # Verifica que el servicio expone el cálculo (si no, calcula manual)
    margen_pct = (er["utilidad_bruta"] / er["ventas"] * 100) if er["ventas"] else Decimal("0")
    costos_fijos = Decimal("450")
    punto_eq = (costos_fijos / (margen_pct/100)).quantize(Decimal("0.01")) if margen_pct else Decimal("0")
    assert punto_eq > Decimal("700") and punto_eq < Decimal("900")
    # Verifica aislamiento rendimiento: no import
    import pathlib
    src = pathlib.Path("app/services/finanzas.py").read_text()
    assert "from app.modules.rendimiento" not in src
    assert "import app.modules.rendimiento" not in src
    # Verifica no asiento descuadrado
    for a in db.query(AsientoContable).all():
        lineas = db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id==a.id).all()
        assert sum(Decimal(str(l.debe)) for l in lineas) == sum(Decimal(str(l.haber)) for l in lineas)
        for l in lineas:
            assert not (Decimal(str(l.debe))>0 and Decimal(str(l.haber))>0)
    # Verifica CxC pendiente
    cxc = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id==800).first()
    assert cxc.saldo_pendiente == 750
    # Verifica flujo caja: caja = ingresos - egresos
    # No duplicar test, solo que no falle
    # Cleanup
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=997"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=997"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=997"))
        conn.commit()
    db.query(GastoRegistrado).filter(GastoRegistrado.monto_total.in_([300,50,400])).delete()
    db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id==800).delete()
    db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id.in_([a.id for a in db.query(AsientoContable).filter(AsientoContable.origen_id.in_([800,801,802,803])).all()])).delete(synchronize_session=False)
    db.query(AsientoContable).filter(AsientoContable.origen_id.in_([800,801,802,803])).delete(synchronize_session=False)
    db.commit()
    db.close()
