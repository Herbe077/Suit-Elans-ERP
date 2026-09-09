"""Regresión de las mejoras enterprise: seguridad, flujo financiero y ACID."""
from datetime import date, timedelta
from decimal import Decimal


def test_gasto_guarda_vencimiento_y_actividad(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.finanzas import GastoRegistrado
    db = SessionLocal()
    try:
        from app.services import finanzas as f
        g, _ = f.registrar_gasto_operativo(
            db, fecha=date(2026, 9, 8), categoria="ACTIVO_FIJO",
            monto_base=1000, monto_igv=180,
            numero_comprobante="HF-001", fecha_vencimiento=date(2026, 10, 8))
        assert g.fecha_vencimiento == date(2026, 10, 8)
        assert g.actividad_flujo == "INVERSION"
    finally:
        db.query(GastoRegistrado).filter(GastoRegistrado.numero_comprobante == "HF-001").delete()
        db.commit()
        db.close()


def test_gasto_con_centro_de_costo_usa_791():
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable, CentroCosto
    from app.services import finanzas as f
    db = SessionLocal()
    try:
        cc = CentroCosto(codigo="QA-ENT", nombre="QA Enterprise", tipo="TALLER", activo=True)
        db.add(cc); db.flush()
        g, _ = f.registrar_gasto_operativo(
            db, fecha=date.today(), categoria="PLANILLA", monto_base=100,
            centro_costo_id=cc.id, numero_comprobante="QA-7911")
        asiento = db.query(AsientoContable).filter(
            AsientoContable.origen_id == g.id,
            AsientoContable.glosa.like("Destino%"),
        ).one()
        cuentas = {l.cuenta.codigo for l in asiento.lineas}
        assert "9211" in cuentas and "791" in cuentas
    finally:
        db.rollback()
        db.close()


def test_normalizacion_origen_cxp_es_cerrada():
    from app.services.finanzas import normalizar_origen_cxp
    assert normalizar_origen_cxp("COMPRAS") == "PROVEEDORES MATERIA PRIMA"
    assert normalizar_origen_cxp("GASTOS_OPERATIVOS") == "GASTOS OPERATIVOS"
    assert normalizar_origen_cxp("DESTAJO") == "SERVICIOS TERCERIZADOS"
    assert normalizar_origen_cxp("INVALIDO") == "GASTOS OPERATIVOS"
