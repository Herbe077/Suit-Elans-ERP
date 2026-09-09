"""Reservas activas + despacho atómico + contabilización de salidas.

- Vista /inventario/reservas lista pendientes y despacha (físico + reservado
  + Kardex valorizado + asiento, un solo commit).
- Telas → DEBE 6111 / HABER 2411; auxiliares → DEBE 6131 / HABER 2521.
- Salida manual con pedido libera la reserva (no duplica descuentos).
- Costo 0 no bloquea la salida (Kardex sin asiento).
"""


def _setup(db, tag, categoria="TELA", stock=20.0, cpp=50.0, total=1500.0):
    from app.models.client import Client
    from app.models.inventario import ProductoInsumo
    from app.models.order import Order
    c = Client(nombre="Res", apellidos=tag, tipo_doc="DNI",
               clasificacion="Nuevo")
    db.add(c)
    db.commit()
    o = Order(folio=f"SE-RES-{tag}", client_id=c.id, estado="confirmado",
              canal="sastreria", total=total)
    db.add(o)
    db.commit()
    p = ProductoInsumo(sku=f"RSK-{tag}", nombre=f"Insumo {tag}",
                       categoria=categoria, unidad_medida="METROS",
                       costo_unitario=cpp, costo_promedio=cpp,
                       stock_fisico=stock)
    db.add(p)
    db.commit()
    ids = (p.id, o.id, o.folio)
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


def test_reservas_vista_y_despacho_tela_6111_2411(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.services.inventory import reservar_insumo
    db = SessionLocal()
    pid, oid, folio = _setup(db, "T01")
    db = SessionLocal()
    reservar_insumo(db, pid, 2.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    t = client.get("/inventario/reservas", cookies=auth_cookies).text
    assert folio in t and "Aprobar y Despachar Material" in t
    r = client.post("/inventario/reservas/despachar",
                    data={"producto_id": str(pid),
                          "orden_venta_id": str(oid), "cantidad": "2"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(ProductoInsumo, pid)
    assert p.stock_fisico == 18.0 and (p.stock_reservado or 0) == 0.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == f"Despacho reserva {folio}").one()
    assert k.tipo_movimiento == "SALIDA_CONSUMO_TALLER" and k.cantidad == 2.0
    assert k.costo_unitario == 50.0 and k.costo_total == 100.0
    assert k.orden_venta_id == oid and k.asiento_id is not None
    a = db.get(AsientoContable, k.asiento_id)
    assert a.origen_tipo == "INVENTARIO"
    assert a.glosa == f"Consumo de almacén - Orden {folio}"
    mapa = _mapa(db, a.id)
    assert mapa["2311"] == (Decimal("100"), Decimal("0"))
    assert mapa["2411"] == (Decimal("0"), Decimal("100"))
    db.close()
    t = client.get("/inventario/reservas", cookies=auth_cookies).text
    assert "DESPACHADA" in t


def test_despacho_auxiliar_6131_2521(client, auth_cookies):
    from decimal import Decimal
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex
    from app.services.inventory import reservar_insumo
    db = SessionLocal()
    pid, oid, folio = _setup(db, "A01", categoria="AVIO", stock=100.0,
                             cpp=5.0)
    db = SessionLocal()
    reservar_insumo(db, pid, 10.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    r = client.post("/inventario/reservas/despachar",
                    data={"producto_id": str(pid),
                          "orden_venta_id": str(oid), "cantidad": "10"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers.get("location", "")
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == f"Despacho reserva {folio}").one()
    mapa = _mapa(db, k.asiento_id)
    assert mapa["2311"] == (Decimal("50"), Decimal("0"))
    assert mapa["2521"] == (Decimal("0"), Decimal("50"))
    db.close()


def test_salida_manual_con_pedido_libera_reserva(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.services.inventory import reservar_insumo
    db = SessionLocal()
    pid, oid, folio = _setup(db, "M01", stock=20.0, cpp=10.0)
    db = SessionLocal()
    reservar_insumo(db, pid, 4.0, orden_venta_id=oid, usuario_id=None)
    db.close()
    # Salida manual vinculada a la reserva: libera 1.0 de reservado (4→3),
    # físico 20→19, con Kardex valorizado y asiento (no duplica).
    r = client.post("/inventario/almacen/kardex",
                    data={"producto_id": str(pid),
                          "tipo_movimiento": "SALIDA_CONSUMO_TALLER", "cantidad": "1",
                          "orden_venta_id": str(oid),
                          "observacion": "corte manual vinculado",
                          "alcance": "mp"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(ProductoInsumo, pid)
    assert p.stock_fisico == 19.0 and p.stock_reservado == 3.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == "corte manual vinculado").one()
    assert k.costo_total == 10.0 and k.asiento_id is not None
    db.close()
    # El despacho posterior solo puede usar el pendiente restante (3.0).
    r = client.post("/inventario/reservas/despachar",
                    data={"producto_id": str(pid),
                          "orden_venta_id": str(oid), "cantidad": "4"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers.get("location", "")
    r = client.post("/inventario/reservas/despachar",
                    data={"producto_id": str(pid),
                          "orden_venta_id": str(oid), "cantidad": "3"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(ProductoInsumo, pid)
    assert p.stock_fisico == 16.0 and (p.stock_reservado or 0) == 0.0
    db.close()


def test_salida_costo_cero_no_bloquea_y_sin_asiento(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    db = SessionLocal()
    pid, oid, folio = _setup(db, "C01", stock=10.0, cpp=0.0)
    db.close()
    r = client.post("/inventario/almacen/kardex",
                    data={"producto_id": str(pid),
                          "tipo_movimiento": "SALIDA_CONSUMO_TALLER", "cantidad": "2",
                          "observacion": "salida sin costo", "alcance": "mp"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    p = db.get(ProductoInsumo, pid)
    assert p.stock_fisico == 8.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid,
        MovimientoKardex.observacion == "salida sin costo").one()
    assert k.asiento_id is None  # sin efecto monetario: no hay asiento de 0
    db.close()
