"""Submenú de Ventas y Atención: 4 tabs, sin botones redundantes, bottom sync."""


def test_tabs_en_las_4_vistas(client, auth_cookies):
    for url, activo in (("/ventas/pos", "🛒 Punto de venta"),
                        ("/ventas/ordenes", "📋 Órdenes de venta"),
                        ("/ventas/caja", "💵 Control de caja"),
                        ("/ventas/facturacion", "🧾 Facturación")):
        t = client.get(url, cookies=auth_cookies).text
        for tab in ("Punto de venta", "Órdenes de venta", "Control de caja",
                    "Facturación"):
            assert tab in t, (url, tab)
        assert f"bg-[#3B0A11] text-white shadow-sm\">{activo}" in t, url
        assert "overflow-x-auto whitespace-nowrap scrollbar-none" in t, url


def test_sin_botones_redundantes(client, auth_cookies):
    t = client.get("/ventas/caja", cookies=auth_cookies).text
    assert "Volver a Punto de Venta" not in t
    t = client.get("/ventas/pos", cookies=auth_cookies).text
    assert "Facturar →" not in t


def test_bottom_nav_pos_en_modulo_ventas(client, auth_cookies):
    for url in ("/ventas/pos", "/ventas/ordenes", "/ventas/facturacion"):
        t = client.get(url, cookies=auth_cookies).text
        assert "🛒<br>POS" in t
    t = client.get("/ventas/ordenes", cookies=auth_cookies).text
    assert "text-laton font-bold" in t
