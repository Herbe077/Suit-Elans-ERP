"""Kardex vinculado a pedido/ficha: consumo automático desde ficha y POS,
y registro manual con referencia de pedido."""


def _setup(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.inventory import Fabric
    db = SessionLocal()
    fab = db.query(Fabric).filter(Fabric.codigo == "KX-PED-TELA").first()
    if not fab:
        fab = Fabric(codigo="KX-PED-TELA", nombre="Tela pedido test",
                     stock_metros=20.0, precio_metro=50.0)
        db.add(fab)
        db.commit()
    fid = fab.id
    c = db.query(Client).filter(Client.nro_doc == "KX-PED-DNI").first()
    if not c:
        c = Client(nombre="Kardex", apellidos="Pedido", tipo_doc="DNI",
                   nro_doc="KX-PED-DNI", clasificacion="Nuevo")
        db.add(c)
        db.commit()
    cid = c.id
    db.close()
    return fid, cid


def test_ficha_genera_salida_taller_vinculada(client, auth_cookies):
    """Ficha + tela → reserva y SALIDA_TALLER en Kardex con order_id."""
    fid, cid = _setup(client, auth_cookies)
    r = client.post(f"/produccion/fichas/{cid}/pedidos",
                    data={"tipo": "saco", "tela_id": str(fid), "precio": "1500",
                          "concepto": "Terno kardex"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.order import Garment, Order
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Terno kardex").first()
    assert o is not None
    g = db.query(Garment).filter(Garment.order_id == o.id).first()
    assert g is not None and g.tela_reservada is True
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == o.id,
        MovimientoKardex.tipo_movimiento == "SALIDA_TALLER").first()
    assert k is not None and k.cantidad == 2.0  # consumo saco
    prod = db.query(ProductoInsumo).filter(
        ProductoInsumo.sku == "KX-PED-TELA").first()
    assert prod is not None and prod.stock_reservado == 2.0
    db.close()


def test_kardex_manual_con_pedido_y_folio(client, auth_cookies):
    """Registro manual de Kardex con Pedido/Ficha → guarda order_id y
    el historial muestra el folio (SE-...)."""
    _setup(client, auth_cookies)
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.order import Order
    db = SessionLocal()
    prod = db.query(ProductoInsumo).filter(
        ProductoInsumo.sku == "KX-PED-TELA").first()
    pid = prod.id
    o = db.query(Order).filter(Order.concepto == "Terno kardex").first()
    oid, folio = o.id, o.folio
    db.close()
    r = client.post("/inventario/almacen/kardex",
                    data={"producto_id": str(pid), "tipo_movimiento": "SALIDA_TALLER",
                          "cantidad": "1", "orden_venta_id": str(oid),
                          "observacion": "corte manual", "alcance": "mp"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == oid,
        MovimientoKardex.observacion == "corte manual").first()
    assert k is not None
    db.close()
    t = client.get("/inventario/almacen", cookies=auth_cookies).text
    assert f"Pedido {folio}" in t  # folio SE-... como referencia del egreso
    assert 'name="orden_venta_id"' in t  # selector Pedido/Ficha en el form


def test_pos_genera_salida_taller_vinculada(client, auth_cookies):
    """POS + tela → SALIDA_TALLER en Kardex vinculada al pedido."""
    fid, cid = _setup(client, auth_cookies)
    r = client.post("/ventas/pos/vender",
                    data={"client_id": str(cid), "concepto": "Saco POS kardex",
                          "precio": "1200", "garment_tipo": "saco",
                          "tela_id": str(fid), "monto_cobro": "0"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex
    from app.models.order import Order
    db = SessionLocal()
    o = db.query(Order).filter(Order.concepto == "Saco POS kardex").first()
    assert o is not None
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == o.id,
        MovimientoKardex.tipo_movimiento == "SALIDA_TALLER").first()
    assert k is not None and k.cantidad == 2.0
    db.close()
