"""Módulo aislado Cierre de Jornada y Pagos por Taller."""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app


def _login_fresh(email: str, password: str = "x"):
    """Cliente HTTP propio por usuario (como navegadores distintos):
    evita que el jar de cookies del TestClient mezcle sesiones."""
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    if not u:
        role = "taller" if email.startswith("taller") else "ventas"
        name = "Operario Taller" if role == "taller" else "Ventas"
        u = User(email=email, full_name=name,
                 hashed_password=security.hash_password(password), role=role,
                 is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": email, "password": password},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _taller_user(client):
    c, ck = _login_fresh("taller@t.pe")
    return ck


def _taller_client():
    return _login_fresh("taller@t.pe")


def _ventas_user(client):
    c, ck = _login_fresh("ventas@t.pe")
    return ck


def test_catalogo_44(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.services import taller_cierre as svc
    db = SessionLocal()
    from app.models.taller import TallerTarea
    svc.seed_tareas(db)
    assert db.query(TallerTarea).count() == 44
    assert svc.seed_tareas(db) == 0  # segunda corrida idempotente
    _prenda(db)  # el checklist vive dentro de cada sección de prenda
    db.close()
    r = client.get("/taller/cierre-jornada", cookies=auth_cookies)
    assert r.status_code == 200 and "Planchado final a vapor" in r.text


def _prenda(db):
    """Crea pedido+prenda abierta y retorna (gid, ref)."""
    from app.models.order import Garment, Order
    from app.services import taller_cierre as svc
    o = Order(folio=f"SE-T-{Order.__table__.name}-{db.query(Order).count() + 1}",
              estado="confirmado", total=0)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco")
    db.add(g)
    db.commit()
    return g.id, svc.ref_producto(o.folio, g.tipo, g.id)


def test_cierre_y_total(client):
    ck = _taller_user(client)
    from app.core.database import SessionLocal
    from app.models.taller import TallerCierreDetalle, TallerCierreJornada, TallerTarea
    from app.services import taller_cierre as svc
    db = SessionLocal()
    svc.seed_tareas(db)
    gid, ref = _prenda(db)
    ids = [t.id for t in db.query(TallerTarea).limit(3).all()]
    esperado = float(sum((t.tarifa for t in db.query(TallerTarea).filter(
        TallerTarea.id.in_(ids)).all()), Decimal("0")))
    db.close()
    r = client.post("/taller/cierre-jornada",
                    data={f"prod_{gid}": "on", f"tasks_{gid}": [str(i) for i in ids]},
                    cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    c = db.query(TallerCierreJornada).order_by(TallerCierreJornada.id.desc()).first()
    assert c.producto_referencia == ref
    assert abs(float(c.total_pago) - esperado) < 0.01
    dets = db.query(TallerCierreDetalle).filter(TallerCierreDetalle.cierre_id == c.id).all()
    assert len(dets) == 3  # tarifa histórica congelada por detalle
    db.close()
    # Sin tareas → no se crea
    antes = db.query(TallerCierreJornada).count()
    r = client.post("/taller/cierre-jornada", data={f"prod_{gid}": "on"}, cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.query(TallerCierreJornada).count() == antes
    db.close()


def _uid(email: str) -> int:
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    uid = db.query(User).filter(User.email == email).first().id
    db.close()
    return uid


def test_bloqueo_entre_operarios(client, auth_cookies):
    """Lo cobrado hoy por A en una prenda no puede cobrarlo B (UI + servidor)."""
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.taller import TallerCierreDetalle, TallerCierreJornada, TallerTarea
    from app.models.user import User
    from app.services import taller_cierre as svc
    db = SessionLocal()
    svc.seed_tareas(db)
    gid, ref = _prenda(db)
    ids = [t.id for t in db.query(TallerTarea).limit(2).all()]
    db.close()
    # Clientes HTTP independientes por operario (sesiones aisladas)
    ca, ck_a = _login_fresh("taller@t.pe")
    cb_client, ck_b = _login_fresh("tallerb@t.pe")
    bid = _uid("tallerb@t.pe")
    r = ca.post("/taller/cierre-jornada",
                data={f"prod_{gid}": "on", f"tasks_{gid}": [str(i) for i in ids]},
                cookies=ck_a)
    assert r.status_code in (200, 303)
    # B ve la tarea de A bloqueada con su nombre
    r = cb_client.get("/taller/cierre-jornada", cookies=ck_b)
    assert r.status_code == 200 and "🔒 Operario Taller" in r.text
    # B intenta cobrar lo mismo + una tarea libre → solo se guarda la libre
    db = SessionLocal()
    libre = db.query(TallerTarea).filter(~TallerTarea.id.in_(ids)).first()
    db.close()
    r = cb_client.post("/taller/cierre-jornada",
                       data={f"prod_{gid}": "on",
                             f"tasks_{gid}": [str(ids[0]), str(libre.id)]}, cookies=ck_b)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    cb = db.query(TallerCierreJornada).filter(
        TallerCierreJornada.user_id == bid).order_by(
        TallerCierreJornada.id.desc()).first()
    dets = db.query(TallerCierreDetalle).filter(
        TallerCierreDetalle.cierre_id == cb.id).all()
    assert [d.tarea_id for d in dets] == [libre.id]  # la de A fue ignorada en servidor
    db.close()


def test_reporte_y_roles(client, auth_cookies):
    ck = _taller_user(client)
    # Operario ve su reporte (forzado a sí mismo)
    r = client.get("/taller/reporte-pagos?preset=semanal", cookies=ck)
    assert r.status_code == 200 and "saco #" in r.text
    # Admin ve todo + filtro empleado
    r = client.get("/taller/reporte-pagos?preset=mensual", cookies=auth_cookies)
    assert r.status_code == 200 and "Acumulado por trabajador" in r.text
    # Ventas tiene prohibido el módulo
    vk = _ventas_user(client)
    assert client.get("/taller/cierre-jornada", cookies=vk).status_code == 403
    assert client.get("/taller/reporte-pagos", cookies=vk).status_code == 403
    assert client.post("/taller/cierre-jornada", data={"producto_referencia": "X"},
                       cookies=vk).status_code == 403
