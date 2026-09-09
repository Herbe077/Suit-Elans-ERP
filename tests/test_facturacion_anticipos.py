"""Facturación por anticipos bespoke: selector de monto, asiento 1221 en
anticipo y aplicación en el comprobante final (7021)."""


def _mapa(db, asiento_id):
    from decimal import Decimal
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _asiento_de(db, inv):
    from app.models.finanzas import AsientoContable
    return db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "VENTA",
        AsientoContable.origen_id == inv.id).order_by(
        AsientoContable.id.desc()).first()


def test_anticipo_y_final_bespoke(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    from app.models.client import Client
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = Client(nombre="Anticipo", apellidos="Fact", tipo_doc="DNI",
               clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio="SE-ANT-001", client_id=c.id, estado="confirmado",
              canal="sastreria", total=2360.0)
    db.add(o)
    db.flush()
    db.add(Garment(order_id=o.id, tipo="saco", precio=2360.0))
    db.commit()
    oid = o.id
    db.close()
    client.post("/ventas/caja/abrir", data={"saldo_apertura": "0"},
                cookies=auth_cookies)
    r = client.post("/ventas/caja/cobrar",
                    data={"order_id": str(oid), "monto": 1180.0,
                          "metodo": "transferencia"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    # a) Anticipo (defecto sugerido): 1212 / 40111 + 1221
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "ANTICIPO", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    assert inv is not None and inv.tipo == "ANTICIPO"
    assert inv.total == 1180.0 and inv.subtotal == 1000.0 and inv.igv == 180.0
    mapa = _mapa(db, _asiento_de(db, inv).id)
    assert mapa["1212"] == (Decimal("1180"), Decimal("0"))
    assert mapa["40111"] == (Decimal("0"), Decimal("180"))
    assert mapa["1221"] == (Decimal("0"), Decimal("1000"))
    db.close()
    # Sin anticipo pendiente ya no se puede facturar otro anticipo
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "ANTICIPO", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code == 400
    # b) Final: aplica 1221, 1212 por el saldo y base total a 7032
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "SALDO", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    inv2 = db.query(Invoice).filter(Invoice.order_id == oid,
                                    Invoice.tipo == "FINAL").first()
    assert inv2 is not None and inv2.total == 1180.0
    mapa2 = _mapa(db, _asiento_de(db, inv2).id)
    assert mapa2["1221"] == (Decimal("1000"), Decimal("0"))
    assert mapa2["1212"] == (Decimal("1180"), Decimal("0"))
    assert mapa2["40111"] == (Decimal("0"), Decimal("180"))
    assert mapa2["7021"] == (Decimal("0"), Decimal("2000"))
    db.close()
    # UI: pedido totalmente cubierto ya no aparece en el selector
    t = client.get("/ventas/facturacion", cookies=auth_cookies).text
    assert "SE-ANT-001" not in t
