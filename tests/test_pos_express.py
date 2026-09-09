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


def test_paso1_gate_y_card_compacta(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert 'id="btn-paso2"' in t and "disabled" in t  # bloqueado sin cliente
    assert "Público General" in t
    assert 'id="pos-confirm"' in t and "Cambiar" in t  # card compacta
    assert "pos-cliente-vacio" not in t and "pos-comprobante" not in t
    assert 'x-show="express"' in t  # alta rápida oculta por defecto


def test_publico_general_vende_sin_cliente(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    antes = db.query(Order).count()
    db.close()
    r = client.post("/ventas/pos/vender",
                    data={"publico_general": "1", "concepto": "Venta menor PG",
                          "precio": "150", "garment_tipo": "camisa",
                          "monto_cobro": "0"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Venta menor PG").first()
    assert o is not None and o.client_id is None and o.company_id is None
    assert o.total == 150.0  # sin recargos
    # Sin cliente ni flag: no se crea nada
    n = db.query(Order).count()
    db.close()
    r = client.post("/ventas/pos/vender",
                    data={"concepto": "Sin cliente", "precio": "100",
                          "garment_tipo": "camisa", "monto_cobro": "0"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    assert db.query(Order).filter(Order.concepto == "Sin cliente").first() is None
    assert db.query(Order).count() == n
    db.close()


def test_catalogo_agrupado_y_layout_paso2(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    for opt in ("Traje / Terno 2 Piezas", "Traje 3 Piezas", "Smoking Completo",
                "Saco Caballero", "Saco / Blazer Dama", "Falda", "Vestido"):
        assert opt in t
    assert 'optgroup label="Conjuntos"' in t and 'optgroup label="Dama"' in t
    assert "grid-cols-1 sm:grid-cols-3" in t
    assert t.count('id="pv-tela"') == 1  # sin desplegable huérfano


def test_conjunto_genera_fichas_por_pieza(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = Client(nombre="Terno", apellidos="SUNAT", tipo_doc="DNI",
               nro_doc="12345678", clasificacion="Nuevo")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    # S/ 3000 ≥ tope SUNAT: exige cliente con DNI (no Público General)
    r = client.post("/ventas/pos/vender",
                    data={"client_id": str(cid), "concepto": "Terno ejecutivo",
                          "precio": "3000", "garment_tipo": "traje_2_piezas",
                          "monto_cobro": "0"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Terno ejecutivo").first()
    gs = db.query(Garment).filter(Garment.order_id == o.id).order_by(
        Garment.id).all()
    assert [g.tipo for g in gs] == ["saco", "pantalon"]  # fichas individuales
    assert round(sum(g.precio for g in gs), 2) == 3000.0
    assert o.total == 3000.0
    db.close()
    r = client.post("/ventas/pos/vender",
                    data={"publico_general": "1", "concepto": "Falda medida",
                          "precio": "600", "garment_tipo": "falda",
                          "monto_cobro": "0"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Falda medida").first()
    assert db.query(Garment).filter(Garment.order_id == o.id).count() == 1
    db.close()
