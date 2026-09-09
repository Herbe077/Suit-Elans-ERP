import os
"""Integración web: router inventario <-> servicio compras_kardex + plantillas.

Flujo HTTP: crear OC (DRAFT) -> aprobar -> landed -> recibir (kardex+241/611)
-> facturar (BILLED, 601+4011/421) -> recepción bloqueada. Verifica que las
vistas rendericen BILLED / PARTIALLY_RECEIVED y el costo final con Landed.
"""
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _setup_oc(folio, sku):
    from app.core.database import Base, SessionLocal, engine
    from app.models.inventario import DetalleOrdenCompra, OrdenCompra, ProductoInsumo
    from app.models.purchasing import Supplier
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    sup = db.query(Supplier).filter(Supplier.nombre == f"SUP-{folio}").first()
    if not sup:
        sup = Supplier(nombre=f"SUP-{folio}")
        db.add(sup); db.commit(); db.refresh(sup)
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku).first()
    if not prod:
        prod = ProductoInsumo(sku=sku, nombre=f"Insumo {sku}", categoria="TELA",
                              unidad_medida="METROS")
        db.add(prod); db.commit(); db.refresh(prod)
    oc = db.query(OrdenCompra).filter(OrdenCompra.folio == folio).first()
    if not oc:
        oc = OrdenCompra(proveedor_id=sup.id, folio=folio, estado="DRAFT")
        db.add(oc); db.flush()
        db.add(DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod.id,
                                 cantidad_solicitada=10, precio_unitario=100.0))
        db.commit(); db.refresh(oc)
    oid, pid = oc.id, prod.id
    db.close()
    return oid, pid


def _cleanup_oc(folio, sku):
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable, CuentaPorPagar, LineaAsientoContable
    from app.models.inventario import DetalleOrdenCompra, MovimientoKardex, OrdenCompra, ProductoInsumo
    from app.models.inventory import StockMovement
    from app.models.purchasing import PurchaseOrder, Supplier
    db = SessionLocal()
    oc = db.query(OrdenCompra).filter(OrdenCompra.folio == folio).first()
    oids = [oc.id] if oc else []
    if oids:
        kas = db.query(MovimientoKardex).filter(MovimientoKardex.orden_compra_id.in_(oids)).all()
        aids = {k.asiento_id for k in kas if k.asiento_id}
        # + asientos de provisión (COMPRA, origen_id=OC) no vinculados al kardex
        for a in db.query(AsientoContable).filter(AsientoContable.origen_id.in_(oids)).all():
            aids.add(a.id)
        db.query(MovimientoKardex).filter(MovimientoKardex.orden_compra_id.in_(oids)).delete(synchronize_session=False)
        if aids:
            db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id.in_(list(aids))).delete(synchronize_session=False)
            db.query(AsientoContable).filter(AsientoContable.id.in_(list(aids))).delete(synchronize_session=False)
        db.query(CuentaPorPagar).filter(CuentaPorPagar.orden_compra_id.in_(oids)).delete(synchronize_session=False)
        db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id.in_(oids)).delete(synchronize_session=False)
        db.query(OrdenCompra).filter(OrdenCompra.id.in_(oids)).delete(synchronize_session=False)
    db.query(StockMovement).filter(StockMovement.motivo.like(f"%{folio}%")).delete(synchronize_session=False)
    db.query(PurchaseOrder).filter(PurchaseOrder.folio == folio).delete(synchronize_session=False)
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku).delete(synchronize_session=False)
    db.query(Supplier).filter(Supplier.nombre == f"SUP-{folio}").delete(synchronize_session=False)
    db.commit(); db.close()


def test_flujo_web_completo_con_plantillas():
    folio, sku = "OC-WEB-001", "SKU-WEB-001"
    oid, pid = _setup_oc(folio, sku)
    c, ck = _client()

    r = c.get("/inventario/compras", cookies=ck)
    assert r.status_code == 200 and "DRAFT" in r.text

    # DRAFT no recibe
    r = c.post(f"/inventario/compras/orden/{oid}/recibir",
               data={"producto_id": str(pid), "cantidad": "5"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    # aprobar
    r = c.post(f"/inventario/compras/orden/{oid}/aprobar", cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    r = c.get(f"/inventario/compras/orden/{oid}", cookies=ck)
    assert r.status_code == 200 and "APPROVED" in r.text

    # landed 200 sobre base 1000 -> +20/u -> final 120/u
    r = c.post(f"/inventario/compras/orden/{oid}/landed",
               data={"landed_flete": "200", "landed_seguro": "0", "landed_otros": "0"}, cookies=ck)
    assert r.status_code == 303

    # recepción parcial 4u -> PARTIALLY_RECEIVED + costo final con landed
    r = c.post(f"/inventario/compras/orden/{oid}/recibir",
               data={"producto_id": str(pid), "cantidad": "4"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    r = c.get(f"/inventario/compras/orden/{oid}", cookies=ck)
    assert r.status_code == 200
    assert "PARTIALLY_RECEIVED" in r.text
    assert "101.69" in r.text  # costo final neto = (100 + 20 landed) / 1.18
    assert "241" in r.text and "611" in r.text

    # facturar lo recibido (4u*100=400 final → base 338.98, igv 61.02)
    r = c.post(f"/inventario/compras/orden/{oid}/facturar",
               data={"numero_factura": "F001-WEB001"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    r = c.get(f"/inventario/compras/orden/{oid}", cookies=ck)
    assert r.status_code == 200 and "BILLED" in r.text and "F001-WEB001" in r.text

    # BILLED bloquea recepción
    r = c.post(f"/inventario/compras/orden/{oid}/recibir",
               data={"producto_id": str(pid), "cantidad": "1"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    # cambio directo a RECEIVED por formulario queda redirigido a Recibir
    r = c.post(f"/inventario/compras/orden/{oid}/estado",
               data={"estado": "RECEIVED"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]

    _cleanup_oc(folio, sku)


def test_listado_muestra_billed():
    folio, sku = "OC-WEB-002", "SKU-WEB-002"
    oid, pid = _setup_oc(folio, sku)
    c, ck = _client()
    c.post(f"/inventario/compras/orden/{oid}/aprobar", cookies=ck)
    c.post(f"/inventario/compras/orden/{oid}/recibir",
           data={"producto_id": str(pid), "cantidad": "10"}, cookies=ck)
    c.post(f"/inventario/compras/orden/{oid}/facturar",
           data={"numero_factura": "F001-WEB002"}, cookies=ck)
    r = c.get("/inventario/compras/listado", cookies=ck)
    assert r.status_code == 200 and "BILLED" in r.text and "F001-WEB002" in r.text
    _cleanup_oc(folio, sku)
