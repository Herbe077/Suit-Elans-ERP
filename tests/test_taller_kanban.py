"""Kanban móvil: acordeón, tarjeta con empresa y tab Taller."""


def _setup(client_cookies=None):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.company import Company
    from app.models.inventory import Fabric
    from app.models.order import Garment, Order
    db = SessionLocal()
    e = db.query(Company).filter(Company.nombre_comercial == "Corp ABC S.A.C.").first()
    if not e:
        e = Company(nombre_comercial="Corp ABC S.A.C.", ruc="20600000001")
        db.add(e)
        db.flush()
    c = db.query(Client).filter(Client.nro_doc == "KANBAN-DNI").first()
    if not c:
        c = Client(nombre="Julian", apellidos="Apaza", tipo_doc="DNI",
                   nro_doc="KANBAN-DNI", company_id=e.id)
        db.add(c)
        db.flush()
    fab = db.query(Fabric).filter(Fabric.codigo == "KANBAN-TELA").first()
    if not fab:
        fab = Fabric(codigo="KANBAN-TELA", nombre="Paño", stock_metros=30.0)
        db.add(fab)
        db.flush()
    n = db.query(Order).count() + 1
    o = Order(folio=f"SE-KAN-{n}", client_id=c.id, company_id=e.id,
              estado="confirmado", total=2000.0)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", tela_id=fab.id, precio=2000.0)
    db.add(g)
    db.commit()
    gid, folio = g.id, o.folio
    db.close()
    return gid, folio


def _taller():
    from fastapi.testclient import TestClient
    from app.core import security
    from app.core.database import SessionLocal
    from app.main import app
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == "kanban_t@t.pe").first()
    if not u:
        u = User(email="kanban_t@t.pe", full_name="T", hashed_password=security.hash_password("x"),
                 role="SASTRE-MAESTRO", is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "kanban_t@t.pe", "password": "x"},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_tab_taller_y_acordeon_movil():
    c, ck = _taller()
    gid, folio = _setup()
    t = c.get("/produccion/kanban", cookies=ck).text
    assert "🏬 Taller" in t  # pestaña activa del submódulo
    assert "Tablero de producción" in t
    assert "Fichas técnicas y medidas" in t and "Pruebas de entalle" in t
    assert "Control de calidad" in t
    assert "block sm:hidden" in t and "hidden sm:flex" in t  # acordeón vs tablero
    assert "accbox-" in t
    assert f"{folio} — Saco #{gid}" in t  # pedido + prenda
    assert "Julian Apaza (Corp ABC S.A.C.)" in t  # cliente + empresa
    assert "KANBAN-TELA" in t  # badge tela/insumo
    assert "Ficha técnica" in t


def test_patch_mueve_y_cuenta_unica():
    c, ck = _taller()
    gid, _ = _setup()
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "EN_CORTE"},
                cookies=ck)
    assert r.status_code == 200
    assert r.text.count(f'id="card-{gid}"') == 1  # desktop una vez
    assert f'id="mcard-{gid}"' in r.text  # móvil refresca por OOB


def test_kanban_excluye_cotizaciones():
    """Solo VENTA_CONFIRMADA/COMPLETADA en el tablero; COTIZACION jamás."""
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Garment, Order
    c, ck = _taller()
    db = SessionLocal()
    cli = db.query(Client).filter(Client.nro_doc == "KANBAN-DNI").first()
    n = db.query(Order).count() + 1
    o_cot = Order(folio=f"SE-KAN-COT-{n}", client_id=cli.id,
                  estado="cotizado", total=500.0)
    db.add(o_cot)
    db.flush()
    g_cot = Garment(order_id=o_cot.id, tipo="pantalon", precio=500.0,
                    estado_taller="POR_CORTAR")
    db.add(g_cot)
    db.commit()
    folio_cot, gid_cot = o_cot.folio, g_cot.id
    db.close()
    t = c.get("/produccion/kanban", cookies=ck).text
    assert folio_cot not in t  # cotización fuera del tablero
    # confirmada sí aparece
    from app.core.database import SessionLocal
    db = SessionLocal()
    db.get(Order, o_cot.id).estado = "confirmado"
    db.commit()
    db.close()
    t = c.get("/produccion/kanban", cookies=ck).text
    assert folio_cot in t
    # limpieza
    db = SessionLocal()
    db.query(Garment).filter(Garment.id == gid_cot).delete()
    db.query(Order).filter(Order.id == o_cot.id).delete()
    db.commit()
    db.close()
