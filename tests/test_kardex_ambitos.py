"""Kardex por ámbito: MP (Cta 24) vs PT (Cta 23), tipos por contexto y costo PT."""


def _client():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _setup_pt():
    from app.core.database import Base, SessionLocal, engine
    from app.models.catalog import Product, ProductVariant
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    p = db.query(Product).filter(Product.codigo == "KXA-P-001").first()
    if not p:
        p = Product(codigo="KXA-P-001", nombre="Prueba ámbitos")
        db.add(p); db.commit(); db.refresh(p)
    pid = p.id
    v = db.query(ProductVariant).filter(ProductVariant.sku == "KXA-P-001-M").first()
    if not v:
        v = ProductVariant(product_id=pid, talla="M", sku="KXA-P-001-M",
                           stock=10.0, precio=100.0, costo_unitario=60.0)
        db.add(v); db.commit(); db.refresh(v)
    vid = v.id
    db.close()
    return pid, vid


def _cleanup_pt(pid, vid):
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    from app.models.inventory import StockMovement
    db = SessionLocal()
    db.query(StockMovement).filter(StockMovement.item_id == vid).delete()
    db.query(ProductVariant).filter(ProductVariant.id == vid).delete()
    db.query(Product).filter(Product.id == pid).delete()
    db.commit(); db.close()


def test_kardex_pt_ingreso_venta_muestra():
    from app.core.database import SessionLocal
    from app.models.catalog import ProductVariant
    pid, vid = _setup_pt()
    c, ck = _client()
    base = {"alcance": "pt", "variant_id": str(vid), "observacion": "t"}
    r = c.post("/inventario/almacen/kardex",
               data={**base, "tipo_movimiento": "INGRESO_PRODUCCION", "cantidad": "5"}, cookies=ck)
    assert r.status_code == 303 and r.headers["location"] == "/inventario/almacen?sub=pt"
    r = c.post("/inventario/almacen/kardex",
               data={**base, "tipo_movimiento": "SALIDA_VENTA", "cantidad": "3"}, cookies=ck)
    assert r.status_code == 303
    r = c.post("/inventario/almacen/kardex",
               data={**base, "tipo_movimiento": "MUESTRA", "cantidad": "1"}, cookies=ck)
    assert r.status_code == 303
    db = SessionLocal()
    assert db.get(ProductVariant, vid).stock == 11.0  # 10+5-3-1
    db.close()
    # tipo MP rechazado en ámbito PT
    r = c.post("/inventario/almacen/kardex",
               data={**base, "tipo_movimiento": "INGRESO_COMPRA", "cantidad": "1"}, cookies=ck)
    assert "error=" in r.headers["location"]
    _cleanup_pt(pid, vid)


def test_kardex_mp_acepta_ajuste_inventario():
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.core.database import Base, engine
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "KXA-MP-001").first()
    if not prod:
        prod = ProductoInsumo(sku="KXA-MP-001", nombre="MP ámbitos", categoria="TELA",
                              stock_fisico=20.0)
        db.add(prod); db.commit(); db.refresh(prod)
    iid = prod.id
    db.close()
    c, ck = _client()
    r = c.post("/inventario/almacen/kardex",
               data={"alcance": "mp", "producto_id": str(iid),
                     "tipo_movimiento": "AJUSTE_INVENTARIO", "cantidad": "2",
                     "observacion": "conteo"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    assert db.get(ProductoInsumo, iid).stock_fisico == 18.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == iid).order_by(MovimientoKardex.id.desc()).first()
    assert k.tipo_movimiento == "AJUSTE_INVENTARIO"
    db.query(MovimientoKardex).filter(MovimientoKardex.producto_id == iid).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.id == iid).delete()
    db.commit(); db.close()


def test_pt_muestra_costo_y_tabs_filtran():
    pid, vid = _setup_pt()
    c, ck = _client()
    c.post(f"/inventario/catalogo/variante/{vid}/guardar",
           data={"talla": "M", "precio": "110", "costo_unitario": "65"}, cookies=ck)
    a = c.get("/inventario/almacen?sub=pt", cookies=ck).text
    assert "Costo S/" in a and "65.00" in a and "Precio S/" not in a.split("Productos Terminados")[1].split("Registro de Movimiento")[0]
    assert "INGRESO_PRODUCCION" in a and "SALIDA_VENTA" in a and "MUESTRA" in a
    assert "KXA-P-001-M" in a  # selector filtrado a variantes
    _cleanup_pt(pid, vid)
