"""Órdenes: acciones por estado, modal de cobro y tipo requerido."""


def _setup(db):
    from app.models.client import Client
    from app.models.order import Garment, Order
    c = db.query(Client).filter(Client.nro_doc == "ORD-VT-DNI").first()
    if not c:
        c = Client(nombre="Orden", apellidos="Ventas", tipo_doc="DNI",
                   nro_doc="ORD-VT-DNI")
        db.add(c)
        db.commit()
        db.refresh(c)
    def _mk(folio, estado, total, anticipo, gtipo, taller):
        o = db.query(Order).filter(Order.folio == folio).first()
        if not o:
            o = Order(folio=folio, client_id=c.id, estado=estado, total=total,
                      anticipo=anticipo)
            db.add(o)
            db.flush()
            db.add(Garment(order_id=o.id, tipo=gtipo, precio=total,
                           estado_taller=taller))
            db.commit()
            db.refresh(o)
        return o.id
    return (c.id,
            _mk("SE-ORD-COT", "cotizado", 1000.0, 0.0, "saco", "POR_CORTAR"),
            _mk("SE-ORD-CONF", "confirmado", 1000.0, 200.0, "saco", "POR_CORTAR"),
            _mk("SE-ORD-READY", "confirmado", 1000.0, 1000.0, "saco", "CALIDAD_OK"))


def _cleanup(db):
    from app.models.order import Garment, Order, Payment
    for folio in ("SE-ORD-COT", "SE-ORD-CONF", "SE-ORD-READY", "SE-ORD-RAPIDA"):
        o = db.query(Order).filter(Order.folio == folio).first()
        if o:
            db.query(Payment).filter(Payment.order_id == o.id).delete()
            db.query(Garment).filter(Garment.order_id == o.id).delete()
            db.delete(o)
    db.commit()


def test_cotizado_solo_confirmar(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _setup(db)
    db.close()
    t = client.get("/ventas/ordenes?estado=cotizado", cookies=auth_cookies).text
    assert "Confirmar y Cobrar Anticipo" in t
    assert "Marcar entregado" not in t and "Facturar" not in t
    assert "En taller" not in t
    assert t.count('name="monto"') == 1  # un solo modal unificado
    assert 'id="modal-cobro"' in t


def test_confirmado_entrega_condicionada(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _setup(db)
    db.close()
    t = client.get("/ventas/ordenes?estado=confirmado", cookies=auth_cookies).text
    assert "Registrar Pago / Saldo" in t
    # SE-ORD-CONF (saldo + taller no listo): sin Marcar entregado
    # SE-ORD-READY (saldo 0 + CALIDAD_OK): con Marcar entregado
    assert t.count("Marcar entregado") == 1


def test_entregar_bloqueado_con_saldo_o_taller(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    _setup(db)
    o = db.query(Order).filter(Order.folio == "SE-ORD-CONF").first()
    oid = o.id
    db.close()
    r = client.post(f"/ventas/ordenes/{oid}/entregar", cookies=auth_cookies,
                    follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    db = SessionLocal()
    assert db.get(Order, oid).estado == "confirmado"
    _cleanup(db)
    db.close()


def test_cotizacion_rapida_exige_tipo_y_deriva(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Garment, Order
    db = SessionLocal()
    cid, _, _, _ = _setup(db)
    n = db.query(Order).count()
    db.close()
    r = client.post("/ventas/ordenes",
                    data={"client_id": str(cid), "concepto": "Sin tipo",
                          "total": "500"}, cookies=auth_cookies,
                    follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    assert db.query(Order).count() == n  # sin tipo no se crea
    db.close()
    r = client.post("/ventas/ordenes",
                    data={"client_id": str(cid), "concepto": "Terno",
                          "total": "1500", "garment_tipo": "saco"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    o = db.query(Order).order_by(Order.id.desc()).first()
    assert o.concepto == "Terno"
    gs = db.query(Garment).filter(Garment.order_id == o.id).all()
    assert len(gs) == 1 and gs[0].tipo == "saco"  # deriva al taller
    o.folio = "SE-ORD-RAPIDA"
    db.commit()
    _cleanup(db)
    db.close()


def test_cobro_desde_modal(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    _setup(db)
    o = db.query(Order).filter(Order.folio == "SE-ORD-COT").first()
    oid = o.id
    db.close()
    client.post("/ventas/caja/abrir", data={"saldo_apertura": "0"},
                cookies=auth_cookies)
    r = client.post("/ventas/cobro",
                    data={"order_id": str(oid), "monto": "500",
                          "metodo": "efectivo", "tipo": "ADELANTO",
                          "back": "/ventas/ordenes"}, cookies=auth_cookies,
                    follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    assert db.get(Order, oid).anticipo == 500.0
    _cleanup(db)
    db.close()
