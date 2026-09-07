"""Rentabilidad: valor neto, costeo cerrado (kardex + tareo) y KPIs."""


def _order(db, folio, total, tela_id=None, precio=0.0):
    from app.models.order import Garment, Order
    o = Order(folio=folio, estado="confirmado", canal="sastreria", total=total)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", tela_id=tela_id, precio=precio)
    db.add(g)
    db.commit()
    return o.id, g.id


def _worklog(db, gid, minutos):
    from app.models.order import Operation, WorkLog
    op = db.query(Operation).filter(Operation.codigo == "RENT-OP").first()
    if not op:
        op = Operation(codigo="RENT-OP", nombre="Op rent", tipo_prenda="saco",
                       sam_minutos=30.0)
        db.add(op)
        db.flush()
    db.add(WorkLog(garment_id=gid, operation_id=op.id, minutos_reales=minutos,
                   estado="terminado"))
    db.commit()


def test_valor_neto_y_pendiente_liquidacion(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _order(db, "SE-RENT-001", 2360.0, tela_id=None, precio=2360.0)
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    assert "Valor Venta (Sin IGV)" in t
    assert "S/ 2000.00" in t  # 2360 / 1.18, no el total comercial
    assert "Pendiente de Liquidación" in t  # sin kardex ni tareo: sin margen


def test_costo_cerrado_kardex_mas_tareo(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    db = SessionLocal()
    oid, gid = _order(db, "SE-RENT-002", 2360.0, tela_id=None, precio=2360.0)
    prod = ProductoInsumo(sku="RENT-TELA", nombre="Tela rent", categoria="TELA",
                          unidad_medida="METROS", stock_fisico=50.0)
    db.add(prod)
    db.flush()
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                            cantidad=2.0, costo_unitario=150.0, costo_total=300.0,
                            orden_venta_id=oid))
    db.commit()
    _worklog(db, gid, 60.0)  # 60 min × 0.5 = S/ 30 M.O.
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    # Valor 2000 − (300 kardex + 30 tareo) = 1670 (83.5%)
    assert "S/ 300.00" in t and "S/ 1670.00" in t and "83.5%" in t


def test_costo_bom_mas_tareo(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventory import Fabric
    db = SessionLocal()
    fab = Fabric(codigo="RENT-FAB", nombre="Tela BOM", stock_metros=20.0,
                 precio_metro=50.0)
    db.add(fab)
    db.commit()
    oid, gid = _order(db, "SE-RENT-003", 2360.0, tela_id=fab.id, precio=2360.0)
    _worklog(db, gid, 120.0)  # 120 min × 0.5 = S/ 60 M.O.
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    # BOM: 2.0 m × 50 = 100 + 60 tareo = 160 → margen 1840 (92%)
    assert "S/ 100.00" in t and "S/ 1840.00" in t and "92.0%" in t


def test_kpis_y_centros_seed(client, auth_cookies):
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    for kpi in ("Margen Bruto Promedio", "Punto de Equilibrio", "Eficiencia Taller",
                "EBITDA"):
        assert kpi in t
    g = client.get("/finanzas/gastos", cookies=auth_cookies).text
    assert "921 - Taller: Confección" in g
    assert "951 - Comercial: Showroom" in g
