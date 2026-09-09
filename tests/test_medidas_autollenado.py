"""Autollenado de medidas: última toma prellena, guardar crea nueva versión, pedido usa versión elegida."""


def _cliente(client, auth_cookies, nombre="Auto"):
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = Client(nombre=nombre, apellidos="Test", tipo_doc="DNI", nro_doc="99990001")
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    return cid


def test_prefill_y_versionado(client, auth_cookies):
    cid = _cliente(client, auth_cookies)
    # Primera toma
    r = client.post(f"/produccion/fichas/{cid}/medidas",
                    data={"tipo_prenda": "saco", "pecho": "100", "cuello": "40"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    # La ficha prellena con la última toma
    r = client.get(f"/produccion/fichas/{cid}", cookies=auth_cookies)
    assert r.status_code == 200
    assert 'value="100' in r.text and "Autollenado v1" in r.text
    # Guardar modificado crea v2 (historial conservado)
    r = client.post(f"/produccion/fichas/{cid}/medidas",
                    data={"tipo_prenda": "saco", "pecho": "102", "cuello": "40"},
                    cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.measurement import Measurement
    db = SessionLocal()
    meds = db.query(Measurement).filter(Measurement.client_id == cid).order_by(
        Measurement.version).all()
    assert [m.version for m in meds] == [1, 2]
    assert meds[0].pecho == 100.0 and meds[1].pecho == 102.0
    db.close()
    r = client.get(f"/produccion/fichas/{cid}", cookies=auth_cookies)
    assert "Autollenado v2" in r.text and 'value="102' in r.text


def test_pedido_usa_medida_elegida(client, auth_cookies):
    """La ficha técnica muestra la última versión (autollenado vigente)."""
    from app.core.database import SessionLocal
    from app.models.measurement import Measurement
    from app.models.order import Garment, Order
    db = SessionLocal()
    c = db.query(__import__("app.models.client", fromlist=["Client"]).Client).filter_by(
        nro_doc="99990001").first()
    # Nueva toma v3 vía endpoint de medidas
    v3 = Measurement(client_id=c.id, tipo_prenda="saco", version=3, pecho=104.0)
    db.add(v3)
    db.flush()
    o = Order(folio="SE-AUTO-MED", client_id=c.id, estado="confirmado",
              total=900.0)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", precio=900.0)
    db.add(g)
    db.commit()
    gid = g.id
    db.close()
    r = client.get(f"/produccion/ficha/{gid}", cookies=auth_cookies)
    assert r.status_code == 200
    assert "v3" in r.text and "104" in r.text


def test_cita_desde_ficha(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.appointment import Appointment
    from app.models.client import Client
    db = SessionLocal()
    c = db.query(Client).filter_by(nro_doc="99990001").first()
    cid = c.id
    db.close()
    r = client.post(f"/produccion/fichas/{cid}/citas",
                    data={"tipo": "PRIMERA_PRUEBA", "inicio": "2026-11-10T10:00",
                          "fin": "2026-11-10T10:30"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    a = db.query(Appointment).filter(Appointment.client_id == cid).first()
    assert a and a.tipo == "PRIMERA_PRUEBA"
    db.close()
