"""Sidebar: solo la sección de la ruta actual inicia abierta (admin ve todo)."""


def _opens(html):
    return html.count('x-data="{ open: true }"'), html.count('x-data="{ open: false }"')


def test_inventario_solo_inventario_abierto(client, auth_cookies):
    t = client.get("/inventario/almacen", cookies=auth_cookies).text
    assert "Inventario y Cadena" in t
    assert _opens(t) == (1, 6)


def test_finanzas_solo_finanzas_abierto(client, auth_cookies):
    t = client.get("/finanzas/gastos", cookies=auth_cookies).text
    assert _opens(t) == (1, 6)


def test_ventas_y_produccion_cerradas_fuera_de_ruta(client, auth_cookies):
    for url in ("/inventario/almacen", "/finanzas/gastos", "/comercial/crm"):
        t = client.get(url, cookies=auth_cookies).text
        assert _opens(t)[0] == 1, url


def test_ventas_abre_solo_ventas(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert _opens(t) == (1, 6)
    assert "Venta y Atención" in t


def test_sub_enlace_activo_resaltado(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert 'href="/ventas/pos"' in t and "text-laton font-bold bg-white/10" in t
