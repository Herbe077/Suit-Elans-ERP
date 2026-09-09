"""FASE 2: finalización de OP de colección / stock comercial.

COMPLETADA/FINALIZADA => en UN commit: descuenta insumos por BOM,
costea (insumos CPP + destajo MOD), actualiza costo/stock del SKU,
Kardex SALIDA_CONSUMO_TALLER + asientos de OP asociados.
"""


def _setup(tag, tela_stock=100.0, avio_stock=50.0):
    from app.core.database import SessionLocal
    from app.models.catalog import Product, ProductVariant
    from app.models.inventario import ProductoInsumo
    from app.models.produccion import OrdenProduccion
    db = SessionLocal()
    tela = ProductoInsumo(sku=f"OPC-TELA-{tag}", nombre="Tela col",
                          categoria="TELA", unidad_medida="METROS",
                          costo_unitario=10.0, costo_promedio=10.0,
                          stock_fisico=tela_stock)
    avio = ProductoInsumo(sku=f"OPC-AVIO-{tag}", nombre="Avío col",
                          categoria="AVIO", unidad_medida="UNIDADES",
                          costo_unitario=2.0, costo_promedio=2.0,
                          stock_fisico=avio_stock)
    db.add_all([tela, avio])
    db.commit()
    p = Product(codigo=f"COL-{tag}", nombre="Saco colección",
                linea="comercial")
    db.add(p)
    db.commit()
    v = ProductVariant(product_id=p.id, talla="M", sku=f"SKU-COL-{tag}",
                       stock=0.0, precio=500.0, costo_unitario=0.0)
    db.add(v)
    db.commit()
    from app.models.user import User
    admin = db.query(User).filter(User.email == "admin@suitelans.mx").first()
    op = OrdenProduccion(orden_venta_id=None, codigo_qr=f"OPC-{tag}",
                         estado="EN_CONFECCION",
                         sastre_asignado_id=admin.id if admin else None)
    db.add(op)
    db.commit()
    ids = (tela.id, avio.id, p.id, v.id, op.id, op.codigo_qr)
    db.close()
    return ids


def _bom(tid, aid):
    return [{"producto_insumo_id": tid, "cantidad_por_unidad": 2.0},
            {"producto_insumo_id": aid, "cantidad_por_unidad": 3.0}]


def test_finalizar_op_descuenta_costea_e_ingresa_pt(client, auth_cookies):
    tid, aid, _pid, vid, opid, code = _setup("F1")
    r = client.post(f"/produccion/op/{opid}/finalizar",
                    json={"variant_id": vid, "cantidad": 10,
                          "insumos": _bom(tid, aid),
                          "mano_obra_directa": 150.0,
                          "estado_final": "COMPLETADA"},
                    cookies=auth_cookies,
                    headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    # b) insumos 10u × (2m×10 + 3×2) = 260 ; + MOD 150 = 410 ; c) unit 41
    assert body["costo_insumos"] == 260.0
    assert body["mano_obra_directa"] == 150.0
    assert body["costo_total"] == 410.0
    assert body["costo_unitario"] == 41.0
    assert body["estado"] == "COMPLETADA"

    from app.core.database import SessionLocal
    from app.models.catalog import ProductVariant
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.produccion import OrdenProduccion
    db = SessionLocal()
    # a) insumos descontados
    assert db.get(ProductoInsumo, tid).stock_fisico == 80.0
    assert db.get(ProductoInsumo, aid).stock_fisico == 20.0
    # d+e) costo y stock del SKU
    v = db.get(ProductVariant, vid)
    assert v.costo_unitario == 41.0 and v.stock == 10.0
    # f) Kardex SALIDA_CONSUMO_TALLER asociado a la OP
    ks = db.query(MovimientoKardex).filter(
        MovimientoKardex.doc_ref == code,
        MovimientoKardex.tipo_movimiento == "SALIDA_CONSUMO_TALLER").all()
    assert len(ks) == 2
    assert sorted(k.cantidad for k in ks) == [20.0, 30.0]
    assert all(k.asiento_id is not None for k in ks)
    assert sorted(k.costo_total for k in ks) == [60.0, 200.0]
    # cierre OP 2111/2311 por 410 + MOD imputada 2311/791 por 150
    from decimal import Decimal
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    def _mapa(asiento_id):
        mapa = {}
        for l in db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == asiento_id).all():
            from app.models.finanzas import CuentaContable
            c = db.get(CuentaContable, l.cuenta_id)
            mapa[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
        return mapa
    a = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == opid,
        AsientoContable.glosa.contains("Cierre OP")).one()
    mapa = _mapa(a.id)
    assert mapa["2111"] == (Decimal("410"), Decimal("0"))
    assert mapa["2311"] == (Decimal("0"), Decimal("410"))
    w = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == opid,
        AsientoContable.glosa.contains("MOD OP")).one()
    mapaw = _mapa(w.id)
    assert mapaw["2311"] == (Decimal("150"), Decimal("0"))
    assert mapaw["791"] == (Decimal("0"), Decimal("150"))
    # consumo a WIP: tela 2311/2411 por 200
    k_tela = next(k for k in ks if k.cantidad == 20.0)
    mapak = _mapa(k_tela.asiento_id)
    assert mapak["2311"] == (Decimal("200"), Decimal("0"))
    assert mapak["2411"] == (Decimal("0"), Decimal("200"))
    assert db.get(OrdenProduccion, opid).estado == "COMPLETADA"
    db.close()


