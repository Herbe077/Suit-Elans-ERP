"""Regresión: crear pedido/cotización con fecha no debe dar 500."""


def test_crear_pedido_con_fecha(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order
    db = SessionLocal()
    c = Client(nombre="Fecha", apellidos="Test")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    r = client.post(f"/produccion/fichas/{cid}/pedidos",
                    data={"tipo": "saco", "precio": "1500",
                          "fecha_entrega": "2026-12-01"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.client_id == cid).first()
    assert o is not None and str(o.fecha_entrega) == "2026-12-01"
    assert o.total == 1500.0
    db.close()


def test_crear_pedido_fecha_invalida_no_500(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = Client(nombre="Fecha2", apellidos="Test")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    r = client.post(f"/produccion/fichas/{cid}/pedidos",
                    data={"tipo": "pantalon", "fecha_entrega": "no-fecha"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)


def test_cotizacion_con_validez(client, auth_cookies):
    r = client.post("/comercial/crm/cotizaciones", data={"validez_hasta": "2026-11-30"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.crm import Quotation
    db = SessionLocal()
    q = db.query(Quotation).order_by(Quotation.id.desc()).first()
    assert str(q.validez_hasta) == "2026-11-30"
    db.close()
