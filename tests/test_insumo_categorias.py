"""Categorías de insumos estandarizadas + dinámica de formulario."""
from decimal import Decimal


def _auth():
    import os
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx",
                                    "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _nuevo_sku(tag):
    from app.core.database import SessionLocal
    from app.models.inventario import ProductoInsumo
    db = SessionLocal()
    db.query(ProductoInsumo).filter(ProductoInsumo.sku.like(f"CAT-{tag}%")).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def test_alta_avios_unidad_defecto_y_filtro():
    from app.core.database import SessionLocal
    from app.models.inventario import ProductoInsumo
    c, ck = _auth()
    _nuevo_sku("AV")
    # sin unidad explícita -> UNIDADES por categoría
    r = c.post("/inventario/catalogo/insumo",
               data={"sku": "CAT-AV-001", "nombre": "Botón nácar",
                     "categoria": "AVIOS_Y_FORNITURAS", "unidad_medida": "",
                     "costo_unitario": "0.5"},
               cookies=ck)
    assert r.status_code == 303 and "error" not in r.headers.get("location", "")
    db = SessionLocal()
    p = db.query(ProductoInsumo).filter(
        ProductoInsumo.sku == "CAT-AV-001").one()
    assert p.categoria == "AVIOS_Y_FORNITURAS"
    assert p.unidad_medida == "UNIDADES"
    # filtro por canónica lo lista; legacy TELA no lo trae
    db.close()
    t = c.get("/inventario/catalogo?categoria=AVIOS_Y_FORNITURAS",
              cookies=ck).text
    assert "CAT-AV-001" in t
    t = c.get("/inventario/catalogo?categoria=TELA_PRINCIPAL",
              cookies=ck).text
    assert "CAT-AV-001" not in t
    # legacy ?categoria=TELA resuelve a TELA_PRINCIPAL sin romper
    t = c.get("/inventario/catalogo?categoria=TELA", cookies=ck).text
    assert r.status_code in (200, 303)


def test_alta_tela_unidad_metros_y_tabs():
    from app.core.database import SessionLocal
    from app.models.inventario import ProductoInsumo
    c, ck = _auth()
    _nuevo_sku("TP")
    r = c.post("/inventario/catalogo/insumo",
               data={"sku": "CAT-TP-001", "nombre": "Casimir",
                     "categoria": "TELA_PRINCIPAL", "unidad_medida": "",
                     "composicion": "100% Lana", "ancho_cm": "150",
                     "costo_unitario": "45"},
               cookies=ck)
    assert r.status_code == 303
    db = SessionLocal()
    p = db.query(ProductoInsumo).filter(
        ProductoInsumo.sku == "CAT-TP-001").one()
    assert p.categoria == "TELA_PRINCIPAL" and p.unidad_medida == "METROS"
    db.close()
    t = c.get("/inventario/catalogo", cookies=ck).text
    for tab in ("Telas Principales", "Forros", "Entretelas/Estructura",
                "Avíos/Fornituras", "Empaques"):
        assert tab in t
    # form dinámico: categorías, unidades extra y JS
    assert "ENTRETELAS_Y_ESTRUCTURA" in t and "insCategoria()" in t
    assert "BOBINAS" in t and "CONOS" in t


def test_normalizacion_legacy():
    from app.models.inventario import es_tela, normalizar_categoria
    assert normalizar_categoria("tela") == "TELA_PRINCIPAL"
    assert normalizar_categoria("AVÍOS") == "AVIOS_Y_FORNITURAS"
    assert normalizar_categoria("empaques") == "EMPAQUES_Y_PRESENTACION"
    assert normalizar_categoria("FORRO") == "FORROS"
    assert normalizar_categoria("xxx") == "TELA_PRINCIPAL"
    assert es_tela("TELA_PRINCIPAL") and es_tela("FORROS") and es_tela("TELA")
    assert not es_tela("AVIOS_Y_FORNITURAS") and not es_tela("EMPAQUES_Y_PRESENTACION")


def test_gasto_suministros_cuenta_centro_y_tooltip():
    from app.core.database import SessionLocal
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    from datetime import date
    db = SessionLocal()
    sup = Supplier(nombre="Ferretería Din", ruc="20999999991")
    db.add(sup)
    db.commit()
    g, a = f.registrar_gasto_operativo(
        db, fecha=date.today(), categoria="SUMINISTROS_Y_HERRAMIENTAS_TALLER",
        monto_base=100, monto_igv=18, tipo_comprobante="FACTURA",
        numero_comprobante="F-SUM-001", proveedor_id=sup.id)
    from app.models.finanzas import CuentaPorPagar, LineaAsientoContable
    from app.models.finanzas import CuentaContable
    mapa = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == a.id).all():
        mapa[db.get(CuentaContable, l.cuenta_id).codigo] = (
            Decimal(str(l.debe)), Decimal(str(l.haber)))
    assert mapa["6599"] == (Decimal("100"), Decimal("0"))
    assert mapa["4212"] == (Decimal("0"), Decimal("118"))
    cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-SUM-001").one()
    assert cxp.estado == "POR_PAGAR" and cxp.saldo_pendiente == 118.0
    # tooltip + opción en el formulario
    c, ck = _auth()
    t = c.get("/finanzas/gastos", cookies=ck).text
    assert "SUMINISTROS_Y_HERRAMIENTAS_TALLER" in t
    assert "herramientas y consumibles menores" in t
    # limpieza
    from app.models.finanzas import AsientoContable, GastoRegistrado
    aids = [x.id for x in db.query(AsientoContable).filter(
        AsientoContable.origen_id == g.id).all()]
    if aids:
        db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id.in_(aids)).delete(
            synchronize_session=False)
        db.query(AsientoContable).filter(
            AsientoContable.id.in_(aids)).delete(synchronize_session=False)
    db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == "F-SUM-001").delete()
    db.query(GastoRegistrado).filter(GastoRegistrado.id == g.id).delete()
    db.query(Supplier).filter(Supplier.id == sup.id).delete()
    db.commit()
    db.close()
