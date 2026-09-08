"""CxP comerciales: origen, espejo RECEIVED→BILLED sin duplicar y dummy fuera."""
from datetime import date
from decimal import Decimal


def _db():
    from app.core.database import Base, SessionLocal, engine
    from app.services import finanzas as f
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    f.seed_pcge_basico(db)
    f.ensure_cxp_tipo_comprobante_column(db)
    f.ensure_cxp_origen_tipo_column(db)
    return db


def _limpia(db):
    from app.models.finanzas import (AsientoContable, CuentaPorPagar,
                                     LineaAsientoContable)
    from app.models.inventario import (DetalleOrdenCompra, MovimientoKardex,
                                       OrdenCompra)
    from app.models.purchasing import PurchaseOrder, Supplier
    for m in (LineaAsientoContable, AsientoContable, MovimientoKardex,
              DetalleOrdenCompra, CuentaPorPagar, OrdenCompra,
              PurchaseOrder):
        db.query(m).delete()
    db.query(Supplier).filter(Supplier.nombre.like("TCO-%")).delete(
        synchronize_session=False)
    db.query(Supplier).filter(
        Supplier.nombre == "Personal Destajo").delete(
        synchronize_session=False)
    db.commit()


def _oc_recibida(db, tag, total_linea=870.0):
    from app.models.inventario import DetalleOrdenCompra, OrdenCompra
    from app.models.inventario import ProductoInsumo
    from app.models.purchasing import Supplier
    from app.services import compras_kardex as ck
    sup = Supplier(nombre=f"TCO-{tag}")
    db.add(sup)
    db.flush()
    prod = ProductoInsumo(sku=f"TCO-{tag}", nombre=f"Insumo {tag}",
                          categoria="TELA", unidad_medida="METROS",
                          stock_fisico=0.0)
    db.add(prod)
    db.flush()
    oc = OrdenCompra(proveedor_id=sup.id, folio=f"OC-TCO-{tag}",
                     estado="APPROVED", monto_total=round(total_linea, 2))
    db.add(oc)
    db.flush()
    d = DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod.id,
                           cantidad_solicitada=10, precio_unitario=total_linea / 10)
    db.add(d)
    db.commit()
    ck.recepcionar_oc(db, oc.id, {d.id: 10.0})
    return oc


def test_dummy_filtrado_y_purgado():
    from app.models.purchasing import Supplier
    from app.services import purchasing as po_svc
    db = _db()
    _limpia(db)
    db.add(Supplier(nombre="Personal Destajo", ruc="00000000000"))
    db.add(Supplier(nombre="TCO-Real"))
    db.commit()
    vis = [s.nombre for s in po_svc.proveedores_visibles(db)]
    assert "TCO-Real" in vis and "Personal Destajo" not in vis
    rep = po_svc.purgar_proveedor_dummy(db)
    assert rep["eliminados"] == 1 and rep["conservados"] == []
    assert db.query(Supplier).filter(
        Supplier.nombre == "Personal Destajo").count() == 0
    _limpia(db)
    db.close()


def test_dummy_referenciado_se_conserva():
    from app.models.finanzas import CuentaPorPagar
    from app.models.purchasing import Supplier
    from app.services import purchasing as po_svc
    db = _db()
    _limpia(db)
    sup = Supplier(nombre="Personal Destajo", ruc="00000000000")
    db.add(sup)
    db.flush()
    db.add(CuentaPorPagar(proveedor_id=sup.id, numero_factura="X-1",
                          monto_total=10.0, saldo_pendiente=10.0,
                          estado="POR_PAGAR"))
    db.commit()
    rep = po_svc.purgar_proveedor_dummy(db)
    assert rep["eliminados"] == 0
    assert rep["conservados"][0]["usos"] == ["cuentas_por_pagar"]
    _limpia(db)
    db.close()


