"""Coherencia ERP: espejos, VENCIDO_EN_TALLER, turno en abonos, IGV, MOD ref, cierre, depreciación."""
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    return db


def test_espejo_compras_idempotente():
    from app.models.purchasing import PurchaseOrder, Supplier
    from app.models.inventario import OrdenCompra
    from app.services.purchasing import sincronizar_espejo_compras
    db = _db()
    sup = db.query(Supplier).first()
    if not sup:
        sup = Supplier(nombre="COH-Sup"); db.add(sup); db.commit(); db.refresh(sup)
    po = PurchaseOrder(folio="COH-OC-001", supplier_id=sup.id, estado="borrador", total=500)
    db.add(po); db.commit()
    r1 = sincronizar_espejo_compras(db)
    assert r1["oc"] >= 1
    assert db.query(OrdenCompra).filter(OrdenCompra.folio == "COH-OC-001").first() is not None
    r2 = sincronizar_espejo_compras(db)
    assert r2 == {"oc": 0, "po": 0}
    assert db.query(OrdenCompra).filter(OrdenCompra.folio == "COH-OC-001").count() == 1
    db.query(OrdenCompra).filter(OrdenCompra.folio == "COH-OC-001").delete()
    db.query(PurchaseOrder).filter(PurchaseOrder.folio == "COH-OC-001").delete()
    db.commit(); db.close()


def test_espejo_ventas_idempotente():
    from app.models.order import Order
    from app.models.ventas import OrdenVenta
    from app.services.ventas import sincronizar_espejo_ventas
    db = _db()
    o = Order(folio="COH-OV-001", total=1000, anticipo=0, estado="cotizado")
    db.add(o); db.commit(); db.refresh(o)
    sincronizar_espejo_ventas(db)
    ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == o.id).first()
    assert ov is not None and ov.estado == "COTIZACION"
    n = db.query(OrdenVenta).filter(OrdenVenta.folio == "COH-OV-001").count()
    sincronizar_espejo_ventas(db)
    assert db.query(OrdenVenta).filter(OrdenVenta.folio == "COH-OV-001").count() == n
    db.query(OrdenVenta).filter(OrdenVenta.folio == "COH-OV-001").delete()
    db.query(Order).filter(Order.folio == "COH-OV-001").delete()
    db.commit(); db.close()


def test_cxc_vencido_en_taller():
    from app.models.client import Client
    from app.models.order import Garment, Order
    from app.models.finanzas import CuentaPorCobrar
    from app.models.inventory import Fabric
    import app.routers.finanzas as rfin
    db = _db()
    cl = db.query(Client).first()
    fab = db.query(Fabric).first()
    o = Order(folio="COH-VENC-001", client_id=cl.id if cl else None, total=800, anticipo=100,
              estado="en_confeccion", fecha_pedido=date(2020, 1, 5), fecha_entrega=date(2020, 2, 5))
    db.add(o); db.flush()
    db.add(Garment(order_id=o.id, tipo="saco", tela_id=fab.id if fab else None, precio=800, estado_taller="EN_CONFECCION"))
    db.commit()
    rfin._sync_cxc(db)
    c = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).first()
    assert c is not None and c.estado == "VENCIDO_EN_TALLER", (c.estado if c else None)
    # entregado con fecha pasada -> VENCIDO clásico
    for g in db.query(Garment).filter(Garment.order_id == o.id).all():
        g.estado_taller = "entregado"
    db.commit()
    rfin._sync_cxc(db)
    c = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).first()
    assert c.estado == "VENCIDO", c.estado
    for g in db.query(Garment).filter(Garment.order_id == o.id).all():
        db.delete(g)
    db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).delete()
    db.query(Order).filter(Order.id == o.id).delete()
    db.commit(); db.close()


def test_abono_registra_turno():
    from app.models.order import Order
    from app.models.finanzas import CuentaPorCobrar
    from app.models.billing import CashMovement
    from app.services.ventas import abrir_turno, turno_abierto
    import app.routers.finanzas as rfin
    db = _db()
    from app.models.user import User
    admin = db.query(User).filter(User.email == "admin@suitelans.mx").first()
    assert admin is not None
    # cierra turnos previos del admin
    for t in db.query(__import__("app.models.billing", fromlist=["CajaTurno"]).CajaTurno).filter_by(usuario_id=admin.id, estado="ABIERTA").all():
        t.estado = "CERRADA"
    db.commit()
    abrir_turno(db, admin.id, 100)
    o = Order(folio="COH-TUR-001", total=600, anticipo=0, estado="cotizado")
    db.add(o); db.commit(); db.refresh(o)
    db.add(CuentaPorCobrar(order_id=o.id, monto_total=600, monto_pagado=0, saldo_pendiente=600, estado="PENDIENTE"))
    db.commit()
    c = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).first()
    assert rfin._aplicar_abono_cxc(db, admin, c.id, 200, "efectivo") is True
    mov = db.query(CashMovement).filter(CashMovement.order_id == o.id).order_by(CashMovement.id.desc()).first()
    assert mov is not None and mov.turno_id is not None
    for t in db.query(__import__("app.models.billing", fromlist=["CajaTurno"]).CajaTurno).filter_by(usuario_id=admin.id, estado="ABIERTA").all():
        t.estado = "CERRADA"
    db.query(CashMovement).filter(CashMovement.order_id == o.id).delete()
    db.query(__import__("app.models.order", fromlist=["Payment"]).Payment).filter_by(order_id=o.id).delete()
    db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).delete()
    db.query(Order).filter(Order.id == o.id).delete()
    db.commit(); db.close()


