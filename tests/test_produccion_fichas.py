"""Ficha de prenda vs medidas de cliente: sin duplicidad de vistas."""


def _auth():
    from fastapi.testclient import TestClient
    from app.core import security
    from app.core.database import SessionLocal
    from app.main import app
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == "ficha@t.pe").first()
    if not u:
        u = User(email="ficha@t.pe", full_name="Ficha", hashed_password=security.hash_password("x"),
                 role="SASTRE-MAESTRO", is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "ficha@t.pe", "password": "x"},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _setup(con_medidas=True):
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.measurement import Measurement
    from app.models.order import Garment, Order
    db = SessionLocal()
    tag = "MED" if con_medidas else "NOMED"
    c = db.query(Client).filter(Client.nro_doc == f"FICHA-{tag}").first()
    if not c:
        c = Client(nombre="Ficha", apellidos=tag.title(), tipo_doc="DNI",
                   nro_doc=f"FICHA-{tag}")
        db.add(c)
        db.commit()
        db.refresh(c)
    mid = None
    if con_medidas:
        m = db.query(Measurement).filter(Measurement.client_id == c.id).first()
        if not m:
            m = Measurement(client_id=c.id, tipo_prenda="saco", pecho=100.0)
            m.version = 1
            db.add(m)
            db.commit()
            db.refresh(m)
        mid = m.id
    o = db.query(Order).filter(Order.folio == f"SE-FICHA-{tag}").first()
    if not o:
        o = Order(folio=f"SE-FICHA-{tag}", client_id=c.id, estado="confirmado",
                  total=1000.0)
        db.add(o)
        db.flush()
        db.add(Garment(order_id=o.id, tipo="saco", measurement_id=mid, precio=1000.0))
        db.commit()
    g = db.query(Garment).filter(Garment.order_id == o.id).first()
    gid, cid = g.id, c.id
    db.close()
    return gid, cid


def test_tab_medidas_cliente():
    c, ck = _auth()
    t = c.get("/produccion/fichas", cookies=ck).text
    assert "Fichas técnicas y medidas" in t


def test_ficha_muestra_medidas_y_link_perfil():
    c, ck = _auth()
    gid, cid = _setup(con_medidas=True)
    t = c.get(f"/produccion/ficha/{gid}", cookies=ck).text
    assert "Medidas antropométricas" in t
    assert "Medidas del cliente (lectura)" not in t
    assert f"/produccion/fichas/{cid}" in t  # ver perfil de medidas
    assert "100" in t  # pecho registrado


def test_ficha_sin_medidas_ofrece_registro_inline():
    c, ck = _auth()
    gid, _cid = _setup(con_medidas=False)
    t = c.get(f"/produccion/ficha/{gid}", cookies=ck).text
    assert "Registrar Medidas" in t
    r = c.post(f"/produccion/ficha/{gid}/medidas",
               data={"tipo_prenda": "saco", "pecho": "104", "cuello": "42"},
               cookies=ck, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/produccion/ficha/{gid}?ok=ficha")
    from app.core.database import SessionLocal
    from app.models.measurement import Measurement
    from app.models.order import Garment
    db = SessionLocal()
    g = db.get(Garment, gid)
    m = db.get(Measurement, g.measurement_id)
    assert m is not None and m.pecho == 104.0 and m.cuello == 42.0
    db.close()
    t = c.get(f"/produccion/ficha/{gid}", cookies=ck).text
    assert "104" in t  # ya visible sin salir del expediente
