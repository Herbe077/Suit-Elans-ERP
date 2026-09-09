"""Plan Operativo: contabilización automática en el Libro Diario.

Bespoke: despacho => CONSUMO_MPD 2311x/2411x (6131? no: auxiliares
2311x/2521x); entrega => COSTO_VENTA_BESPOKE 6921x/2311x directo del WIP.
Colección: OP => consumo 2311x/2411x + MOD 2311x/791 + CIERRE 2111x/2311x.
"""


def _setup(tag, categoria="TELA", stock=20.0, cpp=50.0, bespoke=True,
           total=1000.0, anticipo=0.0, garment_estado="POR_CORTAR"):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.inventario import ProductoInsumo
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = Client(nombre="F3", apellidos=tag, tipo_doc="DNI",
               clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=f"SE-F3-{tag}", client_id=c.id, estado="confirmado",
              canal="sastreria", total=total, anticipo=anticipo)
    db.add(o)
    db.commit()
    gid = None
    if bespoke:
        g = Garment(order_id=o.id, tipo="saco", estado_taller=garment_estado)
        db.add(g)
        db.commit()
        gid = g.id
    p = ProductoInsumo(sku=f"F3-{tag}", nombre=f"Insumo {tag}",
                       categoria=categoria, unidad_medida="METROS",
                       costo_unitario=cpp, costo_promedio=cpp,
                       stock_fisico=stock)
    db.add(p)
    db.commit()
    ids = (p.id, o.id, o.folio, gid)
    db.close()
    return ids


def _mapa(db, asiento_id):
    from decimal import Decimal
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _despachar(client, auth_cookies, pid, oid, cantidad):
    r = client.post("/inventario/reservas/despachar",
                    data={"producto_id": str(pid),
                          "orden_venta_id": str(oid),
                          "cantidad": str(cantidad)},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers.get("location", ""), \
        r.headers.get("location", "")
    return r


def _dims_linea(db, asiento_id):
    from app.models.finanzas import LineaAsientoContable
    return db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == asiento_id).all()


