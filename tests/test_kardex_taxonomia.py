"""Taxonomía canónica de Kardex + automatizaciones.

- Selector poblado server-side con las 7 opciones + PT.
- ENTRADA_AJUSTE manual suma stock y contabiliza (mp).
- ENTRADA_COMPRA automática al recepcionar OC.
- SALIDA_CONSUMO_TALLER automática al pasar a EN_CORTE.
- ENTRADA_PRODUCTO_TERMINADO automática al aprobar calidad (Kanban→Listo).
- SALIDA_VENTA contabiliza costo (6921x/2111x).
"""
from decimal import Decimal


def _auth():
    import os
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={
        "username": "admin@suitelans.mx",
        "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _mapa(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _insumo(db, sku, categoria="TELA_PRINCIPAL", stock=30.0, cpp=10.0):
    from app.models.inventario import ProductoInsumo
    p = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku).first()
    if not p:
        p = ProductoInsumo(sku=sku, nombre=sku, categoria=categoria,
                           unidad_medida="METROS", costo_unitario=cpp,
                           costo_promedio=cpp, stock_fisico=stock)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


def test_selector_poblado_con_7_opciones():
    c, ck = _auth()
    t = c.get("/inventario/almacen", cookies=ck).text
    for code in ("ENTRADA_AJUSTE", "ENTRADA_DEVOLUCION",
                 "SALIDA_CONSUMO_TALLER", "SALIDA_MERMA", "SALIDA_AJUSTE"):
        assert f'value="{code}"' in t, code
    assert "Salida de materia prima a producción/corte" in t
    t = c.get("/inventario/almacen?sub=pt", cookies=ck).text
    for code in ("ENTRADA_PRODUCTO_TERMINADO", "ENTRADA_AJUSTE",
                 "SALIDA_VENTA_RTW", "SALIDA_AJUSTE"):
        assert f'value="{code}"' in t, code


def test_entrada_ajuste_manual_suma_y_contabiliza():
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    c, ck = _auth()
    db = SessionLocal()
    p = _insumo(db, "KX-TAX-AJ", stock=30.0, cpp=10.0)
    pid = p.id
    db.close()
    r = c.post("/inventario/almacen/kardex",
               data={"alcance": "mp", "producto_id": str(pid),
                     "tipo_movimiento": "ENTRADA_AJUSTE", "cantidad": "10",
                     "observacion": "saldo inicial"},
               cookies=ck, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    assert db.get(ProductoInsumo, pid).stock_fisico == 40.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid).order_by(
        MovimientoKardex.id.desc()).first()
    assert k.tipo_movimiento == "ENTRADA_AJUSTE" and k.cantidad == 10.0
    assert k.asiento_id is not None
    mapa = _mapa(db, k.asiento_id)
    assert mapa["2411"] == (Decimal("100"), Decimal("0"))
    assert mapa["6111"] == (Decimal("0"), Decimal("100"))
    db.query(MovimientoKardex).filter(
        MovimientoKardex.producto_id == pid).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.id == pid).delete()
    db.commit()
    db.close()


def test_recepcion_registra_entrada_compra():
    from app.core.database import SessionLocal
    from app.models.inventario import (
        DetalleOrdenCompra,
        MovimientoKardex,
        OrdenCompra,
        ProductoInsumo,
    )
    from app.models.purchasing import Supplier
    from app.services import compras_kardex as ck
    db = SessionLocal()
    sup = Supplier(nombre="TAX-SUP")
    db.add(sup)
    db.commit()
    p = _insumo(db, "KX-TAX-OC", stock=0.0, cpp=0.0)
    oc = OrdenCompra(proveedor_id=sup.id, folio="OC-TAX-001", estado="APPROVED",
                     monto_total=0)
    db.add(oc)
    db.commit()
    d = DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=p.id,
                           cantidad_solicitada=5, precio_unitario=118.0)
    db.add(d)
    db.commit()
    r = ck.recepcionar_oc(db, oc.id, {d.id: 5.0})
    assert r["estado"] == "RECEIVED"
    ks = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_compra_id == oc.id).all()
    assert len(ks) == 1 and ks[0].tipo_movimiento == "ENTRADA_COMPRA"
    db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_compra_id == oc.id).delete()
    db.query(DetalleOrdenCompra).filter(
        DetalleOrdenCompra.orden_compra_id == oc.id).delete()
    db.query(OrdenCompra).filter(OrdenCompra.id == oc.id).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.id == p.id).delete()
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    db.commit()
    db.close()


