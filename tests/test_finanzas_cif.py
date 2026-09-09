"""CIF real vs aplicado, bases de distribución extensibles"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from decimal import Decimal

def test_cif_real_aplicado_y_bases():
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    from app.models.finanzas import GastoRegistrado, CentroCosto
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    db.query(GastoRegistrado).delete()
    db.commit()
    cc = db.query(CentroCosto).filter(CentroCosto.codigo=="TALLER").first()
    if not cc:
        cc = CentroCosto(codigo="TALLER", nombre="Taller", tipo="TALLER")
        db.add(cc); db.commit(); db.refresh(cc)
    # CIF real
    db.add(GastoRegistrado(proveedor_id=None, monto_total=200, clasificacion="CIF", variabilidad="FIJO", centro_costo_id=cc.id, fecha_emision=__import__("datetime").date.today()))
    db.commit()
    cif = f.calcular_cif(db)
    assert cif["real"] == Decimal("200")
    assert cif["aplicado"] == Decimal("200")
    # bases extensibles: costo_mod, horas_mod, etc. (no debe fallar con base distinta)
    for base in ["horas_mod","costo_mod","horas_maquina","unidades_producidas"]:
        c = f.calcular_cif(db, base=base)
        assert c["base"] == base
        assert c["real"] == Decimal("200")
    db.query(GastoRegistrado).delete()
    db.commit()
    db.close()

def test_cif_no_unica_base_rigida():
    from app.core.database import SessionLocal, Base, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # verifica que el modelo permite diferentes bases sin asumir una única
    import inspect
    sig = inspect.signature(f.calcular_cif)
    assert "base" in sig.parameters
    db.close()
