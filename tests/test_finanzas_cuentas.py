"""PCGE: jerarquía, activa/inactiva, FK cuenta_id"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
def test_pcge_jerarquia_y_consulta():
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    # agrupadora 10 no debe aceptar movimientos si se desactiva, pero nivel 1
    c10 = f.get_cuenta_by_codigo(db, "10")
    assert c10.nivel == 1
    assert c10.tipo == "ACTIVO"
    # imputable 101
    c101 = f.get_cuenta_by_codigo(db, "101")
    assert c101.cuenta_padre_id == c10.id
    assert c101.acepta_movimientos is True
    assert c101.activo is True
    # consulta por código y naturaleza (hojas del plan operativo)
    assert f.get_cuenta_by_codigo(db, "7021").tipo == "INGRESO"
    assert f.get_cuenta_by_codigo(db, "6021").tipo == "GASTO"
    c1011 = f.get_cuenta_by_codigo(db, "1011")
    assert c1011.imputable is True and c1011.padre_codigo == "101"
    # acumulación saldos vía balance
    from decimal import Decimal
    # crea asiento que afecta 1011 y 7021
    f.crear_asiento(db, __import__("datetime").date.today(), "Test acum", "MANUAL", 200, [
        {"cuenta_id": c1011.id, "debe": Decimal("50"), "haber": Decimal("0")},
        {"cuenta_id": f.get_cuenta_by_codigo(db, "7021").id, "debe": Decimal("0"), "haber": Decimal("50")},
    ])
    bal = f.obtener_balance_comprobacion(db)
    # debe haber al menos esas cuentas con saldos
    codigos = [r["codigo"] for r in bal]
    assert "1011" in codigos
    assert "7021" in codigos
    # jerarquía: cuenta no imputable rechaza movimientos directos
    assert c10.imputable is False
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Padre no imputable", "MANUAL", 201, [
            {"cuenta_id": c10.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c1011.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError as e:
        assert "imputable" in str(e)
    c10.acepta_movimientos = True
    db.commit()
    db.close()

def test_cuenta_fk_obligatoria():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    c101 = f.get_cuenta_by_codigo(db, "101")
    # cuenta inexistente debe fallar
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Cuenta inexistente", "MANUAL", 202, [
            {"cuenta_id": 99999, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c101.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError:
        pass
    db.close()
