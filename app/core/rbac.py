"""Matriz RBAC por ámbito funcional (6 ámbitos + módulos aislados).

Roles canónicos del ERP (6): ADMIN, VENTA, SASTRE-MAESTRO, SASTRE-ASISTENTE, ALMACEN, FINANZAS.
Regla: ADMIN ve todo. El resto solo sus módulos. Los routers la hacen
cumplir con require_roles (403); sidebar.html y base.html la usan para
mostrar/ocultar navegación (misma matriz).
Legacy (sastre/taller/operario/gerente/contador/venta/...) resuelve vía alias en can().
"""

MODULES: dict[str, tuple[str, ...]] = {
    # Panel (todo usuario logueado; el router solo exige autenticación)
    "panel": ("ADMIN", "VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "FINANZAS"),
    # Comercial y CRM
    "comercial_clientes": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    "comercial_citas": ("ADMIN", "VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    "comercial_crm": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    # Producción y Confección
    "produccion_kanban": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    "produccion_fichas": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "VENTA"),
    "produccion_pruebas": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    "produccion_calidad": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    # Módulo aislado (no va en sidebar, conserva sus rutas)
    "taller_cierre": ("ADMIN", "SASTRE-ASISTENTE"),
    "taller_pagos": ("ADMIN", "SASTRE-ASISTENTE"),
    # Venta y Atención (Front-Office)
    "ventas_pos": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    "ventas_ordenes": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    "ventas_caja": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    "ventas_facturacion": ("ADMIN", "VENTA", "SASTRE-MAESTRO"),
    # Inventario y Cadena
    "inv_catalogo": ("ADMIN", "VENTA", "ALMACEN"),
    "inv_almacen": ("ADMIN", "ALMACEN"),
    "inv_compras": ("ADMIN", "ALMACEN"),
    # Finanzas y Contabilidad (estricto)
    "finanzas_cxc": ("ADMIN", "FINANZAS"),
    "finanzas_cxp": ("ADMIN", "FINANZAS"),
    "finanzas_gastos": ("ADMIN", "FINANZAS"),
    "finanzas_flujo": ("ADMIN", "FINANZAS"),
    "finanzas_rentabilidad": ("ADMIN", "FINANZAS"),
    "finanzas_reportes": ("ADMIN", "FINANZAS"),
    "finanzas_plan": ("ADMIN", "FINANZAS"),
    "finanzas_diario": ("ADMIN", "FINANZAS"),
    "finanzas_mayor": ("ADMIN", "FINANZAS"),
    "finanzas_balance": ("ADMIN", "FINANZAS"),
    "finanzas_estados": ("ADMIN", "FINANZAS"),
    "finanzas_periodos": ("ADMIN", "FINANZAS"),
    "finanzas": ("ADMIN", "FINANZAS"),
    # Rendimiento y Destajo
    "rendimiento_registro": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    "rendimiento_calculadora": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "FINANZAS"),
    "rendimiento_tarifario": ("ADMIN", "FINANZAS", "SASTRE-MAESTRO"),
    "rendimiento": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"),
    # Administración
    "admin_usuarios": ("ADMIN",),
    "admin_config": ("ADMIN",),
    # Utilidades
    "reportes": ("ADMIN", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "VENTA"),
}


def can(role: str, module: str) -> bool:
    """True si el rol accede al módulo (ADMIN siempre, case-insensitive, con alias legacy)."""
    if not role:
        return False
    rl = role.lower()
    if rl == "admin":
        return True
    allowed = MODULES.get(module, ())
    allowed_lower = tuple(x.lower() for x in allowed)
    if rl in allowed_lower:
        return True
    # alias legacy -> canónico (sastre/taller/operario -> SASTRE-ASISTENTE, gerente/contador -> FINANZAS, venta -> VENTA)
    alias = {"venta": "venta", "ventas": "venta", "cajero": "venta", "vendedor": "venta", "recepcion": "venta", "comercial": "venta",
             "sastre": "sastre-asistente", "sastre-asistente": "sastre-asistente", "taller": "sastre-asistente",
             "operario_taller": "sastre-asistente", "operario-taller": "sastre-asistente",
             "sastre-maestro": "sastre-maestro", "maestro_sastre": "sastre-maestro", "maestro-sastre": "sastre-maestro",
             "almacen": "almacen", "almacenero": "almacen",
             "finanzas": "finanzas", "gerente": "finanzas", "contador": "finanzas", "admin": "admin", "administrador": "admin"}
    # si el rol es alias de algo permitido, también pasa
    # ej. VENTA lower=venta, si VENTA está permitido, entonces ok
    for a, b in alias.items():
        if rl == a and b in allowed_lower:
            return True
        if rl == b and a in allowed_lower:
            return True
    return False
