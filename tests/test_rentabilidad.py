"""Rentabilidad: valor neto sin IGV, costo Kardex/BOM y pendiente de costeo."""


def _order(db, folio, total, tela_id=None, precio=0.0):
    from app.models.order import Garment, Order
    o = Order(folio=folio, estado="confirmado", canal="sastreria", total=total)
    db.add(o)
    db.flush()
    db.add(Garment(order_id=o.id, tipo="saco", tela_id=tela_id, precio=precio))
    db.commit()
    return o.id


def test_valor_neto_y_pendiente_sin_margen_falso(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _order(db, "SE-RENT-001", 2360.0, tela_id=None, precio=2360.0)
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    assert "Valor Venta (Sin IGV)" in t
    assert "S/ 2000.00" in t  # 2360 / 1.18, no el total comercial
    assert "Pendiente de Costeo" in t  # sin kardex ni tela: sin 100% falso


def test_costo_real_desde_kardex(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    from app.models.order import Order
    db = SessionLocal()
    oid = _order(db, "SE-RENT-002", 2360.0, tela_id=None, precio=2360.0)
    prod = ProductoInsumo(sku="RENT-TELA", nombre="Tela rent", categoria="TELA",
                          unidad_medida="METROS", stock_fisico=50.0)
    db.add(prod)
    db.flush()
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                            cantidad=2.0, costo_unitario=150.0, costo_total=300.0,
                            orden_venta_id=oid))
    db.commit()
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    # Valor 2000 − costo kardex 300 = margen 1700 (85%)
    assert "S/ 300.00" in t and "S/ 1700.00" in t and "85.0%" in t


def test_costo_bom_con_tela_asignada(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventory import Fabric
    db = SessionLocal()
    fab = Fabric(codigo="RENT-FAB", nombre="Tela BOM", stock_metros=20.0,
                 precio_metro=50.0)
    db.add(fab)
    db.commit()
    _order(db, "SE-RENT-003", 2360.0, tela_id=fab.id, precio=2360.0)
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    # BOM: 2.0 m × 50 = 100 → margen 1900 (95%)
    assert "S/ 100.00" in t and "S/ 1900.00" in t and "95.0%" in t
