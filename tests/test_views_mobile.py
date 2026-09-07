"""Responsive móvil: tablas con scroll, bottom nav activo, selects y banner POS."""


def test_cxc_cxp_tablas_scroll(client, auth_cookies):
    for url in ("/finanzas/cuentas-por-cobrar", "/finanzas/cuentas-por-pagar"):
        t = client.get(url, cookies=auth_cookies).text
        assert "w-full overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm" in t, url
        assert "whitespace-nowrap" in t and "min-w-[100px]" in t, url
        assert "tabular-nums" in t, url


def test_almacen_tablas_y_subnav(client, auth_cookies):
    t = client.get("/inventario/almacen", cookies=auth_cookies).text
    assert "whitespace-nowrap" in t
    assert "min-w-[140px]" in t
    # Submenú unificado con scroll en móvil
    assert "bg-neutral-100/70 p-1.5 border-b border-neutral-200 gap-1.5" in t


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


def test_bottom_nav_cinco_accesos_con_rendimiento(client, auth_cookies):
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    for item in ("Panel", "POS", "Taller", "Rendimiento", "Menú"):
        assert item in t


def test_bottom_nav_rendimiento_activo(client, auth_cookies):
    t = client.get("/rendimiento/registro", cookies=auth_cookies).text
    assert "⚡<br>Rendimiento" in t
    assert '<a href="/rendimiento/registro" class="p-2 whitespace-nowrap text-center min-w-[56px] text-laton font-bold">' in t
    assert 'text-laton font-bold" aria-label="Abrir menú"' not in t
    # Caja sigue accesible desde los tabs de Ventas
    t = client.get("/ventas/caja", cookies=auth_cookies).text
    assert "Control de caja" in t
