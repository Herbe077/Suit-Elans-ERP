"""Edición de maestro: materia prima (insumo) y prendas terminadas (producto+variante).

Reglas: SKU/código solo lectura; stock y costos solo vía Kardex; el nombre se
espeja al gemelo legacy; validaciones con ?error=; 404 si no existe.
"""
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _setup():
    from app.core.database import Base, SessionLocal, engine
    from app.models.catalog import Collection, Product, ProductVariant
    from app.models.inventario import ProductoInsumo
    from app.models.inventory import Fabric
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    fab = db.query(Fabric).filter(Fabric.codigo == "EDIT-TE-001").first()
    if not fab:
        fab = Fabric(codigo="EDIT-TE-001", nombre="Tela vieja", stock_metros=20.0,
                     precio_metro=30.0)
        db.add(fab); db.commit(); db.refresh(fab)
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "EDIT-TE-001").first()
    if not prod:
        prod = ProductoInsumo(sku="EDIT-TE-001", nombre="Tela vieja", categoria="TELA",
                              unidad_medida="METROS", costo_unitario=30.0, costo_promedio=30.0,
                              ultimo_costo=30.0, stock_fisico=20.0, stock_minimo=5.0)
        db.add(prod); db.commit(); db.refresh(prod)
    col = db.query(Collection).filter(Collection.nombre == "EDIT-COL").first()
    if not col:
        col = Collection(nombre="EDIT-COL", temporada="T1")
        db.add(col); db.commit(); db.refresh(col)
    p = db.query(Product).filter(Product.codigo == "EDIT-TR-001").first()
    if not p:
        p = Product(codigo="EDIT-TR-001", nombre="Terno viejo", linea="comercial",
                    collection_id=col.id, precio_base=1000.0)
        db.add(p); db.commit(); db.refresh(p)
    v = db.query(ProductVariant).filter(ProductVariant.sku == "EDIT-TR-001-M").first()
    if not v:
        v = ProductVariant(product_id=p.id, talla="M", sku="EDIT-TR-001-M",
                           stock=3.0, precio=1200.0)
        db.add(v); db.commit(); db.refresh(v)
    ids = (prod.id, p.id, v.id, col.id)
    db.close()
    return ids


def _cleanup():
    from app.core.database import SessionLocal
    from app.models.catalog import Collection, Product, ProductVariant
    from app.models.inventario import ProductoInsumo
    from app.models.inventory import Fabric
    db = SessionLocal()
    db.query(ProductVariant).filter(ProductVariant.sku == "EDIT-TR-001-M").delete()
    db.query(Product).filter(Product.codigo == "EDIT-TR-001").delete()
    db.query(Collection).filter(Collection.nombre == "EDIT-COL").delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == "EDIT-TE-001").delete()
    db.query(Fabric).filter(Fabric.codigo == "EDIT-TE-001").delete()
    db.commit(); db.close()


def test_editar_insumo_guarda_y_no_toca_stock_costos():
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.inventory import Fabric
    iid, _, _, _ = _setup()
    c, ck = _client()
    r = c.get(f"/inventario/catalogo/insumo/{iid}/editar", cookies=ck)
    assert r.status_code == 200 and "EDIT-TE-001" in r.text
    n_k0 = SessionLocal().query(MovimientoKardex).count()
    r = c.post(f"/inventario/catalogo/insumo/{iid}/guardar", data={
        "nombre": "Tela Casimir Nueva", "categoria": "TELA", "composicion": "100% Lana",
        "color": "Azul", "ancho_cm": "150", "unidad_medida": "METROS",
        "precio_metro": "45", "stock_minimo": "8", "proveedor_id": ""}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(ProductoInsumo, iid)
    assert p.nombre == "Tela Casimir Nueva" and p.color == "Azul"
    assert p.composicion == "100% Lana" and p.stock_minimo == 8.0
    # intactos: sku, stock, costos
    assert p.sku == "EDIT-TE-001" and p.stock_fisico == 20.0
    assert p.costo_promedio == 30.0 and p.ultimo_costo == 30.0
    # espejo legacy solo descriptivo
    fab = db.query(Fabric).filter(Fabric.codigo == "EDIT-TE-001").first()
    assert fab.nombre == "Tela Casimir Nueva" and fab.stock_metros == 20.0
    assert db.query(MovimientoKardex).count() == n_k0
    db.close()
    _cleanup()


def test_editar_insumo_valida_y_404():
    _setup()
    c, ck = _client()
    assert c.get("/inventario/catalogo/insumo/999999/editar", cookies=ck).status_code == 404
    assert c.post("/inventario/catalogo/insumo/999999/guardar", data={"nombre": "X"}, cookies=ck).status_code == 404
    from app.core.database import SessionLocal
    from app.models.inventario import ProductoInsumo
    db = SessionLocal()
    iid = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "EDIT-TE-001").first().id
    db.close()
    r = c.post(f"/inventario/catalogo/insumo/{iid}/guardar", data={
        "nombre": "  ", "categoria": "TELA", "ancho_cm": "150",
        "unidad_medida": "METROS", "precio_metro": "0", "stock_minimo": "5",
        "proveedor_id": ""}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    r = c.post(f"/inventario/catalogo/insumo/{iid}/guardar", data={
        "nombre": "Ok", "categoria": "TELA", "ancho_cm": "150",
        "unidad_medida": "METROS", "precio_metro": "0", "stock_minimo": "-1",
        "proveedor_id": ""}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    _cleanup()


def test_editar_producto_y_variante():
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    _, pid, vid, _ = _setup()
    c, ck = _client()
    r = c.get(f"/inventario/catalogo/producto/{pid}/editar", cookies=ck)
    assert r.status_code == 200 and "EDIT-TR-001" in r.text
    assert c.get("/inventario/catalogo/producto/999999/editar", cookies=ck).status_code == 404
    r = c.post(f"/inventario/catalogo/producto/{pid}/guardar", data={
        "nombre": "Terno Ejecutivo Nuevo", "linea": "medida",
        "collection_id": "", "precio_base": "1500"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    r = c.post(f"/inventario/catalogo/variante/{vid}/guardar", data={
        "talla": "L", "precio": "1600"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(Product, pid)
    assert p.nombre == "Terno Ejecutivo Nuevo" and p.linea == "medida"
    assert p.codigo == "EDIT-TR-001" and p.precio_base == 1500.0
    v = db.get(ProductVariant, vid)
    assert v.talla == "L" and v.precio == 1600.0
    assert v.sku == "EDIT-TR-001-M" and v.stock == 3.0
    db.close()
    _cleanup()
