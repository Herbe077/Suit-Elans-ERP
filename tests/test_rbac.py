import os
"""RBAC por ámbitos: cada rol entra a sus módulos (200) y recibe 403 en el resto."""
from fastapi.testclient import TestClient

from app.core.rbac import MODULES
from app.main import app
from app.routers.legacy import REDIRECTS

# Ruta -> roles con 200 (ADMIN siempre 200). Debe coincidir con MODULES (canónicos).
PAGES = {
    "/dashboard": ["VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "FINANZAS"],
    "/comercial/clientes": ["VENTA", "SASTRE-MAESTRO"],
    "/comercial/citas": ["VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"],
    "/comercial/crm": ["VENTA", "SASTRE-MAESTRO"],
    "/produccion/kanban": ["SASTRE-MAESTRO", "SASTRE-ASISTENTE"],
    "/produccion/fichas": ["SASTRE-MAESTRO", "SASTRE-ASISTENTE", "VENTA"],
    "/produccion/pruebas": ["SASTRE-MAESTRO", "SASTRE-ASISTENTE"],
    "/produccion/calidad": ["SASTRE-MAESTRO", "SASTRE-ASISTENTE"],
    "/taller/cierre-jornada": ["SASTRE-ASISTENTE"],
    "/taller/reporte-pagos": ["SASTRE-ASISTENTE"],
    "/ventas/pos": ["VENTA", "SASTRE-MAESTRO"],
    "/ventas/ordenes": ["VENTA", "SASTRE-MAESTRO"],
    "/ventas/comprobantes": ["VENTA", "SASTRE-MAESTRO"],
    "/ventas/caja": ["VENTA", "SASTRE-MAESTRO"],
    "/ventas/facturacion": ["VENTA", "SASTRE-MAESTRO"],
    "/inventario/catalogo": ["VENTA", "ALMACEN"],
    "/inventario/almacen": ["ALMACEN"],
    "/inventario/compras": ["ALMACEN"],
    "/admin/usuarios": [],
    "/admin/configuracion": [],
}

ROLES = ["ADMIN", "VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "FINANZAS"]
EMAILS = {"ADMIN": "admin@suitelans.mx", "VENTA": "rbac_venta@t.pe",
          "SASTRE-MAESTRO": "rbac_maestro@t.pe", "SASTRE-ASISTENTE": "rbac_asistente@t.pe",
          "ALMACEN": "rbac_almacen@t.pe", "FINANZAS": "rbac_finanzas@t.pe"}


def _session(role: str):
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    email = EMAILS[role]
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, full_name=f"RBAC {role}",
                 hashed_password=security.hash_password("x"), role=role,
                 is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    password = os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password") if role == "ADMIN" else "x"
    r = c.post("/auth/login", data={"username": email, "password": password},
               follow_redirects=False)
    assert r.status_code == 303, role
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_matriz_enforcement():
    sessions = {role: _session(role) for role in ROLES}
    for path, ok_roles in PAGES.items():
        for role in ROLES:
            c, ck = sessions[role]
            r = c.get(path, cookies=ck)
            if role == "ADMIN" or role in ok_roles:
                assert r.status_code == 200, f"{role} {path} -> {r.status_code}"
            else:
                assert r.status_code == 403, f"{role} {path} -> {r.status_code} (esperado 403)"


def test_sin_login_redirige():
    c = TestClient(app)
    assert c.get("/comercial/crm", follow_redirects=False).status_code == 303
    assert c.get("/taller/reporte-pagos", follow_redirects=False).status_code == 303


def test_legacy_redirects():
    c, ck = _session("ADMIN")
    for old, new in REDIRECTS.items():
        r = c.get(old, cookies=ck, follow_redirects=False)
        assert r.status_code in (302, 303, 307), f"{old} -> {r.status_code}"
        assert r.headers["location"] == new, f"{old} -> {r.headers['location']}"


def test_nav_oculta_modulos():
    c, ck = _session("SASTRE-ASISTENTE")
    r = c.get("/dashboard", cookies=ck)
    for href in ["/comercial/crm", "/comercial/clientes", "/ventas/caja",
                 "/ventas/facturacion", "/admin/usuarios", "/inventario/almacen",
                 "/inventario/compras"]:
        assert f'href="{href}"' not in r.text, f"asistente ve {href}"
    for href in ["/comercial/citas", "/produccion/kanban", "/produccion/fichas",
                 "/taller/cierre-jornada"]:
        assert f'href="{href}"' in r.text, f"asistente no ve {href}"
    # reporte-pagos no va en el menú (módulo aparte): se llega desde cierre
    r = c.get("/taller/cierre-jornada", cookies=ck)
    assert 'href="/taller/reporte-pagos"' in r.text
    assert "Por cobrar" not in r.text
    c2, ck2 = _session("ADMIN")
    r = c2.get("/dashboard", cookies=ck2)
    assert 'href="/admin/usuarios"' in r.text and "Por cobrar" in r.text


def test_matriz_coherente_con_rbac():
    path_module = {"/dashboard": "panel", "/comercial/clientes": "comercial_clientes",
                   "/comercial/citas": "comercial_citas", "/comercial/crm": "comercial_crm",
                   "/produccion/kanban": "produccion_kanban",
                   "/produccion/fichas": "produccion_fichas",
                   "/produccion/pruebas": "produccion_pruebas",
                   "/produccion/calidad": "produccion_calidad",
                   "/taller/cierre-jornada": "taller_cierre",
                   "/taller/reporte-pagos": "taller_pagos",                    "/ventas/pos": "ventas_pos",
                   "/ventas/ordenes": "ventas_ordenes",
                   "/ventas/comprobantes": "ventas_facturacion",
                   "/ventas/caja": "ventas_caja", "/ventas/facturacion": "ventas_facturacion",
                   "/inventario/catalogo": "inv_catalogo",
                   "/inventario/almacen": "inv_almacen",
                   "/inventario/compras": "inv_compras",
                   "/admin/usuarios": "admin_usuarios",
                   "/admin/configuracion": "admin_config"}
    for path, ok_roles in PAGES.items():
        assert set(MODULES[path_module[path]]) == set(ok_roles) | {"ADMIN"}, path
