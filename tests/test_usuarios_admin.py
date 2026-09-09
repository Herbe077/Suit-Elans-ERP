import os
"""Eliminar/desactivar usuarios: protecciones propio, último admin y vínculos."""


def _admin():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx",
                                    "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")}, follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _crear(email, role="ventas"):
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, full_name=email, role=role,
                 hashed_password=security.hash_password("x"), is_active=True)
        db.add(u)
        db.commit()
    uid = u.id
    db.close()
    return uid


def test_eliminar_usuario_limpio():
    from app.core.database import SessionLocal
    from app.models.user import User
    c, ck = _admin()
    uid = _crear("borrable@t.pe")
    r = c.get("/admin/usuarios", cookies=ck)
    assert "borrable@t.pe" in r.text and "Eliminar" in r.text
    r = c.post(f"/admin/usuarios/{uid}/eliminar", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(User, uid) is None
    db.close()


def test_no_autoborrado_ni_ultimo_admin():
    from app.core.database import SessionLocal
    from app.models.user import User
    c, ck = _admin()
    db = SessionLocal()
    me = db.query(User).filter(User.email == "admin@suitelans.mx").first()
    mid = me.id
    db.close()
    r = c.post(f"/admin/usuarios/{mid}/eliminar", cookies=ck)
    db = SessionLocal()
    assert db.get(User, mid) is not None  # sigo existiendo
    db.close()
    r = c.get("/admin/usuarios?error=propio", cookies=ck)
    assert "propio" in r.text
    # segundo admin sí permite borrar al... no: borrar al único admin activo
    uid = _crear("otroadm@t.pe", "admin")
    r = c.post(f"/admin/usuarios/{uid}/eliminar", cookies=ck)
    db = SessionLocal()
    assert db.get(User, uid) is None  # había otro admin, se puede
    db.close()


def test_usuario_con_vinculos_se_bloquea_y_desactiva():
    from app.core.database import SessionLocal
    from app.models.crm import Lead
    from app.models.user import User
    c, ck = _admin()
    uid = _crear("vinculado@t.pe", "ventas")
    db = SessionLocal()
    db.add(Lead(nombre="Lead V", vendedor_id=uid))
    db.commit()
    db.close()
    r = c.post(f"/admin/usuarios/{uid}/eliminar", cookies=ck)
    db = SessionLocal()
    assert db.get(User, uid) is not None
    db.close()
    r = c.get(f"/admin/usuarios?error=vinculos&uid={uid}", cookies=ck)
    assert "leads asignados" in r.text
    # pero sí se puede desactivar
    r = c.post(f"/admin/usuarios/{uid}/toggle", cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.get(User, uid).is_active is False
    db.close()
    # desactivado ya no puede entrar
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).post("/auth/login",
                             data={"username": "vinculado@t.pe", "password": "x"},
                             follow_redirects=False)
    assert r.status_code == 401
