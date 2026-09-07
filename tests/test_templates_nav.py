"""Componente sub_nav unificado en los 7 módulos (activo automático por URL)."""

MODULOS = {
    "/comercial/clientes": ("🤝 Clientes", "📅 Citas"),
    "/ventas/pos": ("🛒 Punto de venta", "🧾 Facturación"),
    "/produccion/fichas": ("🏭 Taller", "✔ Calidad"),
    "/inventario/almacen": ("🧵 Catálogo", "🧾 Compras"),
    "/finanzas/gastos": ("💳 CxC", "📑 Reportes"),
    "/rendimiento/registro": ("📋 Registro Diario", "💲 Tarifario (44)"),
    "/admin/usuarios": ("👥 Usuarios y roles", "🏢 Configuración de sede"),
}


def test_subnav_presente_y_unificado(client, auth_cookies):
    for url, (tab1, tab2) in MODULOS.items():
        t = client.get(url, cookies=auth_cookies).text
        assert "bg-neutral-100/70 p-1.5 border-b border-neutral-200 gap-1.5" in t, url
        assert tab1 in t and tab2 in t, url
        assert "px-3.5 py-1.5 text-xs sm:text-sm font-medium" in t, url


def test_tab_activo_por_url(client, auth_cookies):
    casos = {"/ventas/pos": "🛒 Punto de venta",
             "/ventas/caja": "💵 Control de caja",
             "/finanzas/mayor": "📘 Mayor",
             "/inventario/compras": "🧾 Compras",
             "/produccion/kanban": "🏭 Taller",
             "/comercial/crm": "📇 CRM",
             "/rendimiento/tarifario": "💲 Tarifario (44)",
             "/admin/configuracion": "🏢 Configuración de sede"}
    for url, activo in casos.items():
        t = client.get(url, cookies=auth_cookies).text
        assert f"bg-[#3B0A11] text-white shadow-sm\">{activo}" in t, url
