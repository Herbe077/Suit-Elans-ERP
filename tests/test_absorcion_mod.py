"""Absorción automática de MOD por pedido terminado (2311 vs 9211).

- Minutos: tareo → sam de ficha → SAM estándar por tipo de prenda.
- absorber_mod_pedido: 2311/9211 idempotente por pedido.
- Entrega/factura: COGS 6921/2311 = materiales + MOD absorbida (WIP en cero).
- Rentabilidad muestra M.O. aunque no haya tareo (fallback SAM).
"""
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services.plan_operativo import cargar_plan_operativo
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    return db


def _limpia(db, tag):
    from app.models.billing import CashMovement
    from app.models.finanzas import (AsientoContable, CuentaPorPagar,
                                     GastoRegistrado, LineaAsientoContable,
                                     MovimientoFinanciero)
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.order import Garment, Order, WorkLog
    from app.models.catalog import Product, ProductVariant
    from app.models.client import Client
    from app.models.inventory import StockMovement
    for o in db.query(Order).filter(Order.folio.like(f"SE-ABS-{tag}%")).all():
        for g in db.query(Garment).filter(Garment.order_id == o.id).all():
            db.query(WorkLog).filter(WorkLog.garment_id == g.id).delete()
            db.query(Garment).filter(Garment.id == g.id).delete()
        for a in db.query(AsientoContable).filter(
                AsientoContable.origen_id == o.id).all():
            db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == a.id).delete(
                synchronize_session=False)
            db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
        db.query(MovimientoKardex).filter(
            MovimientoKardex.orden_venta_id == o.id).delete()
        db.query(Order).filter(Order.id == o.id).delete()
    db.query(Client).filter(Client.nro_doc.like(f"ABS-{tag}%")).delete(
        synchronize_session=False)
    db.query(ProductoInsumo).filter(ProductoInsumo.sku.like(f"ABS-{tag}%")).delete(
        synchronize_session=False)
    db.commit()


def _pedido(db, tag, tipo="saco", total=1180.0, sam_est=0.0):
    from app.models.client import Client
    from app.models.order import Garment, Order
    c = Client(nombre="Abs", apellidos=tag, tipo_doc="DNI",
               nro_doc=f"ABS-{tag}", clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=f"SE-ABS-{tag}", client_id=c.id, estado="confirmado",
              canal="sastreria", total=total, anticipo=total)
    db.add(o)
    db.commit()
    g = Garment(order_id=o.id, tipo=tipo, precio=total,
                estado_taller="CALIDAD_OK", sam_estimado=sam_est)
    db.add(g)
    db.commit()
    return o.id, g.id


def _mapa(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        out[db.get(CuentaContable, l.cuenta_id).codigo] = (
            Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def test_minutos_cadena_tareo_sam_estandar():
    from app.models.order import Operation, WorkLog
    from app.services import finanzas as f
    db = _db()
    oid, gid = _pedido(db, "T1")
    try:
        # Sin tareo ni sam: SAM estándar del saco (600 min).
        minutos, fuente = f.minutos_mod_pedido(db, oid)
        assert minutos == 600.0 and fuente == "sam_estandar"
        # Sam de ficha prevalece sobre el estándar.
        from app.models.order import Garment
        db.get(Garment, gid).sam_estimado = 120.0
        db.commit()
        assert f.minutos_mod_pedido(db, oid) == (120.0, "sam")
        # Tareo real prevalece sobre todo.
        op = db.query(Operation).filter(Operation.codigo == "ABS-OP").first()
        if not op:
            op = Operation(codigo="ABS-OP", nombre="Op abs",
                           tipo_prenda="saco", sam_minutos=30.0)
            db.add(op)
            db.flush()
        db.add(WorkLog(garment_id=gid, operation_id=op.id,
                       minutos_reales=45.0, estado="terminado"))
        db.commit()
        assert f.minutos_mod_pedido(db, oid) == (45.0, "tareo")
    finally:
        _limpia(db, "T1")
        db.close()


def test_absorcion_2311_vs_9211_idempotente():
    from app.models.finanzas import AsientoContable
    from app.services import contabilidad as c
    db = _db()
    oid, _gid = _pedido(db, "T2")
    try:
        r = c.absorber_mod_pedido(db, oid, None)
        # 600 min × 0.35 (sin planilla en test) = 210.
        assert r["monto"] == 210.0 and r["fuente"] == "sam_estandar"
        assert r["asiento_id"] is not None
        mapa = _mapa(db, r["asiento_id"])
        assert mapa["2311"] == (Decimal("210"), Decimal("0"))
        assert mapa["9211"] == (Decimal("0"), Decimal("210"))
        assert c.monto_mod_absorbido(db, oid) == 210.0
        n = db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "ABSORCION_MOD",
            AsientoContable.origen_id == oid).count()
        r2 = c.absorber_mod_pedido(db, oid, None)
        assert r2.get("existente") is True
        assert db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "ABSORCION_MOD",
            AsientoContable.origen_id == oid).count() == n == 1
    finally:
        _limpia(db, "T2")
        db.close()


