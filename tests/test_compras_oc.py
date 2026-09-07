"""OC: Draft Builder, sin vacías, submenu en detalle y forms responsive."""


def _setup(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import ProductoInsumo
    from app.models.purchasing import Supplier
    db = SessionLocal()
    sup = db.query(Supplier).filter(Supplier.nombre == "SUP-BLD").first()
    if not sup:
        sup = Supplier(nombre="SUP-BLD")
        db.add(sup)
        db.commit()
        db.refresh(sup)
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == "BLD-001").first()
    if not prod:
        prod = ProductoInsumo(sku="BLD-001", nombre="Insumo builder",
                              categoria="TELA", unidad_medida="METROS")
        db.add(prod)
        db.commit()
        db.refresh(prod)
    sid, pid = sup.id, prod.id
    db.close()
    return sid, pid


def test_builder_crea_oc_con_lineas(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import DetalleOrdenCompra, OrdenCompra
    sid, pid = _setup(client, auth_cookies)
    db = SessionLocal()
    antes = db.query(OrdenCompra).count()
    db.close()
    r = client.post("/inventario/compras/nueva",
                    data={"supplier_id": str(sid), "producto_id": [str(pid)],
                          "cantidad": ["5"], "precio_unitario": ["100"]},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "/inventario/compras/orden/" in r.headers["location"]
    db = SessionLocal()
    assert db.query(OrdenCompra).count() == antes + 1
    oc = db.query(OrdenCompra).order_by(OrdenCompra.id.desc()).first()
    dets = db.query(DetalleOrdenCompra).filter(
        DetalleOrdenCompra.orden_compra_id == oc.id).all()
    assert len(dets) == 1 and oc.monto_total == 500.0
    db.close()


def test_builder_sin_lineas_no_crea(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import OrdenCompra
    sid, _ = _setup(client, auth_cookies)
    db = SessionLocal()
    antes = db.query(OrdenCompra).count()
    db.close()
    r = client.post("/inventario/compras/nueva",
                    data={"supplier_id": str(sid)},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    db = SessionLocal()
    assert db.query(OrdenCompra).count() == antes
    db.close()


def test_listado_oculta_vacias_y_limpia(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import OrdenCompra
    sid, _ = _setup(client, auth_cookies)
    db = SessionLocal()
    vacia = OrdenCompra(proveedor_id=sid, folio="OC-VACIA-001", estado="DRAFT",
                        monto_total=0)
    db.add(vacia)
    db.commit()
    db.close()
    t = client.get("/inventario/compras", cookies=auth_cookies).text
    assert "OC-VACIA-001" not in t  # oculta por defecto
    assert "Limpiar borradores vacíos" in t
    t = client.get("/inventario/compras?ver_vacias=1", cookies=auth_cookies).text
    assert "OC-VACIA-001" in t
    r = client.post("/inventario/compras/borradores/limpiar",
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "ok=limpios_" in r.headers.get("location", "")
    db = SessionLocal()
    assert db.query(OrdenCompra).filter(
        OrdenCompra.folio == "OC-VACIA-001").first() is None
    db.close()


def test_detalle_con_submenu_y_form_responsive(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import OrdenCompra
    db = SessionLocal()
    oc = db.query(OrdenCompra).order_by(OrdenCompra.id.desc()).first()
    oid = oc.id
    db.close()
    t = client.get(f"/inventario/compras/orden/{oid}", cookies=auth_cookies).text
    assert "Catálogo" in t and "Almacén" in t and "Compras" in t  # submenu base
    assert "flex flex-col sm:flex-row gap-2" in t  # agregar línea responsive
