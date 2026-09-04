"""Compras + Kardex valorizado + Libro Diario (PCGE).

Cubre el spec:
- OC DRAFT no afecta stock/contabilidad; APPROVED habilita recepción.
- Recepción -> Kardex ENTRADA valorizado + CPP + asiento DEBE 2411 / HABER 6111.
- Facturación -> provisión DEBE 6011 + DEBE 40111 / HABER 4212 + CxP, estado BILLED.
- Consumo taller -> DEBE 6111 / HABER 2411. Merma -> DEBE 6591 / HABER 2411.
- Atomicidad: fallo en recepción no deja kardex/asiento parcial.
- Landed prorrateado por valor antes del CPP final.
"""
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    return db


def _nuevo_proveedor(db, nombre):
    from app.models.purchasing import Supplier
    sup = Supplier(nombre=nombre)
    db.add(sup); db.commit(); db.refresh(sup)
    return sup


def _nuevo_producto(db, sku, stock=0.0, cpp=0.0):
    from app.models.inventario import ProductoInsumo
    p = ProductoInsumo(sku=sku, nombre=f"Insumo {sku}", categoria="TELA",
                       unidad_medida="METROS", costo_unitario=cpp, costo_promedio=cpp,
                       stock_fisico=stock)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _nueva_oc(db, sup_id, folio, lineas, landed_flete=0.0, estado="APPROVED"):
    """lineas: [(producto_id, qty, precio)]."""
    from app.models.inventario import DetalleOrdenCompra, OrdenCompra
    oc = OrdenCompra(proveedor_id=sup_id, folio=folio, estado=estado,
                     landed_flete=landed_flete)
    db.add(oc); db.flush()
    dets = []
    for pid, qty, precio in lineas:
        d = DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=pid,
                               cantidad_solicitada=qty, precio_unitario=precio)
        db.add(d); db.flush()
        dets.append(d)
    db.commit(); db.refresh(oc)
    return oc, dets


def _lineas_de_asiento(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    rows = db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == asiento_id).all()
    out = {}
    for l in rows:
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def test_prorrateo_y_cpp_puros():
    from app.services import compras_kardex as ck
    from app.models.inventario import DetalleOrdenCompra
    d1 = DetalleOrdenCompra(id=1, cantidad_solicitada=10, precio_unitario=100)
    d2 = DetalleOrdenCompra(id=2, cantidad_solicitada=10, precio_unitario=300)
    pr = ck.prorratear_landed([d1, d2], 400)
    # base 1000 vs 3000 -> 100 y 300 totales -> 10/u y 30/u
    assert pr[1] == Decimal("10")
    assert pr[2] == Decimal("30")
    assert ck.cpp_nuevo(100, 50, 100, 70) == Decimal("60")
    assert ck.cpp_nuevo(0, 0, 10, 25) == Decimal("25")


def test_draft_no_permite_recepcion():
    from app.services import compras_kardex as ck
    db = _db()
    sup = _nuevo_proveedor(db, "SUP-DRAFT")
    prod = _nuevo_producto(db, "SKU-DRAFT-001")
    oc, dets = _nueva_oc(db, sup.id, "OC-DRAFT-001", [(prod.id, 10, 50)], estado="DRAFT")
    try:
        ck.recepcionar_oc(db, oc.id, {dets[0].id: 5})
        assert False, "DRAFT debió rechazar recepción"
    except ValueError as e:
        assert "DRAFT" in str(e)
    db.refresh(prod)
    assert prod.stock_fisico == 0.0
    db.close()


def test_recepcion_parcial_total_con_cpp_y_asiento_241_611():
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.services import compras_kardex as ck
    db = _db()
    sup = _nuevo_proveedor(db, "SUP-REC")
    prod = _nuevo_producto(db, "SKU-REC-001", stock=100.0, cpp=50.0)
    oc, dets = _nueva_oc(db, sup.id, "OC-REC-001", [(prod.id, 100, 70.0)],
                         landed_flete=1000.0, estado="APPROVED")
    # landed 1000 sobre base 7000 -> +10/u -> costo final 80/u
    r1 = ck.recepcionar_oc(db, oc.id, {dets[0].id: 40.0}, usuario_id=None)
    assert r1["estado"] == "PARTIALLY_RECEIVED"
    assert r1["valorizado"] == 40.0 * 80.0
    db.refresh(prod)
    # CPP = (100*50 + 40*80) / 140 = 58.5714...
    assert abs(prod.costo_promedio - (8200 / 140)) < 1e-6
    assert prod.stock_fisico == 140.0
    mapa = _lineas_de_asiento(db, r1["asiento_id"])
    assert mapa["2411"] == (Decimal("3200"), Decimal("0"))
    assert mapa["6111"] == (Decimal("0"), Decimal("3200"))
    k = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_compra_id == oc.id).all()
    assert len(k) == 1 and k[0].tipo_movimiento == "ENTRADA"
    assert k[0].asiento_id == r1["asiento_id"]
    assert k[0].costo_unitario == 80.0 and k[0].saldo_fisico == 140.0

    r2 = ck.recepcionar_oc(db, oc.id, {dets[0].id: 60.0})
    assert r2["estado"] == "RECEIVED"
    db.refresh(prod)
    assert prod.stock_fisico == 200.0
    # CPP = (140*58.5714 + 60*80)/200 = 65.0
    assert abs(prod.costo_promedio - 65.0) < 1e-6
    db.close()


