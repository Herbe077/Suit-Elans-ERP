"""Costos: MPD + MOD + CIF = Costo producción, y costo ventas separado de ingreso"""
from decimal import Decimal

def test_costo_produccion_mpd_mod_cif():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from app.models.finanzas import GastoRegistrado, CentroCosto
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    # limpia gastos previos para test aislado
    db.query(GastoRegistrado).delete()
    db.commit()
    # crea centro costo taller
    cc = db.query(CentroCosto).filter(CentroCosto.codigo=="TALLER").first()
    if not cc:
        cc = CentroCosto(codigo="TALLER", nombre="Taller", tipo="TALLER")
        db.add(cc); db.commit(); db.refresh(cc)
    # MPD
    db.add(GastoRegistrado(proveedor_id=None, monto_total=100, clasificacion="MPD", variabilidad="VARIABLE", centro_costo_id=cc.id, fecha_emision=__import__("datetime").date.today()))
    # CIF
    db.add(GastoRegistrado(proveedor_id=None, monto_total=30, clasificacion="CIF", variabilidad="FIJO", centro_costo_id=cc.id, fecha_emision=__import__("datetime").date.today()))
    db.commit()
    # MOD devengada: inserta rendimiento directo vía SQL (simula devengado)
    from sqlalchemy import text
    from app.core.database import engine as eng
    with eng.connect() as conn:
        # asegura tablas rendimiento existen
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (999,'OP-99','Test MOD',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (999,1,'2026-09-01','REGISTRADO')"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_detalles (id,registro_jornada_id,orden_produccion_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (999,999,1,999,1,20,20)"))
        conn.commit()
    mpd = f.calcular_mpd(db)
    mod = f.calcular_mod_devengada(db)
    cif = f.calcular_cif(db)["aplicado"]
    costo = f.calcular_costo_produccion(db)
    assert mpd == Decimal("100")
    assert mod == Decimal("20")
    assert cif == Decimal("30")
    assert costo == Decimal("150")
    # costo ventas separado de ingreso
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    c691 = f.get_cuenta_by_codigo(db, "691")
    c231 = f.get_cuenta_by_codigo(db, "231")
    # Simula venta: ingreso + costo ventas
    # Ingreso
    f.crear_asiento(db, __import__("datetime").date.today(), "Venta costo test", "VENTA", 500, [
        {"cuenta_id": c101.id, "debe": Decimal("118"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("100")},
        {"cuenta_id": f.get_cuenta_by_codigo(db, "4011").id, "debe": Decimal("0"), "haber": Decimal("18")},
    ])
    # Costo ventas (separado)
    f.crear_asiento(db, __import__("datetime").date.today(), "Costo ventas", "PRODUCCION", 501, [
        {"cuenta_id": c691.id, "debe": Decimal("80"), "haber": Decimal("0")},
        {"cuenta_id": c231.id, "debe": Decimal("0"), "haber": Decimal("80")},
    ])
    cventas = f.calcular_costo_ventas(db)
    assert cventas >= Decimal("80")
    # margen no es ventas - compras
    from app.services.finanzas import obtener_estado_resultados
    er = obtener_estado_resultados(db)
    assert er["ventas"] >= Decimal("100")
    assert er["costo_ventas"] >= Decimal("80")
    assert er["utilidad_bruta"] == er["ventas"] - er["costo_ventas"]
    # cleanup
    with eng.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=999"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=999"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=999"))
        conn.commit()
    db.query(GastoRegistrado).delete()
    db.commit()
    db.close()
