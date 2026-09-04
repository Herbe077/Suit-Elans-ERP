"""Producción 7 fases: kanban, automatizaciones, ficha, pruebas 1-3 y calidad."""
from datetime import date, timedelta


def _taller_session():
    from fastapi.testclient import TestClient
    from app.core import security
    from app.core.database import SessionLocal
    from app.main import app
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == "prod_taller@t.pe").first()
    if not u:
        u = User(email="prod_taller@t.pe", full_name="Taller Prod",
                 hashed_password=security.hash_password("x"), role="taller",
                 is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "prod_taller@t.pe", "password": "x"},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _prenda_con_cliente():
    from app.core.database import SessionLocal
    from app.models.client import Client
    from app.models.measurement import Measurement
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = Client(nombre="Fit", apellidos="Test", tipo_doc="DNI", nro_doc="55443322")
    db.add(c)
    db.flush()
    m = Measurement(client_id=c.id, tipo_prenda="saco", pecho=102.0, postura="Postura erguida",
                    observaciones="Hombro derecho caído 1.5 cm")
    db.add(m)
    db.flush()
    o = Order(folio=f"SE-FIT-{db.query(Order).count() + 1}", client_id=c.id,
              estado="confirmado", total=1000,
              fecha_entrega=date.today() + timedelta(days=5))
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", measurement_id=m.id, precio=1000)
    db.add(g)
    db.flush()
    from app.services.taller import codigo_qr
    g.codigo_qr = codigo_qr(o.folio, g.id)
    db.commit()
    gid = g.id
    db.close()
    return gid


def test_kanban_7_fases():
    c, ck = _taller_session()
    gid = _prenda_con_cliente()
    r = c.get("/produccion/kanban", cookies=ck)
    assert r.status_code == 200
    for label in ["Por Cortar", "En Corte", "Hilván", "En Prueba", "En Confección",
                  "Acabados", "Listo para Entrega"]:
        assert label in r.text, label
    assert "5d" in r.text or "4d" in r.text
    assert 'id="board"' in r.text
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "EN_CORTE"},
                cookies=ck)
    assert r.status_code == 200 and "Ficha técnica" in r.text
    assert r.text.count(f'id="card-{gid}"') == 1
    from app.core.database import SessionLocal
    from app.models.order import Garment
    db = SessionLocal()
    assert db.get(Garment, gid).estado_taller == "EN_CORTE"
    db.close()
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "narnia"}, cookies=ck)
    assert r.status_code == 400


def test_automatizaciones_flujo():
    c, ck = _taller_session()
    gid = _prenda_con_cliente()
    from app.core.database import SessionLocal
    from app.models.appointment import Appointment
    from app.models.crm import Interaccion
    from app.models.order import Garment
    # A EN_PRUEBA auto-agenda cita comercial
    c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "EN_PRUEBA"}, cookies=ck)
    db = SessionLocal()
    g = db.get(Garment, gid)
    a = db.query(Appointment).filter(Appointment.garment_id == gid).first()
    assert a and a.tipo in ("PRIMERA_PRUEBA", "SEGUNDA_PRUEBA")
    # A EN_CONFECCION sin notas → bloqueado
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "EN_CONFECCION"},
                cookies=ck)
    assert r.status_code == 400
    # Con notas registradas → habilitado + flag
    c.post("/produccion/prueba-entalle",
           data={"garment_id": str(gid), "numero_prueba": "1",
                 "zona_hombros": "bajar 0.5 cm"}, cookies=ck)
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "EN_CONFECCION"},
                cookies=ck)
    assert r.status_code == 200
    db = SessionLocal()
    assert db.get(Garment, gid).paso_confeccion is True
    # A ACABADOS directo sin pasar por confección → bloqueado (otra prenda)
    gid2 = _prenda_con_cliente()
    db = SessionLocal()
    db.get(Garment, gid2).estado_taller = "EN_PRUEBA"
    db.commit()
    db.close()
    r = c.patch(f"/produccion/orden/{gid2}/estado", data={"estado": "ACABADOS"},
                cookies=ck)
    assert r.status_code == 400
    # CALIDAD_OK directo → obligado por checklist
    r = c.patch(f"/produccion/orden/{gid}/estado", data={"estado": "CALIDAD_OK"},
                cookies=ck)
    assert r.status_code == 400
    db.close()


