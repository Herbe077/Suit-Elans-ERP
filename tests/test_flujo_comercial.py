"""Flujo comercial completo: lead → cotización → pedido → cobro → factura."""


def test_lead_y_cotizacion(client, auth_cookies):
    r = client.post("/comercial/crm/leads", data={"nombre": "Cliente Test", "telefono": "999000111",
                                        "tipo_cliente": "individual", "origen": "web",
                                        "interes": "sastreria"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    r = client.post("/comercial/clientes/empresa", data={"nombre_comercial": "Test Corp S.A.",
                                       "descuento_pct": 10}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.company import Company
    from app.models.crm import Lead
    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.nombre == "Cliente Test").first()
    comp = db.query(Company).filter(Company.nombre_comercial == "Test Corp S.A.").first()
    assert lead and comp
    # Cotización corporativa hereda 10% de descuento
    r = client.post("/comercial/crm/cotizaciones", data={"lead_id": str(lead.id),
                                               "company_id": str(comp.id)},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.crm import Quotation
    q = db.query(Quotation).order_by(Quotation.id.desc()).first()
    assert q.descuento_pct == 10.0
    r = client.post(f"/comercial/crm/cotizaciones/{q.id}/lineas",
                    data={"concepto": "Terno medida", "categoria": "prenda_medida",
                          "garment_tipo": "saco", "cantidad": 1,
                          "precio_unitario": 2500}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db.refresh(q)
    assert q.total == 2250.0  # 2500 - 10%
    # Aprobar y convertir
    client.post(f"/comercial/crm/cotizaciones/{q.id}/estado", data={"estado": "aprobada"},
                cookies=auth_cookies)
    r = client.post(f"/comercial/crm/cotizaciones/{q.id}/convertir", cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.order import Garment, Order
    order = db.query(Order).filter(Order.quotation_id == q.id).first()
    assert order and order.canal == "corporativo" and order.total == 2250.0
    assert db.query(Garment).filter(Garment.order_id == order.id).count() == 1
    db.close()


def test_cobro_y_factura(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.order import Order
    db = SessionLocal()
    order = db.query(Order).order_by(Order.id.desc()).first()
    assert order.saldo == 2250.0
    client.post("/ventas/caja/abrir", data={"saldo_apertura": "0"}, cookies=auth_cookies)
    r = client.post("/ventas/caja/cobrar", data={"order_id": str(order.id), "monto": 1000,
                                          "metodo": "transferencia"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db.refresh(order)
    assert order.anticipo == 1000.0
    r = client.post("/ventas/facturacion/emitir", data={"serie": "B001",
                                                 "order_id": str(order.id)},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.models.billing import Invoice
    inv = db.query(Invoice).order_by(Invoice.id.desc()).first()
    # Precios con IGV incluido: el total es el precio final, sin recargo +18%.
    assert inv.numero == "000001" and inv.total == 2250.0
    assert inv.subtotal == round(2250.0 / 1.18, 2)
    assert inv.igv == round(2250.0 - inv.subtotal, 2)
    assert round(inv.subtotal + inv.igv, 2) == inv.total  # 70 + 4011 = 12
    r = client.get(f"/ventas/facturacion/{inv.id}.pdf", cookies=auth_cookies)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    db.close()


def test_compras_y_recepcion(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventory import Fabric
    from app.models.purchasing import PurchaseLine, PurchaseOrder
    from app.models.purchasing import Supplier
    db = SessionLocal()
    sup = Supplier(nombre="Prov Test")
    db.add(sup)
    fab = Fabric(codigo="T-TEST", nombre="Tela test", stock_metros=10.0)
    db.add(fab)
    db.commit()
    stock_antes = fab.stock_metros
    r = client.post("/inventario/compras/oc", data={"supplier_id": str(sup.id)}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    po = db.query(PurchaseOrder).order_by(PurchaseOrder.id.desc()).first()
    r = client.post(f"/inventario/compras/oc/{po.id}/lineas",
                    data={"item_tipo": "fabric", "item_id": str(fab.id),
                          "descripcion": "Tela test", "cantidad": 5,
                          "costo_unitario": 100}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    line = db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).first()
    r = client.post(f"/inventario/compras/linea/{line.id}/recibir", data={"cantidad": 5},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db.refresh(fab)
    db.refresh(po)
    assert fab.stock_metros == stock_antes + 5
    assert po.estado == "recibida"
    db.close()
