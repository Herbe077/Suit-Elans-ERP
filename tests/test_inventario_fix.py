"""Inventario: low-stock spec, duplicados, recepción espejo, signos, kardex, API."""
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_low_stock_incluye_insumos():
    from app.core.database import Base, SessionLocal, engine
    from app.models.inventario import ProductoInsumo
    from app.services.inventory import low_stock
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    p = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "INV-LOW-001").first()
    if not p:
        p = ProductoInsumo(sku="INV-LOW-001", nombre="Insumo bajo", categoria="AVIO",
                           stock_fisico=1, stock_reservado=0, stock_minimo=10)
        db.add(p); db.commit()
    assert any(x.sku == "INV-LOW-001" for x in low_stock(db)["productos"])
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == "INV-LOW-001").delete()
    db.commit(); db.close()


def test_duplicado_sku_sin_500():
    c, ck = _client()
    r1 = c.post("/inventario/almacen/tela", data={"codigo": "INV-DUP-001", "nombre": "Tela dup", "stock_metros": "5"}, cookies=ck)
    assert r1.status_code == 303 and "error" not in r1.headers.get("location", "")
    r2 = c.post("/inventario/almacen/tela", data={"codigo": "INV-DUP-001", "nombre": "Tela dup 2"}, cookies=ck)
    assert r2.status_code == 303 and "error=duplicado" in r2.headers.get("location", "")
    from app.core.database import SessionLocal
    from app.models.inventory import Fabric
    from app.models.inventario import ProductoInsumo
    db = SessionLocal()
    assert db.query(Fabric).filter(Fabric.codigo == "INV-DUP-001").count() == 1
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == "INV-DUP-001").delete()
    db.query(Fabric).filter(Fabric.codigo == "INV-DUP-001").delete()
    db.commit(); db.close()


def test_recepcion_actualiza_ambos_stocks():
    from app.core.database import Base, SessionLocal, engine
    from app.models.inventory import Fabric
    from app.models.inventario import OrdenCompra, DetalleOrdenCompra, ProductoInsumo
    from app.models.purchasing import PurchaseLine, PurchaseOrder, Supplier
    from app.services.purchasing import recalc_po
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    sup = db.query(Supplier).first()
    fab = Fabric(codigo="INV-REC-001", nombre="Tela rec", stock_metros=10.0, precio_metro=50.0, proveedor_id=sup.id if sup else None)
    db.add(fab); db.commit(); db.refresh(fab)
    po = PurchaseOrder(folio="INV-OC-REC-001", supplier_id=sup.id if sup else 1, estado="borrador")
    db.add(po); db.commit(); db.refresh(po)
    oc = OrdenCompra(proveedor_id=sup.id if sup else 1, estado="BORRADOR", monto_total=0, folio="INV-OC-REC-001")
    db.add(oc); db.commit(); db.refresh(oc)
    db.add(PurchaseLine(purchase_id=po.id, item_tipo="fabric", item_id=fab.id, descripcion="tela", cantidad=4, costo_unitario=50))
    db.commit()
    recalc_po(db, po.id)
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "INV-REC-001").first()
    if not prod:
        from app.services.inventory import ensure_producto_for_fabric
        prod = ensure_producto_for_fabric(db, fab)
    db.add(DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod.id, cantidad_solicitada=4, precio_unitario=50))
    db.commit()
    c, ck = _client()
    line = db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).first()
    r = c.post(f"/inventario/compras/linea/{line.id}/recibir", data={"cantidad": "4"}, cookies=ck)
    assert r.status_code == 303
    db.refresh(fab)
    assert fab.stock_metros == 14.0, fab.stock_metros
    db.refresh(prod)
    assert prod.stock_fisico == 14.0, prod.stock_fisico
    # cleanup
    from app.models.inventory import StockMovement
    from app.models.inventario import MovimientoKardex
    db.query(MovimientoKardex).filter(MovimientoKardex.producto_id == prod.id).delete()
    db.query(StockMovement).filter(StockMovement.item_id == fab.id).delete()
    db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id == oc.id).delete()
    db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).delete()
    db.query(OrdenCompra).filter(OrdenCompra.id == oc.id).delete()
    db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.id == prod.id).delete()
    db.query(Fabric).filter(Fabric.id == fab.id).delete()
    db.commit(); db.close()


def test_movimiento_normaliza_signo_y_kardex_valida():
    from app.core.database import Base, SessionLocal, engine
    from app.models.inventory import Fabric
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    fab = Fabric(codigo="INV-SIG-001", nombre="Tela sig", stock_metros=20.0)
    db.add(fab); db.commit(); db.refresh(fab)
    fid = fab.id
    db.close()
    c, ck = _client()
    # entrada con cantidad negativa debe sumar igual
    c.post("/inventario/almacen/movimiento", data={"item_tipo": "fabric", "item_id": str(fid), "cantidad": "-5", "tipo": "entrada", "motivo": "t"}, cookies=ck)
    db = SessionLocal()
    assert db.get(Fabric, fid).stock_metros == 25.0
    # kardex con 0/cantidad inválida no revienta
    prod_id = 999999
    r = c.post("/inventario/almacen/kardex", data={"producto_id": str(prod_id), "tipo_movimiento": "INGRESO_COMPRA", "cantidad": "0"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    db.query(Fabric).filter(Fabric.codigo == "INV-SIG-001").delete()
    from app.models.inventory import StockMovement
    from app.models.inventario import ProductoInsumo, MovimientoKardex
    db.query(StockMovement).filter(StockMovement.item_id == fid).delete()
    db.query(MovimientoKardex).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == "INV-SIG-001").delete()
    db.commit(); db.close()


def test_api_low_stock_completo():
    c, ck = _client()
    r = c.post("/api/v1/auth/token", data={"username": "admin@suitelans.mx", "password": "admin123"})
    token = r.json()["access_token"]
    r = c.get("/api/v1/products/low-stock", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"variantes", "telas", "avios", "insumos"}