def test_facturar_provision_601_4011_421_y_billed():
    from app.models.finanzas import CuentaPorPagar
    from app.services import compras_kardex as ck
    db = _db()
    sup = _nuevo_proveedor(db, "SUP-FAC")
    prod = _nuevo_producto(db, "SKU-FAC-001")
    oc, dets = _nueva_oc(db, sup.id, "OC-FAC-001", [(prod.id, 10, 100.0)], estado="APPROVED")
    ck.recepcionar_oc(db, oc.id, {dets[0].id: 10.0})
    r = ck.facturar_oc(db, oc.id, "F001-000123", date.today())
    assert r["estado"] == "BILLED"
    assert r["base"] == 1000.0 and r["igv"] == 180.0 and r["total"] == 1180.0
    mapa = _lineas_de_asiento(db, r["asiento_id"])
    assert mapa["6011"] == (Decimal("1000"), Decimal("0"))
    assert mapa["40111"] == (Decimal("180"), Decimal("0"))
    assert mapa["4212"] == (Decimal("0"), Decimal("1180"))
    cxp = db.get(CuentaPorPagar, r["cxp_id"])
    assert cxp.numero_factura == "F001-000123" and cxp.saldo_pendiente == 1180.0
    # idempotencia: mismo comprobante no se duplica
    try:
        ck.facturar_oc(db, oc.id, "F001-000123")
        assert False, "factura duplicada debió fallar"
    except ValueError as e:
        assert "ya provisionada" in str(e) or "ya facturada" in str(e)
    # BILLED bloquea recepción posterior
    try:
        ck.recepcionar_oc(db, oc.id, {dets[0].id: 1})
        assert False, "BILLED debió bloquear recepción"
    except ValueError:
        pass
    db.close()


def test_consumo_y_merma_con_asientos():
    from app.services import compras_kardex as ck
    db = _db()
    sup = _nuevo_proveedor(db, "SUP-CON")
    prod = _nuevo_producto(db, "SKU-CON-001", stock=100.0, cpp=60.0)
    c = ck.consumir_taller(db, prod.id, 10.0, doc_ref="OT-001")
    assert c["valorizado"] == 600.0
    mapa = _lineas_de_asiento(db, c["asiento_id"])
    assert mapa["6111"] == (Decimal("600"), Decimal("0"))
    assert mapa["2411"] == (Decimal("0"), Decimal("600"))
    m = ck.registrar_merma(db, prod.id, 5.0, observacion="corte")
    assert m["valorizado"] == 300.0
    mapa2 = _lineas_de_asiento(db, m["asiento_id"])
    assert mapa2["6591"] == (Decimal("300"), Decimal("0"))
    assert mapa2["2411"] == (Decimal("0"), Decimal("300"))
    db.refresh(prod)
    assert prod.stock_fisico == 85.0
    db.close()


def test_rollback_atomico_en_recepcion_fallida():
    from app.models.finanzas import AsientoContable
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.services import compras_kardex as ck
    db = _db()
    sup = _nuevo_proveedor(db, "SUP-RBK")
    prod = _nuevo_producto(db, "SKU-RBK-001", stock=50.0, cpp=40.0)
    oc, dets = _nueva_oc(db, sup.id, "OC-RBK-001", [(prod.id, 10, 100.0)], estado="APPROVED")
    n_k0 = db.query(MovimientoKardex).count()
    n_a0 = db.query(AsientoContable).count()
    try:
        ck.recepcionar_oc(db, oc.id, {dets[0].id: 999.0})  # excede pendiente
        assert False, "debió fallar por pendiente"
    except ValueError:
        pass
    db.refresh(prod)
    assert prod.stock_fisico == 50.0 and prod.costo_promedio == 40.0
    assert db.query(MovimientoKardex).count() == n_k0
    assert db.query(AsientoContable).count() == n_a0
    # la OC sigue viva y operable tras el rollback
    r = ck.recepcionar_oc(db, oc.id, {dets[0].id: 10.0})
    assert r["estado"] == "RECEIVED"
    db.close()
