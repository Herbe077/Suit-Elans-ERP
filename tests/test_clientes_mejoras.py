"""Mejoras Clientes/POS/Fichas: colaboradores B2B, concepto de pedido,
RUC flexible y edición de clientes."""


def test_editar_persona(client, auth_cookies):
    r = client.post("/comercial/clientes/persona",
                    data={"nombre": "Edit", "apellidos": "Mejoras", "tipo_doc": "DNI",
                          "nro_doc": "55667788", "telefono": "999111222",
                          "distrito": "Lima", "clasificacion": "Nuevo"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = db.query(Client).filter(Client.nro_doc == "55667788").first()
    assert c is not None
    pid = c.id
    db.close()
    # Formulario de edición visible
    r = client.get(f"/comercial/clientes/persona/{pid}/editar", cookies=auth_cookies)
    assert r.status_code == 200 and "Guardar cambios" in r.text
    # Actualiza teléfono, distrito, clasificación y RUC flexible
    r = client.post(f"/comercial/clientes/persona/{pid}/editar",
                    data={"nombre": "Edit", "apellidos": "Mejoras",
                          "tipo_doc": "RUC", "nro_doc": "ABC-123",
                          "telefono": "988777666", "email": "",
                          "direccion": "Av. Test 123", "distrito": "Miraflores",
                          "clasificacion": "VIP", "company_id": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    c = db.get(Client, pid)
    assert c.telefono == "988777666" and c.distrito == "Miraflores"
    assert c.clasificacion == "VIP" and c.nro_doc == "ABC123"
    assert c.direccion == "Av. Test 123"
    db.close()
    # Visible en tabla con botón editar
    r = client.get("/comercial/clientes", cookies=auth_cookies)
    assert f"/comercial/clientes/persona/{pid}/editar" in r.text


def test_editar_empresa(client, auth_cookies):
    r = client.post("/comercial/clientes/empresa",
                    data={"nombre_comercial": "EditCorp S.A.C.", "ruc": "XZ-99",
                          "telefono": "999000111", "distrito": "Lima",
                          "clasificacion": "Nuevo", "descuento_pct": "0"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.company import Company
    db = SessionLocal()
    e = db.query(Company).filter(Company.nombre_comercial == "EditCorp S.A.C.").first()
    assert e is not None and e.ruc == "XZ99"  # RUC flexible: se guardó igual
    cid = e.id
    db.close()
    r = client.get(f"/comercial/clientes/empresa/{cid}/editar", cookies=auth_cookies)
    assert r.status_code == 200 and "Guardar cambios" in r.text
    r = client.post(f"/comercial/clientes/empresa/{cid}/editar",
                    data={"nombre_comercial": "EditCorp S.A.C.", "ruc": "XZ-99",
                          "telefono": "977666555", "email": "", "direccion": "",
                          "distrito": "San Isidro", "clasificacion": "VIP",
                          "descuento_pct": "15"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    e = db.get(Company, cid)
    assert e.telefono == "977666555" and e.distrito == "San Isidro"
    assert e.clasificacion == "VIP" and e.descuento_pct == 15.0
    db.close()
    r = client.get("/comercial/clientes?tab=empresas", cookies=auth_cookies)
    assert f"/comercial/clientes/empresa/{cid}/editar" in r.text


def test_colaboradores_b2b(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.company import Company
    db = SessionLocal()
    e = db.query(Company).filter(Company.nombre_comercial == "EditCorp S.A.C.").first()
    cid = e.id
    # Persona libre para vincular
    libre = Client(nombre="Libre", apellidos="Vincular", tipo_doc="DNI",
                   clasificacion="Nuevo")
    db.add(libre)
    db.commit()
    lid = libre.id
    db.close()
    # Registrar colaborador nuevo dentro de la empresa
    r = client.post(f"/comercial/clientes/empresa/{cid}/colaboradores",
                    data={"nombre": "Juan", "apellidos": "Colab",
                          "telefono": "912345678", "distrito": "Ate"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    # Vincular persona existente
    r = client.post(f"/comercial/clientes/empresa/{cid}/colaboradores/vincular",
                    data={"client_id": str(lid)}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    cols = db.query(Client).filter(Client.company_id == cid).all()
    assert {c.nombre for c in cols} == {"Juan", "Libre"}
    jid = next(c.id for c in cols if c.nombre == "Juan")
    db.close()
    # La ficha 360° muestra colaboradores con link a su ficha de medidas
    r = client.get(f"/comercial/clientes/detalle?tipo=empresa&id={cid}",
                   cookies=auth_cookies)
    assert "Colaboradores" in r.text and "Juan Colab" in r.text
    assert f"/produccion/fichas/{jid}" in r.text
    # Desvincular
    r = client.post(f"/comercial/clientes/empresa/{cid}/colaboradores/{lid}/desvincular",
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Client, lid).company_id is None
    assert db.get(Client, jid).company_id == cid
    db.close()


def test_pos_empresa_con_colaborador_factura_a_empresa(client, auth_cookies):
    """POS: empresa + colaborador → pedido con company (facturación) y
    cliente beneficiario (prenda/ficha)."""
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.company import Company
    from app.models.order import Garment, Order
    db = SessionLocal()
    e = db.query(Company).filter(Company.nombre_comercial == "EditCorp S.A.C.").first()
    col = db.query(Client).filter(Client.company_id == e.id).first()
    assert col is not None
    cid, eid = e.id, col.id
    db.close()
    # Venta con colaborador existente + concepto
    r = client.post("/ventas/pos/vender",
                    data={"company_id": str(cid), "colaborador_id": str(eid),
                          "concepto": "Terno corporativo", "precio": "1500",
                          "garment_tipo": "saco", "monto_cobro": "0"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Terno corporativo").first()
    assert o is not None
    assert o.company_id == cid and o.client_id == eid  # factura: empresa
    assert db.query(Garment).filter(Garment.order_id == o.id).count() == 1
    db.close()
    # Venta creando colaborador al vuelo
    r = client.post("/ventas/pos/vender",
                    data={"company_id": str(cid), "colab_nombre": "Nuevo",
                          "colab_apellidos": "Ingresante", "colab_telefono": "900111222",
                          "concepto": "Pantalón corporativo", "precio": "800",
                          "garment_tipo": "pantalon", "monto_cobro": "0"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Pantalón corporativo").first()
    assert o is not None and o.company_id == cid
    nuevo = db.get(Client, o.client_id)
    assert nuevo is not None and nuevo.nombre == "Nuevo"
    assert nuevo.company_id == cid  # vinculado automáticamente
    db.close()
    # El POS muestra el bloque de colaborador al haber empresas
    r = client.get("/ventas/pos", cookies=auth_cookies)
    assert "Colaborador / Beneficiario" in r.text


def test_concepto_desde_alta_clientes(client, auth_cookies):
    """Alta en Clientes con concepto → llega a la ficha y al pedido."""
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order
    r = client.post("/comercial/clientes/persona",
                    data={"nombre": "Concepto", "apellidos": "Test", "tipo_doc": "DNI",
                          "nro_doc": "66778899", "concepto": "Smoking boda"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303
    assert "concepto=" in r.headers["location"]
    db = SessionLocal()
    c = db.query(Client).filter(Client.nro_doc == "66778899").first()
    pid = c.id
    db.close()
    r = client.get(f"/produccion/fichas/{pid}?concepto=Smoking%20boda",
                   cookies=auth_cookies)
    assert r.status_code == 200
    assert 'name="concepto"' in r.text and "Smoking boda" in r.text
    r = client.post(f"/produccion/fichas/{pid}/pedidos",
                    data={"tipo": "smoking", "precio": "2000",
                          "concepto": "Smoking boda"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.client_id == pid).order_by(
        Order.id.desc()).first()
    assert o is not None and o.concepto == "Smoking boda"
    db.close()
