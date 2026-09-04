"""PCGE: jerarquía, activa/inactiva, FK cuenta_id"""
def test_pcge_jerarquia_y_consulta():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    # agrupadora 10 no debe aceptar movimientos si se desactiva, pero nivel 1
    c10 = f.get_cuenta_by_codigo(db, "10")
    assert c10.nivel == 1
    assert c10.tipo == "ACTIVO"
    # imputable 101
    c101 = f.get_cuenta_by_codigo(db, "101")
    assert c101.cuenta_padre_id == c10.id
    assert c101.acepta_movimientos is True
    assert c101.activo is True
    # consulta por código y naturaleza
    assert f.get_cuenta_by_codigo(db, "701").tipo == "INGRESO"
    assert f.get_cuenta_by_codigo(db, "601").tipo == "GASTO"
    # acumulación saldos vía balance
    from decimal import Decimal
    # crea asiento que afecta 101 y 701
    f.crear_asiento(db, __import__("datetime").date.today(), "Test acum", "MANUAL", 200, [
        {"cuenta_id": c101.id, "debe": Decimal("50"), "haber": Decimal("0")},
        {"cuenta_id": f.get_cuenta_by_codigo(db, "701").id, "debe": Decimal("0"), "haber": Decimal("50")},
    ])
    bal = f.obtener_balance_comprobacion(db)
    # debe haber al menos esas cuentas con saldos
    codigos = [r["codigo"] for r in bal]
    assert "101" in codigos
    assert "701" in codigos
    # jerarquía: cuenta padre no debe tener movimientos directos si acepta false (test)
    c10.acepta_movimientos = False
    db.commit()
    try:
        f.crear_asiento(db, __import__("datetime").date.today(), "Padre no imputable", "MANUAL", 201, [
            {"cuenta_id": c10.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c101.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError:
        pass
    c10.acepta_movimientos = True
    db.commit()
    db.close()

def test_cuenta_fk_obligatoria():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
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
