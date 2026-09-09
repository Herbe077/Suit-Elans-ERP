"""Tareo con memoria: cada actividad se marca una sola vez por prenda."""
from decimal import Decimal


def _setup():
    from app.core.database import Base, SessionLocal, engine
    from app.core import security
    from app.models.user import User
    from app.models.order import Garment, Order
    from app.modules.rendimiento.models import CatalogoOperacion
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {}
    for email in ("marc_a@t.pe", "marc_b@t.pe"):
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(email=email, full_name=email, hashed_password=security.hash_password("x"),
                     role="SASTRE-ASISTENTE", is_active=True)
            db.add(u); db.commit()
        users[email] = u
    o = db.query(Order).filter(Order.folio == "MARC-001").first()
    if not o:
        o = Order(folio="MARC-001", total=500, anticipo=0, estado="en_confeccion")
        db.add(o); db.commit(); db.refresh(o)
    g = db.query(Garment).filter(Garment.order_id == o.id).first()
    if not g:
        g = Garment(order_id=o.id, tipo="saco", precio=500, estado_taller="EN_CONFECCION")
        db.add(g); db.commit(); db.refresh(g)
    else:
        g.estado_taller = "EN_CONFECCION"; db.commit()
    op = db.query(CatalogoOperacion).filter(CatalogoOperacion.codigo == "MARC-OP").first()
    if not op:
        op = CatalogoOperacion(codigo="MARC-OP", nombre_operacion="Op marcada test", tarifa_base=Decimal("10"), activa=True)
        db.add(op); db.commit(); db.refresh(op)
    op2 = db.query(CatalogoOperacion).filter(CatalogoOperacion.codigo == "MARC-OP2").first()
    if not op2:
        op2 = CatalogoOperacion(codigo="MARC-OP2", nombre_operacion="Op marcada test 2", tarifa_base=Decimal("5"), activa=True)
        db.add(op2); db.commit(); db.refresh(op2)
    gid, opid, op2id = g.id, op.id, op2.id
    db.close()
    return gid, opid, op2id


def _login(email):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": email, "password": "x"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_sin_prenda_bloqueado():
    _setup()
    ca, cka = _login("marc_a@t.pe")
    r = ca.post("/rendimiento/registro", data={"op_1": "on", "cantidad_1": "1"}, cookies=cka)
    assert "sin_prenda" in r.headers["location"]


def test_memoria_marcadas():
    from app.core.database import SessionLocal
    from app.modules.rendimiento.models import DetalleJornada
    gid, opid, op2id = _setup()
    db = SessionLocal()
    db.query(DetalleJornada).filter(DetalleJornada.orden_produccion_id == gid).delete()
    db.commit(); db.close()

    ca, cka = _login("marc_a@t.pe")
    # 1) A marca op1 -> ok
    r = ca.post("/rendimiento/registro", data={
        "orden_produccion_id": str(gid), f"op_{opid}": "on", f"cantidad_{opid}": "1",
    }, cookies=cka)
    assert r.status_code == 303 and "ok=" in r.headers["location"], r.status_code

    # 2) A intenta de nuevo -> bloqueado, sin fila nueva
    r = ca.post("/rendimiento/registro", data={
        "orden_produccion_id": str(gid), f"op_{opid}": "on", f"cantidad_{opid}": "1",
    }, cookies=cka)
    assert "ya_marcada" in r.headers["location"]

    # 3) B intenta lo mismo -> bloqueado también
    cb, ckb = _login("marc_b@t.pe")
    r = cb.post("/rendimiento/registro", data={
        "orden_produccion_id": str(gid), f"op_{opid}": "on", f"cantidad_{opid}": "1",
    }, cookies=ckb)
    assert "ya_marcada" in r.headers["location"]

    db = SessionLocal()
    assert db.query(DetalleJornada).filter(
        DetalleJornada.orden_produccion_id == gid, DetalleJornada.operacion_id == opid).count() == 1

    # 4) otra actividad sí pasa
    r = cb.post("/rendimiento/registro", data={
        "orden_produccion_id": str(gid), f"op_{op2id}": "on", f"cantidad_{op2id}": "2",
    }, cookies=ckb)
    assert "ok=" in r.headers["location"], r.headers.get("location")

    # 5) endpoint memoria la expone con operario
    r = ca.get(f"/rendimiento/registro/marcadas?garment_id={gid}", cookies=cka)
    assert r.status_code == 200
    ops = {m["operacion_id"] for m in r.json()["marcadas"]}
    assert {opid, op2id} <= ops
    db.close()