def test_despacho_tela_a_wip_2311_2411(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex
    from app.services.inventory import reservar_insumo
    pid, oid, folio, _gid = _setup("WIP1")
    db = SessionLocal()
    reservar_insumo(db, pid, 2.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    _despachar(client, auth_cookies, pid, oid, 2.0)
    db = SessionLocal()
    # consumo directo a WIP 2311/2411 (único asiento, sin 2111/7111)
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == f"Despacho reserva {folio}").one()
    assert _mapa(db, k.asiento_id)["2311"] == (Decimal("100"), Decimal("0"))
    assert _mapa(db, k.asiento_id)["2411"] == (Decimal("0"), Decimal("100"))
    lineas = _dims_linea(db, k.asiento_id)
    assert any(l.producto_id == pid and l.centro_costo_id is not None
               for l in lineas)
    db.close()


def test_despacho_avio_a_wip_2311_2521(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex
    from app.services.inventory import reservar_insumo
    pid, oid, folio, _gid = _setup("WIP2", categoria="AVIO", stock=100.0,
                                   cpp=5.0)
    db = SessionLocal()
    reservar_insumo(db, pid, 10.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    _despachar(client, auth_cookies, pid, oid, 10.0)
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == f"Despacho reserva {folio}").one()
    assert _mapa(db, k.asiento_id)["2311"] == (Decimal("50"), Decimal("0"))
    assert _mapa(db, k.asiento_id)["2521"] == (Decimal("0"), Decimal("50"))
    db.close()


def test_despacho_no_bespoke_igual_consumo(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex
    from app.services.inventory import reservar_insumo
    pid, oid, folio, _gid = _setup("WIP3", bespoke=False)
    db = SessionLocal()
    reservar_insumo(db, pid, 2.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    _despachar(client, auth_cookies, pid, oid, 2.0)
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == f"Despacho reserva {folio}").one()
    assert k.asiento_id is not None
    db.close()


def test_entrega_bespoke_a_costo_ventas_6921_2311(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable
    from app.services.inventory import reservar_insumo
    pid, oid, folio, _gid = _setup("WIP4", total=1000.0, anticipo=1000.0,
                                   garment_estado="CALIDAD_OK")
    db = SessionLocal()
    reservar_insumo(db, pid, 2.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    _despachar(client, auth_cookies, pid, oid, 2.0)
    r = client.post(f"/ventas/ordenes/{oid}/entregar", cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    from app.models.order import Order
    assert db.get(Order, oid).estado == "entregado"
    # Absorción automática de MOD (saco sin tareo: 600 min SAM estándar ×
    # tarifa) al WIP + salida a costo de ventas desde el WIP (sin 23-PT).
    from app.services import finanzas as _fz
    _tarifa = float(_fz.tarifa_minuto_taller(db) or 0.35)
    _mo = round(600.0 * _tarifa, 2)
    _total = round(100.0 + _mo, 2)
    a = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "COSTO_VENTAS",
        AsientoContable.origen_id == oid).one()
    mapa = _mapa(db, a.id)
    assert mapa["6921"] == (Decimal(str(_total)), Decimal("0"))
    assert mapa["2311"] == (Decimal("0"), Decimal(str(_total)))
    assert "2111" not in mapa
    ab = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "ABSORCION_MOD",
        AsientoContable.origen_id == oid).one()
    assert _mapa(db, ab.id)["9211"] == (Decimal("0"), Decimal(str(_mo)))
    db.close()


def test_op_coleccion_consumo_cierre_mod(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    from app.models.finanzas import AsientoContable
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.produccion import OrdenProduccion
    from app.models.user import User
    db = SessionLocal()
    admin = db.query(User).filter(
        User.email == "admin@suitelans.mx").first()
    ins = ProductoInsumo(sku="F3-OP-TELA", nombre="Tela OP", categoria="TELA",
                         unidad_medida="METROS", costo_unitario=10.0,
                         costo_promedio=10.0, stock_fisico=100.0)
    db.add(ins)
    db.commit()
    p = Product(codigo="F3-OP-COL", nombre="Saco OP", linea="comercial")
    db.add(p)
    db.commit()
    v = ProductVariant(product_id=p.id, talla="M", sku="SKU-F3-OP",
                       stock=0.0, precio=500.0, costo_unitario=0.0)
    db.add(v)
    db.commit()
    op = OrdenProduccion(orden_venta_id=None, codigo_qr="OP-F3-1",
                         estado="EN_CONFECCION",
                         sastre_asignado_id=admin.id if admin else None)
    db.add(op)
    db.commit()
    ids = (ins.id, v.id, op.id)
    db.close()
    r = client.post(f"/produccion/op/{ids[2]}/finalizar",
                    json={"variant_id": ids[1], "cantidad": 10,
                          "insumos": [{"producto_insumo_id": ids[0],
                                       "cantidad_por_unidad": 2.0}],
                          "mano_obra_directa": 150.0},
                    cookies=auth_cookies,
                    headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    # consumo a WIP: 2311/2411 (20m x 10 = 200)
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.doc_ref == "OP-F3-1",
        MovimientoKardex.tipo_movimiento == "SALIDA_CONSUMO_TALLER").one()
    assert _mapa(db, k.asiento_id)["2311"] == (Decimal("200"), Decimal("0"))
    assert _mapa(db, k.asiento_id)["2411"] == (Decimal("0"), Decimal("200"))
    # MOD imputada 2311/791 por 150
    w = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == ids[2],
        AsientoContable.glosa.contains("MOD OP")).one()
    assert _mapa(db, w.id)["2311"] == (Decimal("150"), Decimal("0"))
    assert _mapa(db, w.id)["791"] == (Decimal("0"), Decimal("150"))
    # cierre: alta PT 2111/2311 por el costo total (200 + 150)
    a = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == ids[2],
        AsientoContable.glosa.contains("Cierre OP")).one()
    assert _mapa(db, a.id)["2111"] == (Decimal("350"), Decimal("0"))
    assert _mapa(db, a.id)["2311"] == (Decimal("0"), Decimal("350"))
    db.close()
