"""Item 3: anticipo 50% -> el saldo solo permite facturar el 50% restante."""


def _orden_1180(tag):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order
    db = SessionLocal()
    c = Client(nombre="Saldo", apellidos=tag, tipo_doc="DNI",
               nro_doc=f"SL-{tag}", clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=f"SE-SL-{tag}", client_id=c.id, estado="confirmado",
              canal="sastreria", total=1180.0, anticipo=590.0)
    db.add(o)
    db.commit()
    oid = o.id
    db.close()
    return oid


def test_saldo_solo_permite_restante(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    oid = _orden_1180("T1")
    # anticipo 50%
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "ANTICIPO", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    ant = db.query(Invoice).filter(Invoice.order_id == oid,
                                   Invoice.tipo == "ANTICIPO").one()
    assert ant.total == 590.0
    db.close()
    # saldo con monto explícito mayor al restante -> se topa a 590
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "SALDO", "monto": "1180"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    fin = db.query(Invoice).filter(Invoice.order_id == oid,
                                   Invoice.tipo == "FINAL").one()
    assert fin.total == 590.0
    total_fact = sum(
        i.total for i in db.query(Invoice).filter(
            Invoice.order_id == oid, Invoice.estado != "anulada").all())
    assert total_fact == 1180.0  # sin doble facturación
    db.close()
    # TOTAL tras anticipo parcial también se topa al restante
    db = SessionLocal()
    from app.models.order import Order as _O
    from app.models.client import Client as _C
    c2 = _C(nombre="Saldo", apellidos="T2", tipo_doc="DNI", nro_doc="SL-T2",
            clasificacion="Nuevo")
    db.add(c2)
    db.commit()
    db.refresh(c2)
    o2 = _O(folio="SE-SL-T2", client_id=c2.id, estado="confirmado",
            canal="sastreria", total=1180.0, anticipo=590.0)
    db.add(o2)
    db.commit()
    db.refresh(o2)
    oid2 = o2.id
    db.close()
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid2),
                          "modo": "ANTICIPO", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid2),
                          "modo": "TOTAL", "monto": "1180"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    tot = db.query(Invoice).filter(Invoice.order_id == oid2,
                                   Invoice.tipo == "TOTAL").one()
    assert tot.total == 590.0
    db.close()