def test_entrega_absorbe_y_costo_cuadra_wip_cero():
    from app.models.finanzas import AsientoContable
    from app.services import contabilidad as c
    from app.services import ventas as v
    db = _db()
    oid, _gid = _pedido(db, "T3", tipo="pantalon")
    try:
        # Materiales: 1.4 m × 50 = 70 al WIP.
        from app.models.inventario import ProductoInsumo
        from app.services.inventory import reservar_insumo
        p = ProductoInsumo(sku="ABS-T3", nombre="Tela T3",
                           categoria="TELA_PRINCIPAL", unidad_medida="METROS",
                           costo_unitario=50.0, costo_promedio=50.0,
                           stock_fisico=20.0)
        db.add(p)
        db.commit()
        reservar_insumo(db, p.id, 1.4, orden_venta_id=oid, usuario_id=None)
        db.commit()
        from app.models.inventario import MovimientoKardex
        from app.services.compras_kardex import asiento_consumo_flush
        asiento = asiento_consumo_flush(db, p, 1.4, 50.0,
                                        "Consumo test T3", date.today())
        asiento_consumo_id = asiento.id if asiento else None
        p.stock_fisico = round(float(p.stock_fisico) - 1.4, 2)
        db.add(MovimientoKardex(
            producto_id=p.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
            cantidad=1.4, costo_unitario=50.0, costo_total=70.0,
            saldo_fisico=float(p.stock_fisico),
            saldo_valorizado=round(float(p.stock_fisico) * 50.0, 2),
            orden_venta_id=oid,
            asiento_id=asiento.id if asiento else None,
            doc_ref="ABS-T3", observacion="Consumo test T3"))
        db.commit()
        # Pantalón: 195 min estándar × 0.35 = 68.25; total 138.25.
        v.entregar(db, oid)
        costos = db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "COSTO_VENTAS",
            AsientoContable.origen_id == oid).all()
        assert len(costos) == 1
        assert _mapa(db, costos[0].id)["6921"] == (Decimal("138.25"), Decimal("0"))
        assert db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "ABSORCION_MOD",
            AsientoContable.origen_id == oid).count() == 1
        # WIP 2311 del pedido: +70 consumo +68.25 absorción −138.25 costo = 0.
        debe_2311 = Decimal("0")
        haber_2311 = Decimal("0")
        aids = [asiento_consumo_id] + [
            a.id for a in db.query(AsientoContable).filter(
                AsientoContable.origen_tipo.in_(
                    ["ABSORCION_MOD", "COSTO_VENTAS"]),
                AsientoContable.origen_id == oid).all()]
        for aid in aids:
            if not aid:
                continue
            for cod, (d, h) in _mapa(db, aid).items():
                if cod == "2311":
                    debe_2311 += d
                    haber_2311 += h
        assert debe_2311 == Decimal("138.25") and haber_2311 == Decimal("138.25")
    finally:
        _limpia(db, "T3")
        db.close()


def test_rentabilidad_muestra_mo_sam_estandar(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    _pedido(db, "T4")
    o = db.query(Order).filter(Order.folio == "SE-ABS-T4").one()
    from app.routers.finanzas import _fila_rentabilidad
    # Saco sin tareo: 600 min × 0.35 = 210 (antes mostraba S/ 0.00).
    fila = _fila_rentabilidad(db, o, 40.0, 0.35)
    assert fila["costo_destajo"] == 210.0
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    assert "SE-ABS-T4" in t
    db = SessionLocal()
    _limpia(db, "T4")
    db.close()