def test_finalizar_sin_stock_hace_rollback_total(client, auth_cookies):
    tid, aid, _pid, vid, opid, code = _setup("F2", tela_stock=5.0)
    r = client.post(f"/produccion/op/{opid}/finalizar",
                    json={"variant_id": vid, "cantidad": 10,
                          "insumos": _bom(tid, aid),
                          "mano_obra_directa": 150.0},
                    cookies=auth_cookies,
                    headers={"Accept": "application/json"})
    assert r.status_code == 400
    from app.core.database import SessionLocal
    from app.models.catalog import ProductVariant
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.produccion import OrdenProduccion
    db = SessionLocal()
    # nada se movió: stocks, SKU, estado OP intactos y sin kardex de la OP
    assert db.get(ProductoInsumo, tid).stock_fisico == 5.0
    assert db.get(ProductoInsumo, aid).stock_fisico == 50.0
    v = db.get(ProductVariant, vid)
    assert v.stock == 0.0 and v.costo_unitario == 0.0
    assert db.get(OrdenProduccion, opid).estado == "EN_CONFECCION"
    assert db.query(MovimientoKardex).filter(
        MovimientoKardex.doc_ref == code).count() == 0
    db.close()


def test_finalizar_es_idempotente(client, auth_cookies):
    tid, aid, _pid, vid, opid, _code = _setup("F3")
    payload = {"variant_id": vid, "cantidad": 2,
               "insumos": _bom(tid, aid), "mano_obra_directa": 10.0}
    r1 = client.post(f"/produccion/op/{opid}/finalizar", json=payload,
                     cookies=auth_cookies,
                     headers={"Accept": "application/json"})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/produccion/op/{opid}/finalizar", json=payload,
                     cookies=auth_cookies,
                     headers={"Accept": "application/json"})
    assert r2.status_code == 400 and "finalizada" in r2.json()["error"]
    from app.core.database import SessionLocal
    from app.models.catalog import ProductVariant
    db = SessionLocal()
    # sin doble descuento ni doble ingreso: 2u => tela -4, stock PT 2
    assert db.get(ProductVariant, vid).stock == 2.0
    db.close()


