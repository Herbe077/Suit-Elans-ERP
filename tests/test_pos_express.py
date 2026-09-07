"""POS: selector RTW con nombre/stock/precio, alta exprés y medidas segregadas."""


def test_variante_rtw_muestra_nombre_stock_precio(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    db = SessionLocal()
    p = db.query(Product).filter(Product.codigo == "PEX-CAM").first()
    if not p:
        p = Product(codigo="PEX-CAM", nombre="Camisa Oxford Slim Fit")
        db.add(p)
        db.commit()
        db.refresh(p)
    v = db.query(ProductVariant).filter(ProductVariant.sku == "PEX-CAM-M").first()
    if not v:
        v = ProductVariant(product_id=p.id, talla="M", sku="PEX-CAM-M",
                           stock=5.0, precio=236.0)
        db.add(v)
        db.commit()
    db.close()
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert "Camisa Oxford Slim Fit - M (Stock: 5) - S/ 236.00" in t


def test_cliente_express_persona_y_empresa(client, auth_cookies):
    # Persona
    r = client.post("/ventas/pos/cliente-rapido",
                    json={"tipo_doc": "DNI", "nro_doc": "pex12345",
                          "nombre": "Exprés", "apellidos": "Test",
                          "telefono": "900111222", "email": "e@x.com"},
                    cookies=auth_cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["tipo"] == "persona" and body["id"] > 0
    # Empresa por RUC
    r = client.post("/ventas/pos/cliente-rapido",
                    json={"tipo_doc": "RUC", "nro_doc": "pex-999",
                          "nombre": "Exprés Corp", "apellidos": "S.A.C."},
                    cookies=auth_cookies)
    assert r.status_code == 200
    assert r.json()["tipo"] == "empresa"
    # Sin nombre → 400
    r = client.post("/ventas/pos/cliente-rapido", json={},
                    cookies=auth_cookies)
    assert r.status_code == 400
    # Aparecen en el POS para continuar la venta
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert "Exprés" in t


def test_express_no_crea_medidas(client, auth_cookies):
    """Las medidas antropométricas no viven en el alta exprés del POS."""
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.measurement import Measurement
    db = SessionLocal()
    c = db.query(Client).filter(Client.nombre == "Exprés").first()
    assert c is not None
    assert db.query(Measurement).filter(Measurement.client_id == c.id).count() == 0
    db.close()