def test_corte_registra_salida_consumo_taller():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.inventory import Fabric
    from app.models.inventario import MovimientoKardex
    from app.models.order import Garment, Order
    from app.services import taller as taller_svc
    db = SessionLocal()
    c = Client(nombre="Tax", apellidos="Corte", tipo_doc="DNI")
    db.add(c)
    db.commit()
    o = Order(folio="SE-TAX-CUT", client_id=c.id, estado="confirmado",
              total=1000.0)
    db.add(o)
    db.commit()
    fab = Fabric(codigo="KX-TAX-FAB", nombre="Tela corte", stock_metros=20.0,
                 precio_metro=10.0)
    db.add(fab)
    db.commit()
    g = Garment(order_id=o.id, tipo="pantalon", tela_id=fab.id, precio=500.0)
    db.add(g)
    db.commit()
    gid, oid, fid, cid = g.id, o.id, fab.id, c.id
    db.close()
    db = SessionLocal()
    taller_svc.mover(db, gid, "EN_CORTE", None)
    db.close()
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == oid,
        MovimientoKardex.tipo_movimiento == "SALIDA_CONSUMO_TALLER").first()
    assert k is not None and k.cantidad == 1.4  # consumo pantalón
    db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == oid).delete()
    db.query(Garment).filter(Garment.id == gid).delete()
    db.query(Order).filter(Order.id == oid).delete()
    db.query(Fabric).filter(Fabric.id == fid).delete()
    db.query(Client).filter(Client.id == cid).delete()
    from app.models.inventario import ProductoInsumo
    db.query(ProductoInsumo).filter(
        ProductoInsumo.sku == "KX-TAX-FAB").delete()
    db.commit()
    db.close()


def test_calidad_ok_registra_entrada_pt():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.inventario import MovimientoKardex
    from app.models.order import Garment, Order
    from app.services import taller as taller_svc
    db = SessionLocal()
    c = Client(nombre="Tax", apellidos="Calidad", tipo_doc="DNI")
    db.add(c)
    db.commit()
    o = Order(folio="SE-TAX-QC", client_id=c.id, estado="confirmado",
              total=800.0)
    db.add(o)
    db.commit()
    g = Garment(order_id=o.id, tipo="chaleco", precio=800.0,
                estado_taller="ACABADOS")
    db.add(g)
    db.commit()
    gid, oid, cid = g.id, o.id, c.id
    db.close()
    from app.core.constants import QC_CHECKLIST
    db = SessionLocal()
    taller_svc.aprobar_calidad(db, gid, [True] * len(QC_CHECKLIST), "", None)
    db.close()
    db = SessionLocal()
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == oid,
        MovimientoKardex.tipo_movimiento == "ENTRADA_PRODUCTO_TERMINADO").one()
    assert k.cantidad == 1 and k.producto_id is None
    assert k.asiento_id is None and f"#{gid}" in (k.observacion or "")
    db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_venta_id == oid).delete()
    from app.models.order import ControlCalidad
    db.query(ControlCalidad).filter(ControlCalidad.garment_id == gid).delete()
    db.query(Garment).filter(Garment.id == gid).delete()
    db.query(Order).filter(Order.id == oid).delete()
    db.query(Client).filter(Client.id == cid).delete()
    db.commit()
    db.close()