def test_finalizar_usa_receta_y_destajo(client, auth_cookies):
    from app.core.database import SessionLocal
    tid, aid, pid, vid, opid, code = _setup("F4")
    db = SessionLocal()
    from app.models.user import User
    admin = db.query(User).filter(
        User.email == "admin@suitelans.mx").first()
    admin_id = admin.id
    db.close()
    # receta asignada al producto (BOM de la ficha técnica)
    db = SessionLocal()
    from app.services.produccion_coleccion import crear_receta
    crear_receta(db, pid, "Saco colección", _bom(tid, aid))
    db.close()
    # destajo del operario referenciando la OP
    db = SessionLocal()
    from app.services.taller_cierre import seed_tareas
    from app.models.taller import TallerTarea
    seed_tareas(db)
    t = db.query(TallerTarea).first()
    tid_tarea, tarifa = t.id, float(t.tarifa)
    db.close()
    db = SessionLocal()
    from app.services.taller_cierre import registrar_cierre
    registrar_cierre(db, admin_id, code, [tid_tarea])
    db.close()
    # sin insumos ni MOD explícitos: receta + destajo
    r = client.post(f"/produccion/op/{opid}/finalizar",
                    json={"variant_id": vid, "cantidad": 5,
                          "estado_final": "FINALIZADA"},
                    cookies=auth_cookies,
                    headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado"] == "FINALIZADA"
    assert body["mano_obra_directa"] == round(tarifa, 2)
    # 5u × (2×10 + 3×2) = 130 insumos
    assert body["costo_insumos"] == 130.0
    assert body["costo_total"] == round(130.0 + tarifa, 2)
    from app.models.catalog import ProductVariant
    db = SessionLocal()
    v = db.get(ProductVariant, vid)
    assert v.stock == 5.0
    assert v.costo_unitario == round((130.0 + tarifa) / 5, 2)
    db.close()


def test_cierre_liquida_9211_via_7111_y_wip_en_cero(client, auth_cookies):
    """Liquidación de costos: 9211 → 2111 vía 7111; WIP 2311 de la OP en cero."""
    from decimal import Decimal
    tid, aid, _pid, vid, opid, _code = _setup("LQ")
    r = client.post(f"/produccion/op/{opid}/finalizar",
                    json={"variant_id": vid, "cantidad": 10,
                          "insumos": _bom(tid, aid),
                          "mano_obra_directa": 150.0,
                          "estado_final": "COMPLETADA"},
                    cookies=auth_cookies,
                    headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asiento_liquidacion_id"]
    from app.core.database import SessionLocal
    from app.models.finanzas import AsientoContable, CuentaContable, LineaAsientoContable
    db = SessionLocal()
    def _mapa(asiento_id):
        mapa = {}
        for l in db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == asiento_id).all():
            c = db.get(CuentaContable, l.cuenta_id)
            mapa[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
        return mapa
    liq = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == opid,
        AsientoContable.glosa.contains("Liquidación 9211")).one()
    assert liq.id == body["asiento_liquidacion_id"]
    mapa_liq = _mapa(liq.id)
    assert mapa_liq["7111"] == (Decimal("150"), Decimal("0"))
    assert mapa_liq["9211"] == (Decimal("0"), Decimal("150"))
    # WIP 2311 de la OP: +260 consumo +150 MOD −410 cierre = 0 al costo real.
    debe_2311 = Decimal("0")
    haber_2311 = Decimal("0")
    for a in db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "PRODUCCION",
            AsientoContable.origen_id == opid).all():
        for cod, (d, h) in _mapa(a.id).items():
            if cod == "2311":
                debe_2311 += d
                haber_2311 += h
    for k_id in body["asientos_salida_ids"]:
        for cod, (d, h) in _mapa(k_id).items():
            if cod == "2311":
                debe_2311 += d
                haber_2311 += h
    assert debe_2311 == Decimal("410") and haber_2311 == Decimal("410")
    # PT 2111 capitaliza el costo total real.
    cierre = db.query(AsientoContable).filter(
        AsientoContable.origen_tipo == "PRODUCCION",
        AsientoContable.origen_id == opid,
        AsientoContable.glosa.contains("Cierre OP")).one()
    assert _mapa(cierre.id)["2111"] == (Decimal("410"), Decimal("0"))
    db.close()