def test_ficha_qr_limite_diseno():
    c, ck = _taller_session()
    gid = _prenda_con_cliente()
    r = c.get(f"/produccion/ficha/{gid}", cookies=ck)
    assert r.status_code == 200
    for txt in ["102", "Postura erguida", "Hombro derecho caído", "Solapa",
                "Responsable", "QR SE-FIT"]:
        assert txt in r.text, txt
    r = c.post(f"/produccion/prenda/{gid}/diseno",
               data={"solapa": "muesca", "bolsillos": "rectos con solapa",
                     "forro": "completo", "respiraderos": "abertura doble",
                     "botones": "2", "notas_diseno": "ojal lapela",
                     "fecha_limite": "2026-12-20"}, cookies=ck)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.order import Garment
    from app.models.user import User
    db = SessionLocal()
    g = db.get(Garment, gid)
    assert g.diseno["solapa"] == "muesca"
    assert str(g.fecha_limite_entrega) == "2026-12-20"
    op = db.query(User).filter(User.email == "prod_taller@t.pe").first()
    db.close()
    r = c.post(f"/produccion/prenda/{gid}/asignar", data={"artesano_id": str(op.id)},
               cookies=ck)
    db = SessionLocal()
    assert db.get(Garment, gid).artesano_id == op.id
    db.close()


def test_fit_test_3_y_fotos():
    c, ck = _taller_session()
    gid = _prenda_con_cliente()
    from app.core.database import SessionLocal
    from app.models.order import Garment
    db = SessionLocal()
    db.get(Garment, gid).estado_taller = "EN_PRUEBA"
    db.commit()
    db.close()
    r = c.get("/produccion/pruebas", cookies=ck)
    assert r.status_code == 200 and "3ra prueba" in r.text
    r = c.post("/produccion/prueba-entalle",
               data={"garment_id": str(gid), "numero_prueba": "3",
                     "notas_sastre": "verificar caída"}, cookies=ck)
    assert r.status_code in (200, 303)
    from app.models.order import PruebaEntalle
    db = SessionLocal()
    p = db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == gid).first()
    assert p and p.numero_prueba == 3 and p.notas_sastre == "verificar caída"
    # con notas habilita confección
    assert db.get(Garment, gid).estado_taller == "EN_PRUEBA"
    db.close()


def test_calidad_avisa_ventas():
    c, ck = _taller_session()
    gid = _prenda_con_cliente()
    from app.core.database import SessionLocal
    from app.models.order import Garment
    db = SessionLocal()
    g = db.get(Garment, gid)
    g.estado_taller = "ACABADOS"
    g.paso_confeccion = True
    db.commit()
    db.close()
    r = c.get("/produccion/calidad", cookies=ck)
    assert r.status_code == 200 and "6 puntos" in r.text
    r = c.post(f"/produccion/orden/{gid}/control-calidad",
               data={"check_0": "on", "check_1": "on"}, cookies=ck)
    db = SessionLocal()
    assert db.get(Garment, gid).estado_taller == "ACABADOS"
    db.close()
    r = c.post(f"/produccion/orden/{gid}/control-calidad",
               data={f"check_{i}": "on" for i in range(6)}, cookies=ck)
    assert r.status_code in (200, 303)
    from app.models.crm import Interaccion
    from app.models.order import ControlCalidad
    db = SessionLocal()
    assert db.get(Garment, gid).estado_taller == "CALIDAD_OK"
    cc = db.query(ControlCalidad).filter(ControlCalidad.garment_id == gid).first()
    assert cc and cc.aprobado is True and len(cc.checks) == 6
    aviso = db.query(Interaccion).filter(Interaccion.texto.like("%CALIDAD_OK%")).first()
    assert aviso is not None  # ventas avisada para liquidar
    db.close()