def test_facturar_actualiza_espejo_sin_duplicar():
    from app.models.finanzas import CuentaPorPagar
    from app.services import compras_kardex as ck
    db = _db()
    _limpia(db)
    oc = _oc_recibida(db, "UPD")
    # el hook de recepción ya dejó el espejo con el folio
    base = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).all()
    assert len(base) == 1 and base[0].numero_factura == oc.folio
    rep = ck.reparar_cxp_compras(db)
    assert rep["creadas"] == 0  # nada nuevo: el espejo ya existe
    r = ck.facturar_oc(db, oc.id, "F001-TCO-1", date.today())
    assert r["estado"] == "BILLED"
    assert r["base"] == 737.29 and r["total"] == 870.0
    filas = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).all()
    assert len(filas) == 1  # actualiza, no duplica
    cxp = filas[0]
    assert cxp.numero_factura == "F001-TCO-1"
    assert cxp.tipo_comprobante == "FACTURA" and cxp.origen_tipo == "PROVEEDORES MATERIA PRIMA"
    assert cxp.monto_total == 870.0 and cxp.saldo_pendiente == 870.0
    assert cxp.estado == "POR_PAGAR"
    _limpia(db)
    db.close()


def test_reparar_billed_crea_cxp_con_totales():
    from app.models.finanzas import CuentaPorPagar
    from app.services import compras_kardex as ck
    db = _db()
    _limpia(db)
    oc = _oc_recibida(db, "BIL", total_linea=476.0)
    oc.estado = "BILLED"
    oc.numero_factura = "F001-TCO-2"
    oc.subtotal = 403.39
    oc.igv = 72.61
    oc.monto_total = 476.0
    db.commit()
    rep = ck.reparar_cxp_compras(db)
    assert rep["actualizadas"] == 1  # el espejo del hook se actualiza
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).first()
    assert cxp is not None and cxp.monto_total == 476.0
    assert cxp.saldo_pendiente == 476.0 and cxp.estado == "POR_PAGAR"
    assert cxp.origen_tipo == "PROVEEDORES MATERIA PRIMA"
    _limpia(db)
    db.close()


def test_cxp_muestra_origen_y_pagar(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    oc = _oc_recibida(db, "VIS")
    db.close()
    t = client.get("/finanzas/cuentas-por-pagar", cookies=auth_cookies).text
    assert "PROVEEDORES MATERIA PRIMA" in t and oc.folio in t


def test_recepcion_crea_espejo_obligatorio():
    from app.models.finanzas import CuentaPorPagar
    from app.services import compras_kardex as ck
    db = _db()
    _limpia(db)
    oc = _oc_recibida(db, "REC")
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).first()
    assert cxp is not None  # espejo automático al recibir
    assert cxp.numero_factura == oc.folio and cxp.origen_tipo == "PROVEEDORES MATERIA PRIMA"
    assert cxp.tipo_comprobante == "GUIA_RECEPCION" and cxp.estado == "POR_FACTURAR"
    assert cxp.saldo_pendiente == 870.0 and cxp.actividad_flujo == "OPERATIVO"
    assert (cxp.observacion or "").startswith("Mercadería recibida")
    assert cxp.fecha_vencimiento is not None
    # segunda reparación no duplica
    rep = ck.reparar_cxp_compras(db)
    assert db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).count() == 1
    _limpia(db)
    db.close()


def test_reparar_backfill_asiento_billed_legado():
    from app.models.finanzas import AsientoContable, CuentaPorPagar
    from app.models.inventario import OrdenCompra
    from app.models.purchasing import Supplier
    from app.services import compras_kardex as ck
    db = _db()
    _limpia(db)
    sup = Supplier(nombre="TCO-LEG")
    db.add(sup)
    db.flush()
    # OC BILLED legada: totales pero sin CxP ni asiento de provisión
    oc = OrdenCompra(proveedor_id=sup.id, folio="OC-TCO-LEG",
                     estado="BILLED", numero_factura="F001-LEG-001",
                     subtotal=737.29, igv=132.71, monto_total=870.0,
                     fecha_factura=date.today())
    db.add(oc)
    db.commit()
    rep = ck.reparar_cxp_compras(db)
    assert rep["creadas"] == 1
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).first()
    assert cxp is not None and cxp.tipo_comprobante == "FACTURA"
    prov = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "COMPRA",
        AsientoContable.origen_id == oc.id).all()
    assert len(prov) == 1
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    lineas = db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id == prov[0].id).all()
    mapa = {}
    for l in lineas:
        c = db.get(CuentaContable, l.cuenta_id)
        mapa[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    assert mapa["6011"] == (Decimal("737.29"), Decimal("0"))
    assert mapa["40111"] == (Decimal("132.71"), Decimal("0"))
    assert mapa["4212"] == (Decimal("0"), Decimal("870"))
    assert rep["asientos"].get("OC-TCO-LEG") == prov[0].numero
    # segunda pasada: nada nuevo
    rep2 = ck.reparar_cxp_compras(db)
    assert rep2["creadas"] == 0
    assert db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "COMPRA",
        AsientoContable.origen_id == oc.id).count() == 1
    _limpia(db)
    db.close()


