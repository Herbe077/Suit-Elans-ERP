import os
"""Vistas Inventario: tabs de catálogo/almacén, motivo obligatorio en ajuste SKU,
unidad explícita de ancho, y alta de maestros solo en Catálogo."""


def _client():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_catalogo_tabs_y_ancho_explicito():
    c, ck = _client()
    t = c.get("/inventario/catalogo", cookies=ck).text
    assert "Materia Prima / Insumos" in t and "Prendas Terminadas" in t
    assert 'x-show="tab' in t  # conmutación por pestañas
    assert "150cm" in t and "siempre en cm" in t
    assert 'name="motivo" required' in t


def test_almacen_subtabs_sin_altas_rapidas():
    c, ck = _client()
    a = c.get("/inventario/almacen", cookies=ck).text
    assert "Cta 24" in a and "Cta 21" in a and "Cta 23" in a
    assert "Altas rápidas" not in a
    assert "Registro de Movimiento de Kardex" in a


def test_kardex_form_contextual_por_pestana():
    c, ck = _client()
    a = c.get("/inventario/almacen", cookies=ck).text
    # Insumo visible en MP, SKU solo en PT; form oculto en WIP.
    assert 'x-show="sub === \'mp\'"' in a and 'x-show="sub === \'pt\'"' in a
    assert a.count("Registro de Movimiento de Kardex") == 2  # MP + PT, nada en WIP
    assert "no por Kardex manual" in a
    mp = a.split('x-show="sub === \'mp\'"')[-1].split('x-show="sub === \'pt\'"')[0]
    pt = a.split('x-show="sub === \'pt\'"')[-1]
    # MP: insumo/tipo/cantidad/pedido/obs; sin campo SKU ni costo.
    assert 'name="producto_id"' in mp and "Producto Terminado" not in mp
    assert "Producto Terminado / SKU" not in mp
    assert 'name="variant_id"' not in mp and 'name="costo_unitario"' not in mp
    assert 'name="orden_venta_id"' in mp
    assert "Registrar Movimiento" in mp
    for code in ("ENTRADA_AJUSTE", "ENTRADA_DEVOLUCION",
                 "SALIDA_CONSUMO_TALLER", "SALIDA_MERMA", "SALIDA_AJUSTE"):
        assert f'value="{code}"' in mp, code
    assert 'value="ENTRADA_COMPRA"' not in mp  # compras van por OC
    # PT: SKU/tipo/cantidad/costo/obs; sin insumo ni pedido.
    assert 'name="variant_id"' in pt and 'name="costo_unitario"' in pt
    assert "Insumo / Tela" not in pt and 'name="orden_venta_id"' not in pt
    assert 'name="producto_id"' not in pt
    assert "Registrar Movimiento" in pt
    for code in ("ENTRADA_PRODUCTO_TERMINADO", "ENTRADA_AJUSTE",
                 "SALIDA_VENTA_RTW", "SALIDA_AJUSTE"):
        assert f'value="{code}"' in pt, code
    assert "INGRESO_PRODUCCION" not in a


def test_ajuste_sku_exige_motivo():
    c, ck = _client()
    r = c.post("/inventario/catalogo/stock",
               data={"variant_id": "1", "cantidad": "1", "motivo": ""}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = c.post("/inventario/catalogo/stock",
               data={"variant_id": "1", "cantidad": "0", "motivo": "conteo"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]
