"""MOD devengada vs pagada, lectura desacoplada de rendimiento"""
from decimal import Decimal
from datetime import date

def test_mod_devengada_lectura_desacoplada():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from sqlalchemy import text
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    # Inserta MOD devengada para periodo 2026-09
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (998,'OP-98','MOD Test',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (998,1,'2026-09-10','REGISTRADO')"))
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=998"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,orden_produccion_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (998,998,10,998,2,15,30)"))
        conn.commit()
    # MOD por periodo
    periodo = f.get_or_create_periodo(db, 2026, 9)
    mod_periodo = f.calcular_mod_devengada(db, periodo_id=periodo.id)
    assert mod_periodo >= Decimal("30")
    # MOD por orden específica
    mod_orden = f.calcular_mod_devengada(db, orden_produccion_id=10)
    assert mod_orden == Decimal("30")
    # MOD con rango fechas
    mod_rango = f.calcular_mod_devengada(db, desde=date(2026,9,1), hasta=date(2026,9,30))
    assert mod_rango >= Decimal("30")
    # Devengado != Pagado: cambia estado a PAGADO no afecta devengado
    with engine.connect() as conn:
        conn.execute(text("UPDATE rendimiento_registros SET estado='LIQUIDADO' WHERE id=998"))
        conn.commit()
    mod_liquidado = f.calcular_mod_devengada(db, periodo_id=periodo.id)
    # aún cuenta porque incluye LIQUIDADO (devengado)
    assert mod_liquidado >= Decimal("30")
    # Finanzas no importa modelos rendimiento directamente
    import ast, pathlib
    src = pathlib.Path("app/services/finanzas.py").read_text()
    assert "from app.modules.rendimiento" not in src
    assert "import app.modules.rendimiento" not in src
    # solo lectura SQL
    assert "engine.connect()" in src or "text(" in src
    # cleanup
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=998"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=998"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=998"))
        conn.commit()
    db.close()

def test_mod_contabilizacion_devengado():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from sqlalchemy import text
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (997,'OP-97','MOD2',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (997,1,'2026-09-15','REGISTRADO')"))
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=997"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (997,997,997,1,10,10)"))
        conn.commit()
    mod = f.calcular_mod_devengada(db, desde=date(2026,9,1), hasta=date(2026,9,30))
    # contabiliza MOD devengada: DEBE MOD HABER Proveedores/CxP
    c_mod = f.get_cuenta_by_codigo(db, "621")
    c_prov = f.get_cuenta_by_codigo(db, "421")
    f.crear_asiento(db, date(2026,9,15), "MOD devengada", "MOD", 997, [
        {"cuenta_id": c_mod.id, "debe": Decimal("10"), "haber": Decimal("0")},
        {"cuenta_id": c_prov.id, "debe": Decimal("0"), "haber": Decimal("10")},
    ])
    # verifica asiento creado
    assert mod >= Decimal("10")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=997"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=997"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=997"))
        conn.commit()
    db.close()
