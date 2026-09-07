"""Flujo B2B: empresa + colaborador, anticipo 50%, Factura forzada F001 y
saldo en CxC (cuenta 12) a nombre de la empresa."""


def test_pos_b2b_factura_y_cxc_a_empresa(client, auth_cookies):
    # Empresa + colaborador
    r = client.post("/comercial/clientes/empresa",
                    data={"nombre_comercial": "FacturaB2B S.A.C.", "ruc": "20600000001",
                          "descuento_pct": "0"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.company import Company
    db = SessionLocal()
    emp = db.query(Company).filter(
        Company.nombre_comercial == "FacturaB2B S.A.C.").first()
    assert emp is not None and emp.ruc == "20600000001"
    eid = emp.id
    db.close()
    r = client.post(f"/comercial/clientes/empresa/{eid}/colaboradores",
                    data={"nombre": "Cobro", "apellidos": "Colab",
                          "telefono": "911222333"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.client import Client
    db = SessionLocal()
    col = db.query(Client).filter(Client.nombre == "Cobro").first()
    assert col is not None and col.company_id == eid
    lid = col.id
    db.close()
    # Turno + venta POS con anticipo del 50%
    client.post("/ventas/caja/abrir", data={"saldo_apertura": "0"},
                cookies=auth_cookies)
    r = client.post("/ventas/pos/vender",
                    data={"company_id": str(eid), "colaborador_id": str(lid),
                          "concepto": "Terno directivo", "precio": "2000",
                          "garment_tipo": "saco", "monto_cobro": "1000",
                          "metodo": "transferencia"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.order import Order
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Terno directivo").first()
    assert o is not None
    assert o.company_id == eid and o.client_id == lid
    assert o.anticipo == 1000.0
    oid = o.id
    db.close()
    # Se pide Boleta a propósito: empresa con RUC fuerza Factura F001
    r = client.post("/ventas/facturacion/emitir",
                    data={"serie": "B001", "order_id": str(oid)},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.billing import Invoice
    from app.models.finanzas import CuentaPorCobrar
    db = SessionLocal()
    inv = db.query(Invoice).filter(Invoice.order_id == oid).first()
    assert inv is not None and inv.serie == "F001"
    assert inv.company_id == eid  # titular de la factura: la empresa
    cxc = db.query(CuentaPorCobrar).filter(
        CuentaPorCobrar.order_id == oid).first()
    assert cxc is not None and cxc.company_id == eid
    assert cxc.monto_total == 2000.0 and cxc.monto_pagado == 1000.0
    assert cxc.saldo_pendiente == 1000.0 and cxc.estado == "COBRADO_PARCIAL"
    db.close()
    # UI: POS anuncia comprobante y facturación avisa la serie forzada
    assert "Asignar a / Beneficiario" in client.get(
        "/ventas/pos", cookies=auth_cookies).text
    assert "F001" in client.get("/ventas/facturacion",
                                cookies=auth_cookies).text
