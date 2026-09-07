"""Responsive móvil: tablas con scroll, bottom nav activo, selects y banner POS."""


def test_cxc_cxp_tablas_scroll(client, auth_cookies):
    for url in ("/finanzas/cuentas-por-cobrar", "/finanzas/cuentas-por-pagar"):
        t = client.get(url, cookies=auth_cookies).text
        assert "w-full overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0" in t, url
        assert "whitespace-nowrap" in t and "min-w-[100px]" in t, url
        assert "tabular-nums" in t, url


def test_almacen_tablas_y_selects(client, auth_cookies):
    t = client.get("/inventario/almacen", cookies=auth_cookies).text
    assert t.count("w-full overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0") >= 4
    assert "min-w-[140px]" in t
    # Un selector en móvil, tabs clásicos solo en escritorio
    assert 'class="block sm:hidden w-full' in t
    assert 'class="hidden sm:flex gap-2 mb-4 text-xs"' in t


def test_bottom_nav_activo_por_ruta(client, auth_cookies):
    t = client.get("/finanzas/cuentas-por-cobrar", cookies=auth_cookies).text
    assert 'text-laton font-bold" aria-label="Abrir menú"' in t  # Finanzas → Menú
    t = client.get("/inventario/almacen", cookies=auth_cookies).text
    assert 'text-laton font-bold" aria-label="Abrir menú"' in t  # Almacén → Menú
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert 'text-laton font-bold" aria-label="Abrir menú"' not in t  # POS directo
    assert "🛒<br>POS" in t


def test_pos_banner_compacto(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert "Sin turno abierto" in t
    assert "rounded-xl px-3 py-1.5 mb-3 text-xs" in t
