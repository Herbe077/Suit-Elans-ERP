"""Asientos partida doble: validación DEBE=HABER, líneas, cuentas activas"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from decimal import Decimal

def test_asiento_valido_y_descuadrado():
    from app.core.database import SessionLocal
    from app.models.finanzas import CuentaContable, PeriodoContable
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    # cuentas válidas
    c1 = f.get_cuenta_by_codigo(db, "1011")
    c2 = f.get_cuenta_by_codigo(db, "7021")
    assert c1 and c2
    # válido (hojas imputables)
    a = f.crear_asiento(db, __import__("datetime").date.today(), "Venta test", "VENTA", 1, [
        {"cuenta_id": c1.id, "debe": Decimal("118.00"), "haber": Decimal("0")},
        {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("100.00")},
        {"cuenta_id": f.get_cuenta_by_codigo(db, "40111").id, "debe": Decimal("0"), "haber": Decimal("18.00")},
    ])
    assert a.id
    # descuadrado debe fallar
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Mal", "MANUAL", 2, [
            {"cuenta_id": c1.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("5")},
        ])
        assert False, "debe fallar descuadrado"
    except ValueError as e:
        assert "descuadrado" in str(e).lower()
    # debe y haber simultáneos (con totals iguales para que no falle antes por descuadre)
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Mal2", "MANUAL", 3, [
            {"cuenta_id": c1.id, "debe": Decimal("10"), "haber": Decimal("10")},
            {"cuenta_id": c2.id, "debe": Decimal("5"), "haber": Decimal("0")},
            {"cuenta_id": f.get_cuenta_by_codigo(db, "40111").id, "debe": Decimal("0"), "haber": Decimal("5")},
        ])
        assert False
    except ValueError as e:
        assert "simultáneamente" in str(e)
    # importes negativos (total debe ser positivo, probamos línea negativa con total positivo)
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Mal3", "MANUAL", 4, [
            {"cuenta_id": c1.id, "debe": Decimal("15"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("-5"), "haber": Decimal("0")},
            {"cuenta_id": f.get_cuenta_by_codigo(db, "40111").id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError as e:
        assert "negativos" in str(e).lower() or "positivo" in str(e).lower()
    db.close()

def test_cuenta_inactiva_o_no_imputable():
    from app.core.database import SessionLocal, Base, engine
    from app.models.finanzas import CuentaContable
    from app.services import finanzas as f
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    c = f.get_cuenta_by_codigo(db, "10")  # agrupadora, no debe aceptar movimientos si marcamos
    # marca como no imputable
    c.acepta_movimientos = False
    db.commit()
    c2 = f.get_cuenta_by_codigo(db, "1011")
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Test", "MANUAL", 99, [
            {"cuenta_id": c.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError as e:
        assert "no activa" in str(e) or "imputable" in str(e)
    c.acepta_movimientos = True
    db.commit()
    # inactiva
    c.activo = False
    db.commit()
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Test2", "MANUAL", 100, [
            {"cuenta_id": c.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError:
        pass
    c.activo = True
    db.commit()
    # cuenta padre (no imputable) se rechaza aunque esté activa
    c10 = f.get_cuenta_by_codigo(db, "10")
    assert c10.imputable is False
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Padre", "MANUAL", 101, [
            {"cuenta_id": c10.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError as e:
        assert "imputable" in str(e)
    db.close()
