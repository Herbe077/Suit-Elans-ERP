"""Motor transaccional ContabilidadService (PCGE).

  1. Partida doble: en CADA evento, Suma Debe == Suma Haber del asiento.
  2. CxC/CxP amortizables: múltiples abonos, saldo = total - pagado, estados.
  3. Bloqueo duro: período CERRADO/BLOQUEADO deniega ventas, cobros, compras,
     kardex, gastos y pagos.
  4. Mapa de eventos: 1212/40111/70 (7032 bespoke, 7011 RTW) (venta),
     1011/1041 según medio de pago (cobro), 602+40111/4212 (compra),
     4212/1041 (pago CxP), 6911/2111 (costo de ventas RTW).
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


def _limpia(db):
    from app.models.billing import CashMovement, Invoice
    from app.models.finanzas import (AsientoContable, CuentaPorCobrar, CuentaPorPagar,
                                     GastoRegistrado, LineaAsientoContable, MovimientoFinanciero)
    from app.models.order import Garment, Order, Payment
    for m in (LineaAsientoContable, AsientoContable, MovimientoFinanciero, CashMovement,
              GastoRegistrado, CuentaPorCobrar, CuentaPorPagar, Payment, Invoice,
              Garment, Order):
        db.query(m).delete()
    db.commit()


def _order(db, folio, total):
    from app.models.order import Order
    o = Order(folio=folio, total=total, anticipo=0, estado="cotizado")
    db.add(o); db.commit(); db.refresh(o)
    return o


def _lineas(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _balanceado(mapa):
    d = sum(v[0] for v in mapa.values()); h = sum(v[1] for v in mapa.values())
    assert d == h and d > 0
    return d


def test_venta_factura_1212_40111_7011_y_cxc():
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    o = _order(db, "CT-VTA-001", 1180.0)
    r = C.emitir_factura_venta(db, o.id, "F001", 18.0, None)
    from app.models.billing import Invoice
    inv = db.get(Invoice, r["invoice_id"])
    base, igv, total = Decimal(str(inv.subtotal)), Decimal(str(inv.igv)), Decimal(str(inv.total))
    mapa = _lineas(db, r["asiento_id"])
    assert _balanceado(mapa) == total
    assert mapa["1212"] == (total, Decimal("0"))
    assert mapa["40111"] == (Decimal("0"), igv)
    assert mapa["7011"] == (Decimal("0"), base)
    from app.models.finanzas import CuentaPorCobrar
    cxc = db.get(CuentaPorCobrar, r["cxc_id"])
    assert cxc.estado == "PENDIENTE" and Decimal(str(cxc.saldo_pendiente)) == Decimal(str(o.total))
    _limpia(db); db.close()


def test_venta_segrega_7032_bespoke_y_7011_rtw():
    """Bespoke (pedido con prendas) → 7032; stock sin prendas → 7011."""
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    from app.models.order import Garment
    ob = _order(db, "CT-BES-001", 2000.0)
    db.add(Garment(order_id=ob.id, tipo="saco", precio=2000.0))
    db.commit()
    rb = C.emitir_factura_venta(db, ob.id, "F001", 18.0, None)
    mapb = _lineas(db, rb["asiento_id"])
    assert _balanceado(mapb) == Decimal(str(2000.0))
    assert "7032" in mapb and "7011" not in mapb
    base_b = round(2000.0 / 1.18, 2)
    assert mapb["7032"] == (Decimal("0"), Decimal(str(base_b)))
    ortw = _order(db, "CT-RTW-001", 500.0)
    rr = C.emitir_factura_venta(db, ortw.id, "B001", 18.0, None)
    mapr = _lineas(db, rr["asiento_id"])
    assert "7011" in mapr and "7032" not in mapr
    _limpia(db); db.close()


def test_cobro_segun_medio_de_pago_1011_1041():
    """Efectivo → 1011; transferencia/tarjeta/yape → 1041."""
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    o = _order(db, "CT-MP-001", 1000.0)
    r1 = C.cobrar_venta(db, o.id, 400.0, "transferencia", "1011", usuario_id=None)
    assert _lineas(db, r1["asiento_id"])["1041"] == (Decimal("400"), Decimal("0"))
    r2 = C.cobrar_venta(db, o.id, 300.0, "efectivo", "1041", usuario_id=None)
    assert _lineas(db, r2["asiento_id"])["1011"] == (Decimal("300"), Decimal("0"))
    r3 = C.cobrar_venta(db, o.id, 300.0, "yape", "1011", usuario_id=None)
    assert _lineas(db, r3["asiento_id"])["1041"] == (Decimal("300"), Decimal("0"))
    _limpia(db); db.close()


def test_costo_ventas_6911_2111():
    """Costo de ventas RTW: DEBE 6911 / HABER 2111, cuadrado."""
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    o = _order(db, "CT-COGS-001", 500.0)
    C.registrar_ingreso_pt(db, 500.0, "SKU-TEST")
    r = C.registrar_costo_ventas(db, o.id, 300.0, None)
    mapa = _lineas(db, r["asiento_id"])
    assert _balanceado(mapa) == Decimal("300")
    assert mapa["6911"] == (Decimal("300"), Decimal("0"))
    assert mapa["2111"] == (Decimal("0"), Decimal("300"))
    _limpia(db); db.close()


def test_2111_sin_saldo_bloquea_costo():
    """Sin ingreso por producción previa no hay costo (evita acreedor anómalo)."""
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    assert f.saldo_cuenta(db, "2111") == Decimal("0")
    o = _order(db, "CT-COGS-002", 500.0)
    try:
        C.registrar_costo_ventas(db, o.id, 100.0, None)
        assert False, "debió exigir saldo en 2111"
    except ValueError as e:
        assert "2111" in str(e)
    r = C.registrar_ingreso_pt(db, 500.0, "SKU-TEST")
    assert _lineas(db, r["asiento_id"])["2111"] == (Decimal("500"), Decimal("0"))
    C.registrar_costo_ventas(db, o.id, 100.0, None)  # ahora sí
    assert f.saldo_cuenta(db, "2111") == Decimal("400")
    _limpia(db); db.close()


def test_patrimonio_incluye_utilidad():
    """Patrimonio Total = Capital (50) + Resultados (59) + Utilidad."""
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c5011 = f.get_cuenta_by_codigo(db, "5011")
    f.crear_asiento(db, date.today(), "Capital", "APERTURA", None, [
        {"cuenta_id": c101.id, "debe": Decimal("40000"), "haber": Decimal("0")},
        {"cuenta_id": c5011.id, "debe": Decimal("0"), "haber": Decimal("40000")},
    ])
    o = _order(db, "CT-PAT-001", 118.0)
    C.emitir_factura_venta(db, o.id, "B001", 18.0, None)
    bg = f.obtener_balance_general(db)
    assert bg["capital"] == Decimal("40000")
    assert bg["resultado"] == Decimal("100")  # base 100 de la venta
    assert bg["patrimonio_total"] == Decimal("40100")
    assert bg["activo"] == Decimal("40118") and bg["pasivo"] == Decimal("18")
    assert bg["valida"] is True
    _limpia(db); db.close()


def test_mayor_agrupado_con_saldo_acumulado():
    """Mayor por cuenta con fechas YYYY-MM-DD y saldo acumulado."""
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    o = _order(db, "CT-MAY-001", 1000.0)
    C.cobrar_venta(db, o.id, 400.0, "efectivo", "1011", usuario_id=None)
    C.cobrar_venta(db, o.id, 600.0, "transferencia", "1041", usuario_id=None)
    grupos = f.obtener_mayor_agrupado(db)
    g1011 = next(g for g in grupos if g["codigo"] == "1011")
    assert g1011["total_debe"] == Decimal("400")
    assert g1011["lineas"][0]["saldo_acum"] == Decimal("400")
    assert len(g1011["lineas"][0]["fecha"]) == 10  # YYYY-MM-DD
    g1212 = next(g for g in grupos if g["codigo"] == "1212")
    saldos = [l["saldo_acum"] for l in g1212["lineas"]]
    assert saldos == [Decimal("-400"), Decimal("-1000")]  # acumulado por fecha
    assert saldos[-1] == g1212["saldo"] == Decimal("-1000")
    _limpia(db); db.close()


def test_cobro_1011_1212_mas_flujo_y_cxc_parcial_pagado():
    from app.models.finanzas import CuentaPorCobrar, MovimientoFinanciero
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    o = _order(db, "CT-COB-001", 1000.0)
    r1 = C.cobrar_venta(db, o.id, 400.0, "efectivo", "1011", usuario_id=None)
    mapa = _lineas(db, r1["asiento_id"])
    assert _balanceado(mapa) == Decimal("400")
    assert mapa["1011"] == (Decimal("400"), Decimal("0"))
    assert mapa["1212"] == (Decimal("0"), Decimal("400"))
    cxc = db.get(CuentaPorCobrar, r1["cxc_id"])
    assert cxc.saldo_pendiente == 600.0 and cxc.estado == "COBRADO_PARCIAL"
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "INGRESO").count() == 1
    # segundo abono salda
    r2 = C.cobrar_venta(db, o.id, 600.0, "yape", "1041", usuario_id=None)
    db.refresh(cxc)
    assert cxc.saldo_pendiente == 0.0 and cxc.estado == "COBRADO"
    assert cxc.monto_pagado == 1000.0
    assert r2["saldo"] == 0.0
    # sin saldo: rechaza
    try:
        C.cobrar_venta(db, o.id, 10.0, "efectivo", "1011")
        assert False
    except ValueError:
        pass
    _limpia(db); db.close()


def test_compra_602_40111_4212_y_pago_4212_1041():
    from app.models.finanzas import CuentaPorPagar, MovimientoFinanciero
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    from app.models.purchasing import Supplier
    sup = Supplier(nombre="CT-SUP"); db.add(sup); db.commit(); db.refresh(sup)
    r = C.provisionar_compra(db, sup.id, 2000.0, 360.0, numero_factura="F001-CT01")
    mapa = _lineas(db, r["asiento_id"])
    assert _balanceado(mapa) == Decimal("2360")
    assert mapa["602"] == (Decimal("2000"), Decimal("0"))
    assert mapa["40111"] == (Decimal("360"), Decimal("0"))
    assert mapa["4212"] == (Decimal("0"), Decimal("2360"))
    # abono parcial 1000 -> PARCIAL
    p1 = C.pagar_proveedor(db, r["cxp_id"], 1000.0, "1041")
    assert p1["estado"] == "PARCIAL" and p1["saldo"] == 1360.0
    mapa2 = _lineas(db, p1["asiento_id"])
    assert _balanceado(mapa2) == Decimal("1000")
    assert mapa2["4212"] == (Decimal("1000"), Decimal("0"))
    assert mapa2["1041"] == (Decimal("0"), Decimal("1000"))
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count() == 1
    # salda
    p2 = C.pagar_proveedor(db, r["cxp_id"], 1360.0, "1011")
    assert p2["estado"] == "PAGADO"
    cxp = db.get(CuentaPorPagar, r["cxp_id"])
    assert cxp.monto_pagado == 2360.0 and cxp.saldo_pendiente == 0.0
    db.query(CuentaPorPagar).delete(); db.query(Supplier).filter(Supplier.id == sup.id).delete()
    _limpia(db); db.close()


def test_gasto_alquiler_6311_y_pago_con_flujo():
    from app.services import contabilidad as C
    db = _db(); _limpia(db)
    from datetime import date as _d
    g, a = C.registrar_gasto_atomico(db, fecha=_d.today(), categoria="ALQUILER",
                                     monto_base=2000, monto_igv=360,
                                     numero_comprobante="F001-CT-G")
    assert _balanceado(_lineas(db, a.id)) == Decimal("2360")
    r = C.pagar_gasto_atomico(db, g.id, "104", usuario_id=None)
    assert r["asiento_id"]
    assert _balanceado(_lineas(db, r["asiento_id"])) == Decimal("2360")
    from app.models.finanzas import MovimientoFinanciero
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.tipo == "EGRESO").count() == 1
    db.refresh(g)
    assert g.estado == "PAGADO"
    _limpia(db); db.close()


def test_bloqueo_duro_periodo_cerrado():
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    o = _order(db, "CT-BLK-001", 500.0)
    per = f.get_or_create_periodo(db, date.today().year, date.today().month)
    f.cerrar_periodo(db, per.anio, per.mes)
    for fn in (
        lambda: C.emitir_factura_venta(db, o.id, "F001", 18.0, None),
        lambda: C.cobrar_venta(db, o.id, 100.0, "efectivo", "1011"),
        lambda: C.provisionar_compra(db, 1, 100.0, 18.0),
        lambda: C.registrar_gasto_atomico(db, date.today(), "OTRO", 100.0, 0.0),
    ):
        try:
            fn()
            assert False, "período cerrado debió denegar"
        except ValueError as e:
            assert "CERRADO" in str(e) or "denegada" in str(e)
    # kardex también bloqueado
    from app.models.inventario import DetalleOrdenCompra, OrdenCompra, ProductoInsumo
    from app.models.purchasing import Supplier
    from app.services import compras_kardex as ck
    sup = Supplier(nombre="CT-BLK-S"); db.add(sup); db.commit(); db.refresh(sup)
    prod = ProductoInsumo(sku="CT-BLK-SKU", nombre="X", categoria="TELA"); db.add(prod); db.commit(); db.refresh(prod)
    oc = OrdenCompra(proveedor_id=sup.id, folio="CT-BLK-OC", estado="APPROVED"); db.add(oc); db.flush()
    det = DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod.id, cantidad_solicitada=5, precio_unitario=10)
    db.add(det); db.commit()
    try:
        ck.recepcionar_oc(db, oc.id, {det.id: 5})
        assert False
    except ValueError as e:
        assert "CERRADO" in str(e) or "denegada" in str(e)
    db.refresh(prod)
    assert prod.stock_fisico == 0.0  # nada se movió
    # limpieza + reabre para no contaminar otros tests
    f.reabrir_periodo(db, per.anio, per.mes)
    db.query(DetalleOrdenCompra).delete(); db.query(OrdenCompra).delete()
    db.query(ProductoInsumo).filter(ProductoInsumo.sku == "CT-BLK-SKU").delete()
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    _limpia(db); db.close()


def test_flujo_por_actividad_y_eeff_dinamicos():
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    o = _order(db, "CT-FLU-001", 1000.0)
    C.cobrar_venta(db, o.id, 1000.0, "transferencia", "1041")
    fl = C.flujo_por_actividad(db)
    assert fl["por_actividad"]["OPERATIVA"]["ingresos"] == Decimal("1000")
    assert fl["bancos"]["ingresos"] == Decimal("1000")
    assert fl["total_ingresos"] - fl["total_egresos"] == Decimal("1000")
    er = f.obtener_estado_resultados(db)
    assert er["ventas"] >= Decimal("0")
    bg = f.obtener_balance_general(db)
    assert bg["valida"] is True
    _limpia(db); db.close()


def test_diario_con_lineas_totales_y_validacion():
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    o = _order(db, "CT-DIA-001", 1180.0)
    r = C.emitir_factura_venta(db, o.id, "F001", 18.0, None)
    d = f.obtener_diario(db)
    assert d["n"] >= 1 and d["cuadrado"] is True
    fila = next(x for x in d["filas"] if x["asiento"].id == r["asiento_id"])
    assert fila["cuadrado"] is True and fila["debe"] == fila["haber"] > 0
    codigos = {l["codigo"] for l in fila["lineas"]}
    assert {"1212", "40111", "7011"} <= codigos
    assert d["total_debe"] == d["total_haber"]
    # filtro por cuenta restringe a los que la tocan
    d2 = f.obtener_diario(db, cuenta_id=db.query(
        __import__("app.models.finanzas", fromlist=["CuentaContable"]).CuentaContable
    ).filter_by(codigo="7011").first().id)
    assert all(any(l["codigo"] == "7011" for l in x["lineas"]) for x in d2["filas"])
    # asiento descuadrado insertado directo => flag + badge
    mal = AsientoContable(numero="AST-MAL-00001", origen_tipo="MANUAL", glosa="mala")
    db.add(mal); db.flush()
    c1212 = f.get_cuenta_by_codigo(db, "1212")
    db.add(LineaAsientoContable(asiento_id=mal.id, cuenta_id=c1212.id, debe=100.0, haber=0.0))
    db.commit()
    d3 = f.obtener_diario(db)
    assert d3["cuadrado"] is False
    assert next(x for x in d3["filas"] if x["asiento"].id == mal.id)["cuadrado"] is False
    _limpia(db); db.close()


def test_diario_web_muestra_lineas_y_badges():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import contabilidad as C
    from app.core.database import Base, SessionLocal, engine
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    o = _order(db, "CT-DIAW-001", 500.0)
    C.cobrar_venta(db, o.id, 500.0, "efectivo", "1011")
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    ck = {"suitelans_token": r.cookies.get("suitelans_token")}
    r = c.get("/finanzas/diario", cookies=ck)
    assert r.status_code == 200
    assert "1011" in r.text and "1212" in r.text  # líneas anidadas
    assert "REGISTRADO" in r.text and "Total general" in r.text
    assert "el detalle por cuenta está en el Libro mayor" not in r.text
    _limpia(db); db.close()


def test_gasto_genera_destino_clase9_y_activo_no():
    from app.models.finanzas import AsientoContable
    from app.services import finanzas as f
    from datetime import date as _d
    db = _db(); _limpia(db)
    g, a = f.registrar_gasto_operativo(db, fecha=_d.today(), categoria="HONORARIOS",
                                       monto_base=500, monto_igv=90,
                                       numero_comprobante="F001-CT-D")
    dest = db.query(AsientoContable).filter(
        AsientoContable.origen_id == g.id,
        AsientoContable.glosa.like("Destino%")).all()
    assert len(dest) == 1
    mapa = _lineas(db, dest[0].id)
    assert mapa["941"] == (Decimal("500"), Decimal("0"))
    assert mapa["791"] == (Decimal("0"), Decimal("500"))
    # naturaleza intacta
    nat = _lineas(db, a.id)
    assert nat["6322"] == (Decimal("500"), Decimal("0"))
    # activo fijo: sin destino
    g2, _ = f.registrar_gasto_operativo(db, fecha=_d.today(), categoria="ACTIVO_FIJO",
                                        monto_base=1000, monto_igv=0,
                                        numero_comprobante="F001-CT-A")
    assert db.query(AsientoContable).filter(
        AsientoContable.origen_id == g2.id,
        AsientoContable.glosa.like("Destino%")).count() == 0
    _limpia(db); db.close()


def test_apertura_cuadrada_y_descuadrada_rechazada():
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    c = {x.codigo: x for x in
         db.query(__import__("app.models.finanzas", fromlist=["CuentaContable"]).CuentaContable).all()}
    a = f.crear_asiento(db, date.today(), "Apertura test", "APERTURA", None, [
        {"cuenta_id": c["1011"].id, "debe": Decimal("1000"), "haber": Decimal("0")},
        {"cuenta_id": c["2411"].id, "debe": Decimal("500"), "haber": Decimal("0")},
        {"cuenta_id": c["5011"].id, "debe": Decimal("0"), "haber": Decimal("1500")},
    ])
    d = f.obtener_diario(db, origen_tipo="APERTURA")
    assert d["n"] >= 1 and d["cuadrado"] is True
    assert d["total_debe"] == d["total_haber"] == Decimal("1500")
    try:
        f.crear_asiento(db, date.today(), "Mala", "APERTURA", None, [
            {"cuenta_id": c["1011"].id, "debe": Decimal("100"), "haber": Decimal("0")},
            {"cuenta_id": c["5011"].id, "debe": Decimal("0"), "haber": Decimal("90")},
        ])
        assert False
    except ValueError:
        pass
    _limpia(db); db.close()


def test_gastos_grid_y_apertura_web():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    ck = {"suitelans_token": r.cookies.get("suitelans_token")}
    t = c.get("/finanzas/gastos", cookies=ck).text
    assert "Monto Total S/ (IGV incl.)" in t and "gastoCalc" in t
    assert c.get("/finanzas/apertura", cookies=ck).status_code == 200
    r = c.post("/finanzas/apertura",
               data={"caja": "1000", "banco": "2000", "inventario": "500",
                     "capital": "3500"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    assert "AST-" in c.get("/finanzas/apertura", cookies=ck).text
    r = c.post("/finanzas/apertura",
               data={"caja": "100", "banco": "0", "inventario": "0",
                     "capital": "999"}, cookies=ck)
    assert "error=" in r.headers.get("location", "")
    # limpieza apertura creada
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    db = SessionLocal()
    ids = [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "APERTURA").all()]
    if ids:
        db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id.in_(ids)).delete(synchronize_session=False)
        db.query(AsientoContable).filter(AsientoContable.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    db.close()


def test_apertura_con_activofijo_y_cxp_y_periodo():
    from app.models.finanzas import AsientoContable
    from app.services import finanzas as f
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
    ck = {"suitelans_token": r.cookies.get("suitelans_token")}
    # capital = (1000+2000+500+4000) - 1500 = 6000
    r = c.post("/finanzas/apertura",
               data={"caja": "1000", "banco": "2000", "inventario": "500",
                     "activofijo": "4000", "cxp": "1500", "capital": "6000"}, cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    t = c.get("/finanzas/apertura", cookies=ck).text
    assert "3341" in t and "4212" in t and "REGISTRADO" in t
    # capital manipulado => rechazo
    r = c.post("/finanzas/apertura",
               data={"caja": "100", "capital": "999"}, cookies=ck)
    assert "error=" in r.headers.get("location", "")
    # alimenta Diario (origen APERTURA), Mayor y Período
    from app.core.database import SessionLocal
    db = SessionLocal()
    a = db.query(AsientoContable).filter(AsientoContable.origen_tipo == "APERTURA").order_by(
        AsientoContable.id.desc()).first()
    assert a is not None and a.numero.startswith("AST-")
    assert a.periodo_id is not None
    d = f.obtener_diario(db, origen_tipo="APERTURA")
    assert d["n"] >= 1 and d["cuadrado"] is True
    assert d["total_debe"] == d["total_haber"]
    per = db.get(__import__("app.models.finanzas", fromlist=["PeriodoContable"]).PeriodoContable, a.periodo_id)
    assert per is not None
    may = f.obtener_mayor(db, periodo_id=a.periodo_id)
    assert len(may) >= 2
    # limpieza
    from app.models.finanzas import LineaAsientoContable
    ids = [x.id for x in db.query(AsientoContable).filter(AsientoContable.origen_tipo == "APERTURA").all()]
    db.query(LineaAsientoContable).filter(LineaAsientoContable.asiento_id.in_(ids)).delete(synchronize_session=False)
    db.query(AsientoContable).filter(AsientoContable.id.in_(ids)).delete(synchronize_session=False)
    db.commit(); db.close()


def test_init_production_db_idempotente_y_health_db():
    from app.core import startup as st
    from fastapi.testclient import TestClient
    from app.main import app
    status = st.init_production_db()
    assert set(status) >= {"migrations", "tables", "pcge", "admin"}
    assert status["tables"] == []
    assert status["admin"] in ("exists", "created")
    # segunda corrida: totalmente idempotente
    status2 = st.init_production_db()
    assert status2["tables"] == []
    c = TestClient(app, follow_redirects=False)
    r = c.get("/health/db")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["usuarios"] >= 1 and body["cuentas_pcge"] > 0


def test_filtro_periodo_excluye_otros_meses():
    """Filtrar 2026-01 no muestra asientos de 2026-09 (ni sin periodo)."""
    from datetime import date as _d
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c5011 = f.get_cuenta_by_codigo(db, "5011")
    for dia, mes, monto in ((15, 1, 100), (20, 9, 900)):
        f.crear_asiento(db, _d(2026, mes, dia), f"T-{mes}", "APERTURA", None, [
            {"cuenta_id": c101.id, "debe": Decimal(str(monto)), "haber": Decimal("0")},
            {"cuenta_id": c5011.id, "debe": Decimal("0"), "haber": Decimal(str(monto))},
        ])
    pid, desde, hasta = f.resolver_filtro_periodo(db, "2026-01")
    assert desde == _d(2026, 1, 1) and hasta == _d(2026, 1, 31)
    bg = f.obtener_balance_general(db, periodo_id=pid, desde=desde, hasta=hasta)
    assert bg["activo"] == Decimal("100")  # sin los 900 de septiembre
    grupos = f.obtener_mayor_agrupado(db, periodo_id=pid, desde=desde, hasta=hasta)
    g1011 = next(g for g in grupos if g["codigo"] == "1011")
    assert g1011["total_debe"] == Decimal("100")
    # Cuenta inexistente → vacío, no todo
    assert f.obtener_mayor_agrupado(db, cuenta_id=-1) == []
    _limpia(db); db.close()


def test_saldo_por_naturaleza_sin_negativos_anomalos():
    """Pasivo/ingresos (4,5,7) saldan Haber − Debe: 40111 y 1221 positivos."""
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    o = _order(db, "CT-NAT-001", 1180.0)
    C.emitir_factura_venta(db, o.id, "F001", 18.0, None)
    grupos = {g["codigo"]: g for g in f.obtener_mayor_agrupado(db)}
    assert grupos["40111"]["saldo"] == Decimal("180")
    assert grupos["7011"]["saldo"] == Decimal("1000")
    assert grupos["1212"]["saldo"] == Decimal("1180")
    _limpia(db); db.close()


def test_rubros_eerr():
    """Desglose por rubros oficiales suma a los totales."""
    from app.services import contabilidad as C
    from app.services import finanzas as f
    db = _db(); _limpia(db)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c5011 = f.get_cuenta_by_codigo(db, "5011")
    f.crear_asiento(db, date.today(), "Cap", "APERTURA", None, [
        {"cuenta_id": c101.id, "debe": Decimal("40000"), "haber": Decimal("0")},
        {"cuenta_id": c5011.id, "debe": Decimal("0"), "haber": Decimal("40000")},
    ])
    o = _order(db, "CT-RUB-001", 1180.0)
    C.emitir_factura_venta(db, o.id, "F001", 18.0, None)
    bg = f.obtener_balance_general(db)
    r = bg["rubros"]
    assert r["efectivo_10"] == Decimal("40000")
    assert r["cxc_12"] == Decimal("1180")
    assert r["total_activo"] == bg["activo"] == Decimal("41180")
    assert r["tributos_40"] == Decimal("180")
    assert r["total_pasivo"] == bg["pasivo"] == Decimal("180")
    assert r["capital_50"] == Decimal("40000")
    assert r["total_patrimonio"] == bg["patrimonio_total"] == Decimal("41000")
    _limpia(db); db.close()
