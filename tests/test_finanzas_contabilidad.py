"""Invariantes contables básicas"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from decimal import Decimal

def test_no_asientos_descuadrados_y_mayor_coincide():
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c701 = f.get_cuenta_by_codigo(db, "7021")
    # crea dos asientos válidos
    a1 = f.crear_asiento(db, __import__("datetime").date.today(), "A1", "VENTA", 301, [
        {"cuenta_id": c101.id, "debe": Decimal("100"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("100")},
    ])
    a2 = f.crear_asiento(db, __import__("datetime").date.today(), "A2", "VENTA", 302, [
        {"cuenta_id": c101.id, "debe": Decimal("50"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("50")},
    ])
    # mayor coincide con asientos
    mayor = f.obtener_mayor(db)
    total_debe = sum(Decimal(str(l.debe)) for l in mayor)
    total_haber = sum(Decimal(str(l.haber)) for l in mayor)
    assert total_debe == total_haber
    assert total_debe >= Decimal("150")
    # verifica que no hay líneas con debe y haber simultáneos
    for l in mayor:
        assert not (Decimal(str(l.debe)) > 0 and Decimal(str(l.haber)) > 0)
        assert Decimal(str(l.debe)) >= 0 and Decimal(str(l.haber)) >= 0
    db.close()

def test_balance_general_activo_pasivo_patrimonio():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    bg = f.obtener_balance_general(db)
    # con datos de test, activo = pasivo + patrimonio_total o pasivo+patrimonio
    assert bg["valida"] is True
    db.close()
