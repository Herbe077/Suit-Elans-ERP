"""Escenario completo: Compra→MPD→Rendimiento→MOD→CIF→Costo→Venta→CostoVentas→Cobro→EEFF"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from decimal import Decimal
from datetime import date

def test_flujo_completo():
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    from app.models.finanzas import GastoRegistrado, CentroCosto, CuentaPorCobrar, CuentaPorPagar
    from sqlalchemy import text
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cargar_plan_operativo(db)
    # limpia
    db.query(GastoRegistrado).delete()
    from app.models.finanzas import AsientoContable, LineaAsientoContable
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.commit()
    cc_taller = db.query(CentroCosto).filter(CentroCosto.codigo=="TALLER").first()
    if not cc_taller:
        cc_taller = CentroCosto(codigo="TALLER", nombre="Taller", tipo="TALLER")
        db.add(cc_taller); db.commit(); db.refresh(cc_taller)
    # 1. Compra tela -> Inventario MP (simulado como Gasto MPD)
    db.add(GastoRegistrado(proveedor_id=None, monto_total=100, clasificacion="MPD", variabilidad="VARIABLE", centro_costo_id=cc_taller.id, fecha_emision=date.today()))
    db.commit()
    # 2. Consumo en producción -> MPD leído
    mpd = f.calcular_mpd(db)
    assert mpd == Decimal("100")
    # 3. Rendimiento -> MOD devengada
    with engine.connect() as conn:
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_catalogo (id,codigo,nombre_operacion,tarifa_base,activa) VALUES (996,'OP-96','MOD Int',1,1)"))
        conn.execute(text("INSERT OR IGNORE INTO rendimiento_registros (id,operario_id,fecha,estado) VALUES (996,1,'2026-09-20','REGISTRADO')"))
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=996"))
        conn.execute(text("INSERT INTO rendimiento_detalles (id,registro_jornada_id,operacion_id,cantidad,tarifa_aplicada,subtotal) VALUES (996,996,996,1,50,50)"))
        conn.commit()
    mod = f.calcular_mod_devengada(db)
    assert mod == Decimal("50")
    # 4. CIF
    db.add(GastoRegistrado(proveedor_id=None, monto_total=30, clasificacion="CIF", variabilidad="FIJO", centro_costo_id=cc_taller.id, fecha_emision=date.today()))
    db.commit()
    cif = f.calcular_cif(db)["aplicado"]
    assert cif == Decimal("30")
    # 5. Costo producción
    costo_prod = f.calcular_costo_produccion(db)
    assert costo_prod == Decimal("180")  # 100+50+30
    # 6. Venta: Ingreso + IGV + CxC (partida doble)
    c101 = f.get_cuenta_by_codigo(db, "1011")
    c121 = f.get_cuenta_by_codigo(db, "1212")
    c701 = f.get_cuenta_by_codigo(db, "7021")
    c4011 = f.get_cuenta_by_codigo(db, "40111")
    c231 = f.get_cuenta_by_codigo(db, "2311")
    c691 = f.get_cuenta_by_codigo(db, "6921")
    # Venta 200 + IGV 36 = 236
    a_venta = f.crear_asiento(db, date.today(), "Venta traje", "VENTA", 700, [
        {"cuenta_id": c121.id, "debe": Decimal("236"), "haber": Decimal("0")},
        {"cuenta_id": c701.id, "debe": Decimal("0"), "haber": Decimal("200")},
        {"cuenta_id": c4011.id, "debe": Decimal("0"), "haber": Decimal("36")},
    ])
    # CxC pendiente
    db.add(CuentaPorCobrar(order_id=1, cliente_id=1, monto_total=236, monto_pagado=0, saldo_pendiente=236, estado="PENDIENTE"))
    db.commit()
    # 7. Costo de ventas (separado)
    a_costo = f.crear_asiento(db, date.today(), "Costo ventas", "PRODUCCION", 701, [
        {"cuenta_id": c691.id, "debe": Decimal("180"), "haber": Decimal("0")},
        {"cuenta_id": c231.id, "debe": Decimal("0"), "haber": Decimal("180")},
    ])
    assert f.calcular_costo_ventas(db) >= Decimal("180")
    # 8. Cobro -> Caja/Banco + cancelación CxC
    cxc = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id==1).first()
    cxc.monto_pagado = Decimal("236")
    cxc.saldo_pendiente = Decimal("0")
    cxc.estado = "COBRADO"
    # asiento cobro: DEBE Caja HABER Clientes
    f.crear_asiento(db, date.today(), "Cobro cliente", "COBRO", 702, [
        {"cuenta_id": c101.id, "debe": Decimal("236"), "haber": Decimal("0")},
        {"cuenta_id": c121.id, "debe": Decimal("0"), "haber": Decimal("236")},
    ])
    db.commit()
    # 9. Estados financieros derivados del mayor (no manual)
    er = f.obtener_estado_resultados(db)
    assert er["ventas"] == Decimal("200")
    assert er["costo_ventas"] == Decimal("180")
    assert er["utilidad_bruta"] == Decimal("20")
    bg = f.obtener_balance_general(db)
    assert bg["valida"] is True
    bal = f.obtener_balance_comprobacion(db)
    total_debe = sum(r["debe"] for r in bal)
    total_haber = sum(r["haber"] for r in bal)
    assert total_debe == total_haber
    # cleanup
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM rendimiento_detalles WHERE id=996"))
        conn.execute(text("DELETE FROM rendimiento_registros WHERE id=996"))
        conn.execute(text("DELETE FROM rendimiento_catalogo WHERE id=996"))
        conn.commit()
    db.query(GastoRegistrado).delete()
    db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id==1).delete()
    db.query(LineaAsientoContable).delete()
    db.query(AsientoContable).delete()
    db.commit()
    db.close()
