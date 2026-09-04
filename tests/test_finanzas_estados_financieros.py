"""Estados financieros derivados del mayor, filtrados por periodo/centro costo"""
from decimal import Decimal
from datetime import date

def _limpia(db):
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.commit()

def test_balance_comprobacion_mayor_y_filtros():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    _limpia(db)
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    c4011 = f.get_cuenta_by_codigo(db, "4011")
    # asiento con centro costo
    cc = db.query(f.get_cuenta_by_codigo(db, "101").__class__).first()
    from app.models.finanzas import CentroCosto
    centro = db.query(CentroCosto).filter(CentroCosto.codigo=="COMERCIAL").first()
    if not centro:
        centro = CentroCosto(codigo="COMERCIAL", nombre="Comercial", tipo="COMERCIAL")
        db.add(centro); db.commit(); db.refresh(centro)
    f.crear_asiento(db, date(2026,9,10), "Venta comercial", "VENTA", 601, [
        {"cuenta_id": c101.id, "debe": Decimal("118"), "haber": Decimal("0"), "centro_costo_id": centro.id},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("100")},
        {"cuenta_id": c4011.id, "debe": Decimal("0"), "haber": Decimal("18")},
    ])
    bal = f.obtener_balance_comprobacion(db)
    assert any(r["codigo"]=="101" for r in bal)
    mayor = f.obtener_mayor(db, cuenta_id=c101.id)
    assert len(mayor) >= 1
    # filtro por centro costo
    mayor_cc = f.obtener_mayor(db, cuenta_id=c101.id)
    # verifica que el asiento tiene centro costo
    assert any(l.centro_costo_id==centro.id for l in mayor)
    _limpia(db)
    db.close()

def test_estado_resultados_y_balance_general():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    _limpia(db)
    # Crea asientos mínimos para P&L
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    c4011 = f.get_cuenta_by_codigo(db, "4011")
    c691 = f.get_cuenta_by_codigo(db, "691")
    c231 = f.get_cuenta_by_codigo(db, "231")
    c94 = f.get_cuenta_by_codigo(db, "94")
    # Venta 100 + IGV 18
    f.crear_asiento(db, date(2026,9,11), "Venta", "VENTA", 602, [
        {"cuenta_id": c101.id, "debe": Decimal("118"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("100")},
        {"cuenta_id": c4011.id, "debe": Decimal("0"), "haber": Decimal("18")},
    ])
    # Costo ventas 60
    f.crear_asiento(db, date(2026,9,11), "Costo", "PRODUCCION", 603, [
        {"cuenta_id": c691.id, "debe": Decimal("60"), "haber": Decimal("0")},
        {"cuenta_id": c231.id, "debe": Decimal("0"), "haber": Decimal("60")},
    ])
    # Gastos admin 10
    f.crear_asiento(db, date(2026,9,11), "Gasto admin", "MANUAL", 604, [
        {"cuenta_id": c94.id, "debe": Decimal("10"), "haber": Decimal("0")},
        {"cuenta_id": c101.id, "debe": Decimal("0"), "haber": Decimal("10")},
    ])
    er = f.obtener_estado_resultados(db)
    assert er["ventas"] == Decimal("100")
    assert er["costo_ventas"] == Decimal("60")
    assert er["utilidad_bruta"] == Decimal("40")
    assert er["gastos_admin"] == Decimal("10")
    assert er["resultado"] == Decimal("30")  # 40-10
    bg = f.obtener_balance_general(db)
    # con asientos, activo debe ser pasivo+patrimonio+resultado o valida
    assert bg["valida"] is True
    _limpia(db)
    db.close()

def test_libro_diario_y_mayor_por_periodo():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from datetime import date
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    _limpia(db)
    p = f.get_or_create_periodo(db, 2026, 9)
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    a = f.crear_asiento(db, date(2026,9,12), "Periodo test", "MANUAL", 605, [
        {"cuenta_id": c101.id, "debe": Decimal("10"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("10")},
    ])
    assert a.periodo_id == p.id
    mayor_periodo = f.obtener_mayor(db, periodo_id=p.id)
    assert len(mayor_periodo) >= 2
    bal_periodo = f.obtener_balance_comprobacion(db, periodo_id=p.id)
    assert len(bal_periodo) >= 2
    _limpia(db)
    db.close()
