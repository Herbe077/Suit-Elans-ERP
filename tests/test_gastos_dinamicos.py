"""Gastos dinámicos: categorías nuevas, cuentas PCGE, centros y CxP total."""
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services.plan_operativo import cargar_plan_operativo
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    return db


def _mapa(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _limpia_gasto(db, gasto):
    from app.models.finanzas import (
        AsientoContable,
        CuentaPorPagar,
        GastoRegistrado,
        LineaAsientoContable,
    )
    aids = [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_id == gasto.id).all()]
    if aids:
        db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(aids)).delete(
            synchronize_session=False)
        db.query(AsientoContable).filter(
            AsientoContable.id.in_(aids)).delete(synchronize_session=False)
    db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == (gasto.numero_comprobante or f"GASTO-{gasto.id}")).delete(
        synchronize_session=False)
    db.query(GastoRegistrado).filter(GastoRegistrado.id == gasto.id).delete()
    db.commit()


def test_planilla_personal_6211_921_y_cxp():
    from app.services import finanzas as f
    from app.services.motor_contable import dim_centro
    db = _db()
    g, a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="PLANILLA_PERSONAL",
        monto_base=2000, numero_comprobante="PL-DIN-001",
        centro_costo_id=dim_centro(db, "921"))
    mapa = _mapa(db, a.id)
    assert mapa["6211"] == (Decimal("2000"), Decimal("0"))
    assert mapa["4111"] == (Decimal("0"), Decimal("2000"))
    lineas_9211 = [l for l in mapa if l == "6211"]
    assert lineas_9211
    # dims: centro 921 en la línea de gasto
    from app.models.finanzas import LineaAsientoContable
    lc = db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == a.id).all()
    assert any(l.centro_costo_id == dim_centro(db, "921") for l in lc)
    # CxP POR_PAGAR con saldo total (incluye planilla)
    from app.models.finanzas import CuentaPorPagar
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "PL-DIN-001").first()
    assert cxp is not None and cxp.estado == "POR_PAGAR"
    assert cxp.saldo_pendiente == 2000.0 and cxp.monto_pagado == 0.0
    _limpia_gasto(db, g)
    db.close()


def test_honorarios_terceros_rxh_retencion():
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    db = _db()
    sup = Supplier(nombre="Consultor Din", ruc="10444444444")
    db.add(sup)
    db.commit()
    g, a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="HONORARIOS_TERCEROS",
        monto_base=1000, numero_comprobante="RX-DIN-001",
        proveedor_id=sup.id)
    assert g.tipo_comprobante == "RECIBO_HONORARIOS"  # fijado por categoría
    assert Decimal(str(g.retencion)) == Decimal("80")  # 8% automático
    mapa = _mapa(db, a.id)
    assert mapa["6322"] == (Decimal("1000"), Decimal("0"))
    assert mapa["40172"] == (Decimal("0"), Decimal("80"))
    assert mapa["4241"] == (Decimal("0"), Decimal("920"))
    from app.models.finanzas import CuentaPorPagar
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "RX-DIN-001").first()
    assert cxp is not None and cxp.saldo_pendiente == 920.0
    _limpia_gasto(db, g)
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    db.commit()
    db.close()


def test_avios_publicidad_servicios_cuentas():
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    db = _db()
    sup = Supplier(nombre="Prov Din", ruc="20111111111")
    db.add(sup)
    db.commit()
    for cat, cuenta, total, igv in [
            ("COMPRAS_AVIOS_SUMINISTROS", "6032", 1180.0, 180.0),
            ("PUBLICIDAD_MARKETING", "6371", 1180.0, 180.0),
            ("SERVICIOS_BASICOS", "6361", 1180.0, 180.0)]:
        g, a = f.registrar_gasto_operativo(
            db, fecha=date.today(), categoria=cat, monto_base=1000,
            monto_igv=180, tipo_comprobante="FACTURA",
            numero_comprobante=f"DIN-{cat[:6]}", proveedor_id=sup.id)
        mapa = _mapa(db, a.id)
        assert mapa[cuenta] == (Decimal("1000"), Decimal("0")), cat
        assert mapa["40111"] == (Decimal("180"), Decimal("0")), cat
        from app.models.finanzas import CuentaPorPagar
        assert db.query(CuentaPorPagar).filter(
            CuentaPorPagar.numero_factura == f"DIN-{cat[:6]}").count() == 1
        _limpia_gasto(db, g)
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    db.commit()
    db.close()


def test_credito_y_contado_cxp():
    from app.models.finanzas import CuentaPorPagar, MovimientoFinanciero
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    from app.services import tesoreria_service as tes
    db = _db()
    sup = Supplier(nombre="Prov Cond", ruc="20222222222")
    db.add(sup)
    db.commit()
    # Crédito: POR_PAGAR, saldo total, sin caja
    g, _a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="ALQUILERES", monto_base=1000,
        monto_igv=180, tipo_comprobante="FACTURA",
        numero_comprobante="ALQ-COND-001", proveedor_id=sup.id)
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "ALQ-COND-001").one()
    assert cxp.estado == "POR_PAGAR" and cxp.saldo_pendiente == 1180.0
    assert cxp.monto_pagado == 0.0
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.comprobante_ref == "ALQ-COND-001").count() == 0
    # Contado (mismo gasto vía tesorería): PAGADO, saldo 0 + egreso
    r = tes.ejecutar_pago_proveedor(db, g.id, medio_pago="banco",
                                    monto=1180.0, permitir_sobregiro=True)
    db.refresh(cxp)
    assert cxp.estado == "PAGADO" and cxp.saldo_pendiente == 0.0
    assert r["asiento_id"]
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO",
        MovimientoFinanciero.comprobante_ref == "ALQ-COND-001").count() == 1
    _limpia_gasto(db, g)
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    db.commit()
    db.close()
