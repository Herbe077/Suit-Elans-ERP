"""Tareo: opciones enriquecidas [Código · Cliente (Empresa) · Concepto · Estado]."""


def _setup():
    from app.core.database import Base, SessionLocal, engine
    from app.core import security
    from app.models.user import User
    from app.models.client import Client
    from app.models.company import Company
    from app.models.order import Garment, Order
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    u = db.query(User).filter(User.email == "tareo@t.pe").first()
    if not u:
        u = User(email="tareo@t.pe", full_name="Tareo", hashed_password=security.hash_password("x"),
                 role="SASTRE-ASISTENTE", is_active=True)
        db.add(u)
        db.commit()
    e = db.query(Company).filter(Company.nombre_comercial == "Minera del Sur S.A.A.").first()
    if not e:
        e = Company(nombre_comercial="Minera del Sur S.A.A.", ruc="20600000001")
        db.add(e)
        db.flush()
    c = db.query(Client).filter(Client.nro_doc == "TAREO-DNI").first()
    if not c:
        c = Client(nombre="Julian", apellidos="Apaza", tipo_doc="DNI",
                   nro_doc="TAREO-DNI", company_id=e.id)
        db.add(c)
        db.flush()
    ob = db.query(Order).filter(Order.folio == "SE-TAR-B2B").first()
    if not ob:
        ob = Order(folio="SE-TAR-B2B", client_id=c.id, company_id=e.id,
                   estado="confirmado", total=2000.0, concepto="Saco 2 Piezas")
        db.add(ob)
        db.flush()
        db.add(Garment(order_id=ob.id, tipo="saco", precio=2000.0,
                       estado_taller="EN_CONFECCION"))
        db.commit()
    c2 = db.query(Client).filter(Client.nro_doc == "TAREO-DNI2").first()
    if not c2:
        c2 = Client(nombre="Julian", apellidos="Apaza", tipo_doc="DNI",
                    nro_doc="TAREO-DNI2")
        db.add(c2)
        db.flush()
    op = db.query(Order).filter(Order.folio == "SE-TAR-01").first()
    if not op:
        op = Order(folio="SE-TAR-01", client_id=c2.id, estado="confirmado",
                   total=800.0, concepto="Pantalón de Vestir")
        db.add(op)
        db.flush()
        db.add(Garment(order_id=op.id, tipo="pantalon", precio=800.0,
                       estado_taller="EN_CORTE"))
        db.commit()
    db.close()


def _login():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "tareo@t.pe", "password": "x"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_opciones_con_codigo_cliente_empresa_concepto_estado():
    _setup()
    c, ck = _login()
    t = c.get("/rendimiento/registro?todas=1", cookies=ck).text
    assert ("SE-TAR-B2B · Julian Apaza (Minera del Sur S.A.A.) · "
            "Saco 2 Piezas · ") in t
    assert "SE-TAR-01 · Julian Apaza · Pantalón de Vestir · " in t
    # Sin paréntesis duplicado cuando no hay empresa
    assert "Julian Apaza · Pantalón" in t
