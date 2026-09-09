"""Tope SUNAT S/ 700: Público General solo bajo el tope en POS/cobro/factura."""


def _orden(total, tag="X"):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    o = Order(folio=f"SE-SUNAT-{tag}-{int(total)}", client_id=None,
              company_id=None, estado="confirmado", canal="comercial",
              total=float(total))
    db.add(o)
    db.commit()
    oid = o.id
    db.close()
    return oid


def test_pos_pg_750_bloqueado(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    n = db.query(Order).count()
    db.close()
    r = client.post("/ventas/pos/vender",
                    data={"publico_general": "1", "concepto": "Terno SUNAT",
                          "precio": "750", "garment_tipo": "saco",
                          "monto_cobro": "0"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error=sunat" in r.headers["location"]
    db = SessionLocal()
    assert db.query(Order).filter(
        Order.concepto == "Terno SUNAT").first() is None
    assert db.query(Order).count() == n
    db.close()
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert "SUNAT" in t  # la alerta existe en la vista (se muestra con ?error=sunat)


def test_cobro_pg_750_exige_dni(client, auth_cookies):
    oid = _orden(750, "COB")
    client.post("/ventas/caja/abrir", data={"saldo_apertura": "0"},
                cookies=auth_cookies)
    r = client.post("/ventas/cobro",
                    data={"order_id": str(oid), "monto": "750",
                          "metodo": "efectivo", "back": "/ventas/ordenes"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error=sunat" in r.headers["location"]
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    assert db.get(Order, oid).anticipo == 0
    db.close()


def test_factura_pg_750_bloqueada(client, auth_cookies):
    oid = _orden(750, "FAC")
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "TOTAL", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code == 400
    assert "SUNAT" in r.text


def test_pos_pg_150_ok(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    r = client.post("/ventas/pos/vender",
                    data={"publico_general": "1", "concepto": "Venta PG 150",
                          "precio": "150", "garment_tipo": "camisa",
                          "monto_cobro": "0"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Venta PG 150").first()
    assert o is not None and o.total == 150.0
    assert o.client_id is None and o.company_id is None
    db.close()


def test_factura_pg_150_usa_cliente_generico(client, auth_cookies):
    """Boleta PG bajo el tope: emite y traza al cliente genérico S/D."""
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    from app.models.client import Client
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    from app.models.order import Order
    db = SessionLocal()
    o = Order(folio="SE-SUNAT-PG150", client_id=None, company_id=None,
              estado="confirmado", canal="comercial", total=150.0)
    db.add(o)
    db.commit()
    oid = o.id
    db.close()
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "TOTAL", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    assert inv is not None and inv.estado == "emitida"
    pg = db.query(Client).filter(Client.nombre == "Público General",
                                 Client.apellidos == "S/D").one()
    assert db.get(Order, oid).client_id is None  # el pedido sigue PG
    asiento_ids = [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "VENTA",
        AsientoContable.origen_id == inv.id)]
    from app.models.finanzas import CuentaContable
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(asiento_ids)):
        cta = db.get(CuentaContable, l.cuenta_id)
        if cta.analitica:
            assert l.cliente_id == pg.id  # trazabilidad total al genérico
    cids = {l.cliente_id for l in db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id.in_(asiento_ids),
        LineaAsientoContable.cliente_id.is_not(None))}
    assert cids == {pg.id}
    db.close()


def test_tope_exactos_y_con_cliente():
    from app.services.peru import SUNAT_BOLETA_PG_TOPE, exigir_cliente_sunat
    import pytest
    assert SUNAT_BOLETA_PG_TOPE == 700.0
    exigir_cliente_sunat(699.99, False)  # bajo el tope: ok
    exigir_cliente_sunat(5000.0, True)  # con cliente: ok
    with pytest.raises(ValueError, match="SUNAT"):
        exigir_cliente_sunat(700.0, False)  # igual al tope: bloquea
    with pytest.raises(ValueError, match="SUNAT"):
        exigir_cliente_sunat(750.0, False)
