import os
"""Eliminar ficha de cliente: solo admin, solo sin movimientos."""


def _login(client, email, password="x"):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": email, "password": password},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _admin():
    from app.core.database import SessionLocal
    from app.models.user import User
    from fastapi.testclient import TestClient
    from app.main import app
    db = SessionLocal()
    assert db.query(User).filter(User.email == "admin@suitelans.mx").first()
    db.close()
    return _login(TestClient(app), "admin@suitelans.mx", os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password"))


def test_eliminar_ficha_limpia():
    from app.core.database import SessionLocal
    from app.models.appointment import Appointment
    from app.models.client import Client
    from app.models.measurement import Measurement
    c, ck = _admin()
    db = SessionLocal()
    cli = Client(nombre="Borrable", apellidos="Test")
    db.add(cli)
    db.flush()
    db.add(Measurement(client_id=cli.id, tipo_prenda="saco", pecho=100.0))
    db.commit()
    cid = cli.id
    db.close()
    # Botón visible para admin
    r = c.get(f"/produccion/fichas/{cid}", cookies=ck)
    assert r.status_code == 200 and "Eliminar ficha" in r.text
    r = c.post(f"/produccion/fichas/{cid}/eliminar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Client, cid) is None
    assert db.query(Measurement).filter(Measurement.client_id == cid).count() == 0
    assert db.query(Appointment).filter(Appointment.client_id == cid).count() == 0
    db.close()


def test_eliminar_bloqueada_con_pedido():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order
    c, ck = _admin()
    db = SessionLocal()
    cli = Client(nombre="NoBorrable", apellidos="Test")
    db.add(cli)
    db.flush()
    db.add(Order(folio=f"SE-NB-{cli.id}", client_id=cli.id, estado="confirmado", total=10))
    db.commit()
    cid = cli.id
    db.close()
    r = c.post(f"/produccion/fichas/{cid}/eliminar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Client, cid) is not None  # intacto
    db.close()
    r = c.get(f"/produccion/fichas/{cid}?error=movimientos", cookies=ck)
    assert "No se puede eliminar" in r.text
    # El mensaje detalla qué bloquea
    assert "SE-NB-" in r.text


def test_anular_y_luego_eliminar():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Garment, Order
    c, ck = _admin()
    db = SessionLocal()
    cli = Client(nombre="Anulable", apellidos="Test")
    db.add(cli)
    db.flush()
    o = Order(folio=f"SE-AN-{cli.id}", client_id=cli.id, estado="confirmado", total=100)
    db.add(o)
    db.flush()
    db.add(Garment(order_id=o.id, tipo="saco", precio=100))
    db.commit()
    cid, oid = cli.id, o.id
    db.close()
    # Anular libera la ficha...
    r = c.post(f"/produccion/fichas/{cid}/pedidos/{oid}/cancelar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Order, oid).estado == "cancelado"
    db.close()
    # ...y ahora sí se puede purgar (incluye la prenda del pedido anulado)
    r = c.post(f"/produccion/fichas/{cid}/eliminar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Client, cid) is None
    assert db.get(Order, oid) is None
    assert db.query(Garment).filter(Garment.order_id == oid).count() == 0
    db.close()


def test_pedido_con_pagos_no_se_anula_ni_borra():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.order import Order, Payment
    c, ck = _admin()
    db = SessionLocal()
    cli = Client(nombre="ConPagos", apellidos="Test")
    db.add(cli)
    db.flush()
    o = Order(folio=f"SE-CP-{cli.id}", client_id=cli.id, estado="confirmado",
              total=500, anticipo=100)
    db.add(o)
    db.flush()
    db.add(Payment(order_id=o.id, monto=100, metodo="efectivo"))
    db.commit()
    cid, oid = cli.id, o.id
    db.close()
    r = c.post(f"/produccion/fichas/{cid}/pedidos/{oid}/cancelar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(Order, oid).estado == "confirmado"  # no se anuló
    db.close()
    r = c.post(f"/produccion/fichas/{cid}/eliminar", cookies=ck)
    db = SessionLocal()
    assert db.get(Client, cid) is not None  # intacto
    db.close()


def test_eliminar_solo_admin(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    # auth_cookies es admin en conftest... usar rol ventas explícito
    from fastapi.testclient import TestClient
    from app.core import security
    from app.main import app
    from app.models.user import User
    db = SessionLocal()
    if not db.query(User).filter(User.email == "adel@t.pe").first():
        db.add(User(email="adel@t.pe", full_name="Adel", role="sastre",
                    hashed_password=security.hash_password("x"), is_active=True))
        db.commit()
    db.close()
    vc, vck = _login(client, "adel@t.pe")
    # sastre no ve el botón
    db = SessionLocal()
    cli = Client(nombre="Vista", apellidos="Boton")
    db.add(cli)
    db.commit()
    cid = cli.id
    db.close()
    r = vc.get(f"/produccion/fichas/{cid}", cookies=vck)
    assert "Eliminar ficha" not in r.text
    # y el endpoint lo rechaza
    r = vc.post(f"/produccion/fichas/{cid}/eliminar", cookies=vck)
    assert r.status_code == 403
    from app.core.database import SessionLocal as S2
    db = S2()
    assert db.get(Client, cid) is not None
    db.close()
