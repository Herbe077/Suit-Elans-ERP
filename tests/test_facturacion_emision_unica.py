"""Emisión única de comprobantes + visibilidad Facturar en órdenes.

- Segunda emisión sobre orden con comprobante activo → 400 con alerta.
- Tras anular, se puede emitir de nuevo.
- Órdenes entregadas sin comprobante muestran "Facturar →"; con
  comprobante muestran badge PDF y ocultan el enlace.
- El selector de facturación excluye pedidos ya cubiertos.
"""


def _orden(db_tag):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order
    db = SessionLocal()
    c = Client(nombre="EmiUnica", apellidos=db_tag, tipo_doc="DNI",
               nro_doc=f"EU-{db_tag}", clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=f"SE-EU-{db_tag}", client_id=c.id, estado="confirmado",
              canal="sastreria", total=1180.0)
    db.add(o)
    db.commit()
    oid = o.id
    db.close()
    return oid


def test_doble_emision_rechazada(client, auth_cookies):
    oid = _orden("DOBLE")
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "TOTAL", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    folio = f"{inv.serie}-{inv.numero}"
    db.close()
    r2 = client.post("/ventas/facturacion/emitir",
                     data={"serie": "B001", "order_id": str(oid),
                           "modo": "TOTAL", "monto": ""},
                     cookies=auth_cookies)
    assert r2.status_code == 400
    assert "ya cuenta con un comprobante emitido activo" in r2.text
    assert folio in r2.text


def test_reemision_tras_anular(client, auth_cookies):
    oid = _orden("REEMIT")
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "TOTAL", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    iid = inv.id
    db.close()
    r = client.post(f"/ventas/facturacion/{iid}/anular", cookies=auth_cookies)
    assert r.status_code in (200, 303)
    r2 = client.post("/ventas/facturacion/emitir",
                     data={"serie": "B001", "order_id": str(oid),
                           "modo": "TOTAL", "monto": ""},
                     cookies=auth_cookies)
    assert r2.status_code in (200, 303)
    db = SessionLocal()
    n = db.query(Invoice).filter(Invoice.order_id == oid,
                                 Invoice.estado != "anulada").count()
    db.close()
    assert n == 1


def test_ordenes_entregadas_facturar_o_badge(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    oid_sin = _orden("SINCOMP")
    oid_con = _orden("CONCOMP")
    db = SessionLocal()
    db.query(Order).filter(Order.id.in_([oid_sin, oid_con])).update(
        {"estado": "entregado"}, synchronize_session=False)
    db.commit()
    db.close()
    client.post("/ventas/facturacion/emitir",
                data={"serie": "B001", "order_id": str(oid_con),
                      "modo": "TOTAL", "monto": ""},
                cookies=auth_cookies)
    t = client.get("/ventas/ordenes", cookies=auth_cookies).text
    # sin comprobante: enlace Facturar visible
    assert f"/ventas/facturacion?order_id={oid_sin}" in t
    # con comprobante: badge PDF y sin enlace
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid_con).first()
    folio = f"{inv.serie}-{inv.numero}"
    db.close()
    assert folio in t
    assert f"/ventas/facturacion?order_id={oid_con}" not in t


def test_selector_excluye_cubiertos(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    oid = _orden("SELECT")
    client.post("/ventas/facturacion/emitir",
                data={"serie": "B001", "order_id": str(oid),
                      "modo": "TOTAL", "monto": ""},
                cookies=auth_cookies)
    db = SessionLocal()
    folio = db.get(Order, oid).folio
    db.close()
    t = client.get("/ventas/facturacion", cookies=auth_cookies).text
    assert folio not in t
