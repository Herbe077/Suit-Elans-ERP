"""Rentabilidad: tarifa por planilla, liquidación por estado y KPIs."""


def _order(db, folio, total, estado="confirmado", tela_id=None, precio=0.0):
    from app.models.order import Garment, Order
    o = Order(folio=folio, estado=estado, canal="sastreria", total=total)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", tela_id=tela_id, precio=precio)
    db.add(g)
    db.flush()
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
    _order(db, "SE-RENT-001", 2360.0, precio=2360.0)
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    assert "Valor Venta (Sin IGV)" in t
    assert "S/ 2000.00" in t  # 2360 / 1.18, no el total comercial
    assert "Pendiente de Liquidación" in t  # cotizada/conf. sin registros


def test_costo_cerrado_kardex_mas_tareo(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.inventario import MovimientoKardex, ProductoInsumo
    db = SessionLocal()
    oid, gid = _order(db, "SE-RENT-002", 2360.0, precio=2360.0)
    prod = ProductoInsumo(sku="RENT-TELA", nombre="Tela rent", categoria="TELA",
                          unidad_medida="METROS", stock_fisico=50.0)
    db.add(prod)
    db.flush()
    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                            cantidad=2.0, costo_unitario=150.0, costo_total=300.0,
                            orden_venta_id=oid))
    db.commit()
    _worklog(db, gid, 100.0)  # 100 min × 0.35 default = S/ 35 M.O.
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    # Valor 2000 − (300 kardex + 35 tareo) = 1665 (83.2%)
    assert "S/ 300.00" in t and "S/ 35.00" in t
    assert "S/ 1665.00" in t and "83.2%" in t


def test_entregada_liquida_sin_etiqueta(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _order(db, "SE-RENT-004", 1180.0, estado="entregado", precio=1180.0)
    db.close()
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    assert "SE-RENT-004" in t and "100.0%" in t


def test_kpis_presentes(client, auth_cookies):
    t = client.get("/finanzas/rentabilidad", cookies=auth_cookies).text
    for kpi in ("Margen Bruto Promedio", "Punto de Equilibrio", "Eficiencia Taller",
                "EBITDA", "S/ 0.35/min"):
        assert kpi in t
    g = client.get("/finanzas/gastos", cookies=auth_cookies).text
    assert "921 - Taller: Confección" in g
    assert "951 - Comercial: Showroom" in g


def test_tarifa_por_planilla_taller():
    from datetime import date
    from app.core.database import SessionLocal
    from app.models.finanzas import (AsientoContable, CentroCosto, GastoRegistrado,
                                     LineaAsientoContable)
    from app.services import finanzas as f
    db = SessionLocal()
    f.seed_pcge_basico(db)
    assert f.tarifa_minuto_taller(db) == 0.35  # sin planilla: default
    cc = db.query(CentroCosto).filter(CentroCosto.codigo == "921").first()
    assert cc is not None and cc.tipo == "TALLER"
    g, _a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="PLANILLA", monto_base=6000,
        centro_costo_id=cc.id, numero_comprobante="PL-TAR-001")
    assert f.tarifa_minuto_taller(db) == round(6000 / 11520, 4)  # 6000 / capacidad 11520
    assert f.planilla_taller_mes(db, date.today().year, date.today().month) > 0
    # limpieza
    aids = [a.id for a in db.query(AsientoContable).filter(
        AsientoContable.origen_id == g.id).all()]
    if aids:
        db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(aids)).delete(synchronize_session=False)
        db.query(AsientoContable).filter(AsientoContable.id.in_(aids)).delete(
            synchronize_session=False)
    db.query(GastoRegistrado).filter(GastoRegistrado.id == g.id).delete()
    db.commit()
    db.close()
