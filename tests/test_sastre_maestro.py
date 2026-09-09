"""SASTRE-MAESTRO opera con totalidad comercial, producción, ventas y rendimiento."""
from fastapi.testclient import TestClient

from app.main import app

EMAIL = "rbac_maestro@t.pe"

PAGINAS_TOTALES = [
    "/dashboard",
    "/comercial/clientes", "/comercial/citas", "/comercial/crm",
    "/produccion/kanban", "/produccion/fichas", "/produccion/pruebas", "/produccion/calidad",
    "/ventas/pos", "/ventas/ordenes", "/ventas/caja", "/ventas/facturacion", "/ventas/comprobantes",
    "/rendimiento/registro", "/rendimiento/calculadora", "/rendimiento/tarifario",
]

SOLO_ADMIN = ["/admin/usuarios", "/admin/configuracion"]
AJENOS = ["/inventario/almacen", "/inventario/compras", "/taller/cierre-jornada",
          "/finanzas/cuentas-por-cobrar", "/finanzas/gastos"]


def _session():
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == EMAIL).first()
    if not u:
        u = User(email=EMAIL, full_name="RBAC Sastre Maestro",
                 hashed_password=security.hash_password("x"), role="SASTRE-MAESTRO",
                 is_active=True)
        db.add(u)
        db.commit()
    else:
        u.role = "SASTRE-MAESTRO"
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": EMAIL, "password": "x"}, follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_maestro_acceso_total():
    c, ck = _session()
    for path in PAGINAS_TOTALES:
        r = c.get(path, cookies=ck)
        assert r.status_code == 200, f"sastre-maestro {path} -> {r.status_code}"


def test_maestro_restringidos():
    c, ck = _session()
    for path in SOLO_ADMIN + AJENOS:
        r = c.get(path, cookies=ck)
        assert r.status_code == 403, f"sastre-maestro {path} -> {r.status_code} (esperado 403)"


def test_maestro_menu_visible():
    c, ck = _session()
    r = c.get("/dashboard", cookies=ck)
    assert r.status_code == 200
    for href in ["/comercial/clientes", "/comercial/crm", "/ventas/pos", "/ventas/caja",
                 "/produccion/kanban", "/rendimiento/registro"]:
        assert f'href="{href}"' in r.text, f"maestro no ve {href}"


def test_maestro_variantes_rol():
    """Guion, mayúsculas y plural resuelven igual."""
    from app.core.deps import _role_match
    for variant in ["sastre-maestro", "SASTRE-MAESTRO", "sastre_maestro", "sastre-maestros"]:
        assert _role_match(variant, "sastre-maestro"), variant
        assert _role_match(variant, "sastre"), variant