def test_po_igv_desagregado():
    from app.models.purchasing import PurchaseLine, PurchaseOrder, Supplier
    from app.models.inventory import Fabric
    from app.services.purchasing import recalc_po
    db = _db()
    sup = db.query(Supplier).first()
    fab = db.query(Fabric).first()
    po = PurchaseOrder(folio="COH-IGV-001", supplier_id=sup.id, estado="borrador")
    db.add(po); db.commit(); db.refresh(po)
    db.add(PurchaseLine(purchase_id=po.id, item_tipo="fabric", item_id=fab.id if fab else 1,
                        descripcion="tela", cantidad=10, costo_unitario=118))
    db.commit()
    po = recalc_po(db, po.id)
    assert po.total == Decimal("1180") or float(po.total) == 1180.0
    assert float(po.igv) > 0 and abs(float(po.subtotal) + float(po.igv) - float(po.total)) < 0.02
    db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).delete()
    from app.models.inventario import OrdenCompra
    db.query(OrdenCompra).filter(OrdenCompra.folio == "COH-IGV-001").delete()
    db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).delete()
    db.commit(); db.close()


def test_mod_ref_canonica():
    from sqlalchemy import text
    from app.core.database import engine
    from app.services import finanzas as f
    db = _db()
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (991,'COH-OP','Cohesion',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (991,1,'2026-09-05','REGISTRADO')"))
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id IN (991,992)"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,orden_produccion_id,orden_produccion_ref_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (991,991,555,777,991,1,40,40)"))
        conn.commit()
    assert f.calcular_mod_devengada(db, orden_produccion_id=777) >= Decimal("40")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id IN (991,992)"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=991"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=991"))
        conn.commit()
    db.close()


def test_cierre_y_depreciacion():
    from app.services import finanzas as f
    from app.models.finanzas import AsientoContable
    db = _db()
    from app.models.finanzas import LineaAsientoContable
    # limpieza previa de artefactos COH
    for a in db.query(AsientoContable).filter(AsientoContable.glosa.in_(["COH venta"])).all():
        db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == a.id).delete()
        db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    db.commit()
    c101 = f.get_cuenta_by_codigo(db, "101")
    c701 = f.get_cuenta_by_codigo(db, "701")
    f.crear_asiento(db, date.today(), "COH venta", "VENTA", 9101, [
        {"cuenta_id": c101.id, "debe": Decimal("118"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("100")},
        {"cuenta_id": f.get_cuenta_by_codigo(db, "4011").id, "debe": Decimal("0"), "haber": Decimal("18")},
    ])
    dep = f.registrar_depreciacion(db, date.today(), 50)
    assert dep.id
    from app.models.finanzas import LineaAsientoContable
    dep_cuentas = {l.cuenta_id for l in db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == dep.id).all()}
    assert f.get_cuenta_by_codigo(db, "681").id in dep_cuentas
    assert f.get_cuenta_by_codigo(db, "391").id in dep_cuentas
    p = f.get_or_create_periodo(db, date.today().year, date.today().month)
    a1 = f.cierre_resultados(db, p.anio, p.mes)
    assert a1 is not None
    a2 = f.cierre_resultados(db, p.anio, p.mes)
    assert a2.id == a1.id  # idempotente
    bg = f.obtener_balance_general(db)
    assert bg["valida"] is True
    # limpieza posterior (no contaminar otros tests)
    for a in db.query(AsientoContable).filter(AsientoContable.glosa.in_(["COH venta"])).all():
        db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == a.id).delete()
        db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    for a in db.query(AsientoContable).filter(AsientoContable.origen_tipo == "CIERRE", AsientoContable.periodo_id == p.id).all():
        db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == a.id).delete()
        db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    for a in db.query(AsientoContable).filter(AsientoContable.glosa.like("Depreciación%")).all():
        db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == a.id).delete()
        db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    db.commit()
    db.close()