def test_por_facturar_bloquea_pago_y_billed_lo_habilita():
    from app.models.finanzas import CuentaPorPagar
    from app.services import compras_kardex as ck
    from app.services import contabilidad as C
    db = _db()
    _limpia(db)
    oc = _oc_recibida(db, "BLQ")
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).first()
    assert cxp.estado == "POR_FACTURAR"
    try:
        C.pagar_proveedor(db, cxp.id, 100.0, "1041")
        assert False, "pagar sin factura debió fallar"
    except ValueError as e:
        assert "factura" in str(e).lower()
    ck.facturar_oc(db, oc.id, "F001-BLQ-1", date.today())
    db.close()
    db = _db()
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.orden_compra_id == oc.id).first()
    assert cxp.estado == "POR_PAGAR" and cxp.tipo_comprobante == "FACTURA"
    p = C.pagar_proveedor(db, cxp.id, cxp.saldo_pendiente, "1041")
    assert p["estado"] == "PAGADO"
    _limpia(db)
    db.close()


def test_gasto_activo_fijo_origen_e_inversion():
    from datetime import date as _d
    from app.models.finanzas import CuentaPorPagar
    from app.models.purchasing import Supplier
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db()
    _limpia(db)
    f.seed_pcge_basico(db)
    sup = Supplier(nombre="TCO-ACT")
    db.add(sup)
    db.flush()
    g, _a = f.registrar_gasto_operativo(
        db, fecha=_d.today(), categoria="ACTIVO_FIJO", monto_base=5000,
        tipo_comprobante="FACTURA", numero_comprobante="F-ACT-001",
        proveedor_id=sup.id)
    n0 = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-ACT-001").count()
    n = C.sincronizar_cxp_desde_gastos(db)
    assert db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-ACT-001").count() == n0 + n
    assert n >= 1
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-ACT-001").first()
    assert cxp is not None
    assert cxp.origen_tipo == "ACTIVOS Y MAQUINARIA"
    assert cxp.actividad_flujo == "INVERSION"
    assert cxp.fecha_vencimiento is not None
    _limpia(db)
    db.close()


def test_origenes_legacy_se_normalizan():
    from datetime import date as _d
    from app.models.finanzas import CuentaPorPagar
    from app.models.purchasing import Supplier
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db()
    _limpia(db)
    f.seed_pcge_basico(db)
    sup = Supplier(nombre="TCO-LEG2")
    db.add(sup)
    db.flush()
    g, _a = f.registrar_gasto_operativo(
        db, fecha=_d.today(), categoria="OTRO", monto_base=200,
        tipo_comprobante="FACTURA", numero_comprobante="F-LEG-002",
        proveedor_id=sup.id)
    # espejo legacy con origen antiguo
    db.add(CuentaPorPagar(proveedor_id=sup.id, numero_factura="F-LEG-002",
                          origen_tipo="GASTOS", monto_total=236.0,
                          saldo_pendiente=236.0, estado="POR_PAGAR"))
    db.commit()
    C.sincronizar_cxp_desde_gastos(db)
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-LEG-002").first()
    assert cxp.origen_tipo == "GASTOS OPERATIVOS"
    _limpia(db)
    db.close()


def test_ensure_runtime_schema_idempotente():
    from app.services import finanzas as f
    db = _db()
    r1 = f.ensure_runtime_schema(db)
    r2 = f.ensure_runtime_schema(db)
    assert r1["columnas"] is True and r2["columnas"] is True
    assert r1["empleados"] is True
    db.close()


def test_cxp_filtro_origen_normalizado(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    oc = _oc_recibida(db, "FLT")
    db.close()
    r = client.get("/finanzas/cuentas-por-pagar?origen=PROVEEDORES_MATERIA_PRIMA",
                   cookies=auth_cookies)
    assert r.status_code == 200 and oc.folio in r.text