def test_salida_venta_contabiliza_costo():
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    from app.models.inventory import StockMovement
    c, ck = _auth()
    from app.models.client import Client
    from app.models.order import Order
    db = SessionLocal()
    cli = Client(nombre="Tax", apellidos="Venta", tipo_doc="DNI")
    db.add(cli)
    db.commit()
    ov = Order(folio="SE-TAX-SV", client_id=cli.id, estado="confirmado",
               total=400.0)
    db.add(ov)
    db.commit()
    oid, cid = ov.id, cli.id
    p = Product(codigo="KX-TAX-PT", nombre="PT venta", linea="comercial")
    db.add(p)
    db.commit()
    v = ProductVariant(product_id=p.id, talla="M", sku="KX-TAX-PT-M",
                       stock=10.0, precio=200.0, costo_unitario=80.0)
    db.add(v)
    db.commit()
    vid, pid = v.id, p.id
    db.close()
    r = c.post("/inventario/almacen/kardex",
               data={"alcance": "pt", "variant_id": str(vid),
                     "tipo_movimiento": "SALIDA_VENTA", "cantidad": "2",
                     "orden_venta_id": str(oid),
                     "observacion": "venta mostrador"},
               cookies=ck, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    assert db.get(ProductVariant, vid).stock == 8.0
    from app.models.finanzas import AsientoContable
    a = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "COSTO_VENTAS").order_by(
        AsientoContable.id.desc()).first()
    assert a is not None
    mapa = _mapa(db, a.id)
    assert mapa["6921"] == (Decimal("160"), Decimal("0"))
    assert mapa["2111"] == (Decimal("0"), Decimal("160"))
    db.query(StockMovement).filter(StockMovement.item_id == vid).delete()
    db.query(ProductVariant).filter(ProductVariant.id == vid).delete()
    db.query(Product).filter(Product.id == pid).delete()
    db.query(Order).filter(Order.id == oid).delete()
    db.query(Client).filter(Client.id == cid).delete()
    from app.models.finanzas import LineaAsientoContable
    db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == a.id).delete()
    db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    db.commit()
    db.close()


def test_entrada_pt_unitaria_historial_y_diario():
    """Item 3: 1 unidad a SKU -> stock+1, fila en historial y 2311/7111."""
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    from app.models.finanzas import AsientoContable
    from app.models.inventario import MovimientoKardex
    c, ck = _auth()
    db = SessionLocal()
    p = Product(codigo="KX-TAX-U1", nombre="Saco smoke", linea="comercial")
    db.add(p)
    db.commit()
    v = ProductVariant(product_id=p.id, talla="M", sku="SKU-SMOKE",
                       stock=0.0, precio=120.0, costo_unitario=0.0)
    db.add(v)
    db.commit()
    vid, pid = v.id, p.id
    db.close()
    r = c.post("/inventario/almacen/kardex",
               data={"alcance": "pt", "variant_id": str(vid),
                     "tipo_movimiento": "ENTRADA_PRODUCTO_TERMINADO",
                     "cantidad": "1", "costo_unitario": "70",
                     "observacion": "ingreso inicial"},
               cookies=ck, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    assert db.get(ProductVariant, vid).stock == 1.0
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.doc_ref == "SKU-SMOKE",
        MovimientoKardex.tipo_movimiento == "ENTRADA_PRODUCTO_TERMINADO").one()
    assert k.cantidad == 1.0 and k.costo_total == 70.0
    assert k.asiento_id is not None
    a = db.get(AsientoContable, k.asiento_id)
    assert a.origen_tipo == "PRODUCCION"
    mapa = _mapa(db, a.id)
    assert mapa["2311"] == (Decimal("70"), Decimal("0"))
    assert mapa["7111"] == (Decimal("0"), Decimal("70"))
    # visible en el historial renderizado
    t = c.get("/inventario/almacen?sub=pt", cookies=ck).text
    assert "SKU-SMOKE" in t and "ENTRADA_PRODUCTO_TERMINADO" in t
    from app.models.inventory import StockMovement
    db.query(StockMovement).filter(StockMovement.item_id == vid).delete()
    db.query(MovimientoKardex).filter(MovimientoKardex.id == k.id).delete()
    db.query(ProductVariant).filter(ProductVariant.id == vid).delete()
    db.query(Product).filter(Product.id == pid).delete()
    from app.models.finanzas import LineaAsientoContable
    db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == a.id).delete()
    db.query(AsientoContable).filter(AsientoContable.id == a.id).delete()
    db.commit()
    db.close()
