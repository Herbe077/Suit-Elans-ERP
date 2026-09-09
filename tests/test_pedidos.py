"""Regresión: crear pedido/cotización desde Ventas no debe dar 500."""


def test_crear_pedido_desde_ventas(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = Client(nombre="Fecha", apellidos="Test")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    r = client.post("/ventas/ordenes",
                    data={"client_id": str(cid), "concepto": "Terno medida",
                          "total": "1500", "garment_tipo": "saco"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.client_id == cid).first()
    assert o is not None and o.concepto == "Terno medida"
    assert o.total == 1500.0
    assert db.query(Garment).filter(Garment.order_id == o.id).count() == 1
    db.close()


def test_crear_pedido_invalido_no_500(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = Client(nombre="Fecha2", apellidos="Test")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    r = client.post("/ventas/ordenes",
                    data={"client_id": str(cid), "total": "0",
                          "concepto": "Pantalón", "garment_tipo": "pantalon"},
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
