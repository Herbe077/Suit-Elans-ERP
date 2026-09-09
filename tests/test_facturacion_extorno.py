"""Fix 1: pre-selección ?order_id= en facturación/comprobantes.
Fix 2: anulación de comprobante genera asiento de extorno (neto cero)."""


def _mapa(db, asiento_id):
    from decimal import Decimal
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _nuevo_pedido(db, folio, total):
    from app.models.client import Client
    from app.models.order import Order
    c = Client(nombre="Extorno", apellidos=folio, tipo_doc="DNI",
               clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=folio, client_id=c.id, estado="confirmado",
              canal="sastreria", total=total)
    db.add(o)
    db.commit()
    oid = o.id
    db.close()
    return oid


def test_facturacion_preselcciona_order_id(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    oid = _nuevo_pedido(db, "SE-PRESEL-001", 500.0)
    for url in (f"/ventas/facturacion?order_id={oid}",
                f"/ventas/comprobantes?order_id={oid}"):
        r = client.get(url, cookies=auth_cookies)
        assert r.status_code == 200
        assert f'value="{oid}" selected' in r.text
    # order_id inválido o inexistente: 200 sin pre-selección, sin romper
    import re
    r = client.get("/ventas/facturacion?order_id=999999",
                   cookies=auth_cookies)
    assert r.status_code == 200
    assert not re.search(r"<option[^>]*selected", r.text)
    r = client.get("/ventas/ordenes", cookies=auth_cookies)
    assert r.status_code == 200
    assert f"/ventas/facturacion?order_id={oid}" in r.text


def test_anular_genera_extorno_y_netea_saldos(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.billing import Invoice
    from app.models.finanzas import AsientoContable
    from app.models.ventas import ComprobanteVenta
    from app.services.finanzas import saldo_cuenta

    db = SessionLocal()
    oid = _nuevo_pedido(db, "SE-EXT-001", 110.0)
    db = SessionLocal()
    antes = {cod: saldo_cuenta(db, cod) for cod in ("1212", "40111", "7021")}
    db.close()
    # Emite TOTAL 110.00 → 1212/40111+7011 (base 93.22, IGV 16.78)
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid),
                          "modo": "TOTAL", "monto": ""},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    assert inv is not None and inv.estado == "emitida"
    assert inv.total == 110.0 and inv.subtotal == 93.22 and inv.igv == 16.78
    orig = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "VENTA",
        AsientoContable.origen_id == inv.id).one()
    mapa_orig = _mapa(db, orig.id)
    assert mapa_orig["1212"] == (Decimal("110"), Decimal("0"))
    assert mapa_orig["40111"] == (Decimal("0"), Decimal("16.78"))
    assert mapa_orig["7021"] == (Decimal("0"), Decimal("93.22"))
    iid = inv.id
    db.close()
    # Anula → extorno espejo + original ANULADO, todo en una transacción
    r = client.post(f"/ventas/facturacion/{iid}/anular",
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    inv = db.get(Invoice, iid)
    assert inv.estado == "anulada"
    ext = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "EXTORNO",
        AsientoContable.origen_id == iid).one()
    mapa_ext = _mapa(db, ext.id)
    assert mapa_ext["1212"] == (Decimal("0"), Decimal("110"))
    assert mapa_ext["40111"] == (Decimal("16.78"), Decimal("0"))
    assert mapa_ext["7021"] == (Decimal("93.22"), Decimal("0"))
    assert f"Extorno {orig.numero}" in ext.glosa
    orig_numero = orig.numero
    db.close()
    db = SessionLocal()
    assert db.get(AsientoContable, orig.id).estado == "ANULADO"
    assert f"Extorno {orig_numero}" in db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "EXTORNO",
        AsientoContable.origen_id == iid).one().glosa
    cv = db.query(ComprobanteVenta).filter(
        ComprobanteVenta.legacy_invoice_id == iid).first()
    assert cv is not None and cv.estado == "anulada"
    # Mayor/Balance: el efecto neto vuelve a cero (saldos previos intactos)
    despues = {cod: saldo_cuenta(db, cod) for cod in ("1212", "40111", "7021")}
    assert despues == antes
    db.close()
    # Idempotente: segunda anulación no duplica el extorno
    r = client.post(f"/ventas/facturacion/{iid}/anular",
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "EXTORNO",
        AsientoContable.origen_id == iid).count() == 1
    db.close()
    # Comprobante inexistente → 400 controlado (no 500)
    r = client.post("/ventas/facturacion/999999/anular",
                    cookies=auth_cookies)
    assert r.status_code == 400
