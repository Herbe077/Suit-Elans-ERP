"""Vistas Inventario: tabs de catálogo/almacén, motivo obligatorio en ajuste SKU,
unidad explícita de ancho, y alta de maestros solo en Catálogo."""


def _client():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": "admin123"})
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


def test_ajuste_sku_exige_motivo():
    c, ck = _client()
    r = c.post("/inventario/catalogo/stock",
               data={"variant_id": "1", "cantidad": "1", "motivo": ""}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = c.post("/inventario/catalogo/stock",
               data={"variant_id": "1", "cantidad": "0", "motivo": "conteo"}, cookies=ck)
    assert r.status_code == 303 and "error=" in r.headers["location"]
