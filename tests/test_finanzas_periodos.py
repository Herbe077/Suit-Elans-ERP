"""Periodos contables: ABIERTO/CERRADO/BLOQUEADO, no contabilizar en cerrados"""
from datetime import date

def test_periodo_cierre_y_bloqueo():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    from app.models.finanzas import PeriodoContable, CuentaContable
    from decimal import Decimal
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    # crea periodo
    p = f.get_or_create_periodo(db, 2026, 9)
    assert p.estado == "ABIERTO"
    # crea asiento válido en abierto
    c1 = f.get_cuenta_by_codigo(db, "101")
    c2 = f.get_cuenta_by_codigo(db, "701")
    a = f.crear_asiento(db, date(2026,9,5), "Venta periodo abierto", "VENTA", 10, [
        {"cuenta_id": c1.id, "debe": Decimal("100"), "haber": Decimal("0")},
        {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("100")},
    ])
    assert a.periodo_id == p.id
    # cierra periodo
    f.cerrar_periodo(db, 2026, 9)
    p2 = f.get_periodo(db, 2026, 9)
    assert p2.estado == "CERRADO"
    # intenta crear asiento en cerrado -> debe fallar
    try:
        f.crear_asiento(db, date(2026,9,6), "Venta cerrado", "VENTA", 11, [
            {"cuenta_id": c1.id, "debe": Decimal("50"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("50")},
        ])
        assert False, "debe bloquear periodo cerrado"
    except ValueError as e:
        assert "ABIERTO" in str(e) or "CERRADO" in str(e)
    # reabrir
    f.reabrir_periodo(db, 2026, 9)
    p3 = f.get_periodo(db, 2026, 9)
    assert p3.estado == "ABIERTO"
    # ahora sí permite
    a2 = f.crear_asiento(db, date(2026,9,7), "Venta reabierto", "VENTA", 12, [
        {"cuenta_id": c1.id, "debe": Decimal("20"), "haber": Decimal("0")},
        {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("20")},
    ])
    assert a2.id
    # bloqueado
    p3.estado = "BLOQUEADO"
    db.commit()
    try:
        f.crear_asiento(db, date(2026,9,8), "Venta bloqueado", "VENTA", 13, [
            {"cuenta_id": c1.id, "debe": Decimal("10"), "haber": Decimal("0")},
            {"cuenta_id": c2.id, "debe": Decimal("0"), "haber": Decimal("10")},
        ])
        assert False
    except ValueError:
        pass
    # deja abierto para otros tests
    p3.estado = "ABIERTO"
    db.commit()
    db.close()
