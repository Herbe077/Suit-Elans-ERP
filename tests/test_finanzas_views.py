"""Viewport móvil en Finanzas y Clientes: contenedor raíz, filtros y tablas."""


def test_raiz_y_filtros_en_columna(client, auth_cookies):
    for url in ("/finanzas/cuentas-por-cobrar", "/finanzas/cuentas-por-pagar",
                "/comercial/clientes", "/comercial/clientes?tab=empresas"):
        t = client.get(url, cookies=auth_cookies).text
        assert "w-full max-w-full overflow-x-hidden" in t, url
        assert "flex flex-col gap-2 sm:flex-row" in t, url


def test_tablas_en_cards_con_scroll(client, auth_cookies):
    for url in ("/finanzas/cuentas-por-cobrar", "/finanzas/cuentas-por-pagar",
                "/comercial/clientes"):
        t = client.get(url, cookies=auth_cookies).text
        assert "w-full overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm" in t, url
        assert "whitespace-nowrap" in t, url


def test_submenu_sin_overflow(client, auth_cookies):
    for url in ("/finanzas/gastos", "/comercial/crm"):
        t = client.get(url, cookies=auth_cookies).text
        assert "overflow-x-auto whitespace-nowrap scrollbar-none w-full" in t, url
