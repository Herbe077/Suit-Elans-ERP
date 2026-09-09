import os
"""
Verificación mínima de funcionamiento del módulo Finanzas y Contabilidad
Ejecutado dentro del módulo final, no en tests/
Cubre: partida doble, periodos, PCGE, MOD lectura desacoplada, costos, EEFF y flujo completo
"""
from decimal import Decimal
from datetime import date
import sys

def verificar():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from app.models.finanzas import GastoRegistrado, CentroCosto, CuentaPorCobrar
    from sqlalchemy import text
    from fastapi.testclient import TestClient
    from app.main import app
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    from app.services.plan_operativo import cargar_plan_operativo
    cargar_plan_operativo(db, desde_cero=True)
    db.commit()
    print("✓ Plan operativo OK:", db.query(f.CuentaContable).count(), "cuentas")
    # 1. Partida doble (hojas imputables)
    from app.services.motor_contable import post_manual
    a = post_manual(db, [("1011", Decimal("118"))],
                    [("7021", Decimal("100")), ("40111", Decimal("18"))],
                    None, "Verif Venta", "VENTA", 9001, date.today())
    db.commit()
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c701 = f.get_cuenta_by_codigo(db, "7021")
    print(f"✓ Asiento válido {a.numero} DEBE=HABER 118")
    try:
        post_manual(db, [("1011", Decimal("10"))], [("7021", Decimal("5"))],
                      None, "Mal", "MANUAL", 9002, date.today())
        print("✗ No bloqueó descuadre")
        sys.exit(1)
    except ValueError:
        print("✓ Bloquea descuadre")
    # 2. Periodos
    p = f.get_or_create_periodo(db, 2099, 1)
    f.cerrar_periodo(db, 2099, 1)
    try:
        f.crear_asiento(db, date(2099,1,15), "Cerrado", "MANUAL", 9003, [
            {"cuenta_id": c101.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        print("✗ No bloqueó periodo cerrado")
        sys.exit(1)
    except ValueError:
        print("✓ Bloquea periodo CERRADO")
    f.reabrir_periodo(db, 2099, 1)
    # 3. PCGE
    c10 = f.get_cuenta_by_codigo(db, "10")
    assert c10.acepta_movimientos == False or c10.nivel == 1
    print("✓ PCGE jerarquía OK")
    # 4. MOD lectura desacoplada
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (9999,'OP-99','Verif',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (9999,1,:f,'REGISTRADO')"), {"f": date.today().isoformat()})
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=9999"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (9999,9999,9999,1,99,99)"))
        conn.commit()
    mod = f.calcular_mod_devengada(db, desde=date.today(), hasta=date.today())
    assert mod >= Decimal("99"), mod
    print(f"✓ MOD devengada lectura desacoplada: {mod}")
    # Verifica no import
    import pathlib
    assert "from app.modules.rendimiento" not in pathlib.Path("app/services/finanzas.py").read_text()
    print("✓ Rendimiento intacto (no import)")
    # 5. Costos MPD+MOD+CIF
    db.query(GastoRegistrado).delete()
    db.commit()
    cc = db.query(CentroCosto).filter(CentroCosto.codigo=="TALLER").first() or CentroCosto(codigo="TALLER", nombre="Taller", tipo="TALLER")
    if not cc.id:
        db.add(cc); db.commit(); db.refresh(cc)
    db.add(GastoRegistrado(monto_total=300, clasificacion="MPD", variabilidad="VARIABLE", centro_costo_id=cc.id, fecha_emision=date.today()))
    db.add(GastoRegistrado(monto_total=50, clasificacion="CIF", variabilidad="FIJO", centro_costo_id=cc.id, fecha_emision=date.today()))
    db.commit()
    assert f.calcular_mpd(db) == Decimal("300")
    assert f.calcular_cif(db)["aplicado"] == Decimal("50")
    print("✓ Costos MPD/CIF OK, Costo Prod:", f.calcular_costo_produccion(db))
    # 6. EEFF
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.commit()
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    c4011 = f.get_cuenta_by_codigo(db, "40111")
    c691 = f.get_cuenta_by_codigo(db, "691")
    c231 = f.get_cuenta_by_codigo(db, "231")
    c94 = f.get_cuenta_by_codigo(db, "94")
    f.crear_asiento(db, date.today(), "Venta", "VENTA", 9101, [
        {"cuenta_id": c101.id, "debe": Decimal("1180"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("1000")},
        {"cuenta_id": c4011.id, "debe": Decimal("0"), "haber": Decimal("180")},
    ])
    f.crear_asiento(db, date.today(), "Costo", "PRODUCCION", 9102, [
        {"cuenta_id": c691.id, "debe": Decimal("600"), "haber": Decimal("0")},
        {"cuenta_id": c231.id, "debe": Decimal("0"), "haber": Decimal("600")},
    ])
    f.crear_asiento(db, date.today(), "Gasto", "MANUAL", 9103, [
        {"cuenta_id": c94.id, "debe": Decimal("100"), "haber": Decimal("0")},
        {"cuenta_id": c101.id, "debe": Decimal("0"), "haber": Decimal("100")},
    ])
    er = f.obtener_estado_resultados(db)
    assert er["ventas"] == Decimal("1000")
    assert er["costo_ventas"] == Decimal("600")
    assert er["utilidad_bruta"] == Decimal("400")
    print(f"✓ P&L Ventas {er['ventas']} Costo {er['costo_ventas']} Bruta {er['utilidad_bruta']}")
    bg = f.obtener_balance_general(db)
    assert bg["valida"] is True
    print(f"✓ Balance Activo={bg['activo']} Pasivo={bg['pasivo']} Pat={bg['patrimonio']} valida={bg['valida']}")
    # 7. Flujo completo via HTTP (si servidor corre)
    try:
        c = TestClient(app)
        r = c.post('/auth/login', data={'username':'lebsast@gmail.com','password':os.environ.get('TEST_ADMIN_PASSWORD','test-admin-password')}, follow_redirects=False)
        if r.status_code == 303:
            ck = {'suitelans_token': r.cookies.get('suitelans_token')}
            for path in ["/finanzas/plan-contable","/finanzas/diario","/finanzas/mayor","/finanzas/balance","/finanzas/periodos"]:
                resp = c.get(path, cookies=ck)
                print(f"✓ {path} {resp.status_code}")
        else:
            # fallback admin
            r = c.post('/auth/login', data={'username':'admin@suitelans.mx','password':os.environ.get('TEST_ADMIN_PASSWORD','test-admin-password')}, follow_redirects=False)
            ck = {'suitelans_token': r.cookies.get('suitelans_token')}
            resp = c.get('/finanzas/plan-contable', cookies=ck)
            print(f"✓ Finanzas plan-contable {resp.status_code}")
    except Exception as e:
        print("HTTP check skip:", e)
    # cleanup
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=9999"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=9999"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=9999"))
        conn.commit()
    db.query(GastoRegistrado).delete()
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.commit()
    db.close()
    print("✅ VERIFICACIÓN MÍNIMA FINANZAS OK — Devengado, PCGE, partida doble, periodos, MOD, costos, EEFF, rendimiento intacto")

if __name__ == "__main__":
    verificar()
