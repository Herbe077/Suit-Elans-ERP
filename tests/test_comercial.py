import os
"""Ámbito Comercial consolidado: 360°, calendario, cotizar, buscar e interacciones."""


def _ventas():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx",
                                    "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")}, follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_clientes_unificado_y_clasificacion():
    c, ck = _ventas()
    r = c.post("/comercial/clientes/persona",
               data={"nombre": "Rosa", "apellidos": "VIP", "tipo_doc": "DNI",
                     "nro_doc": "44556677", "clasificacion": "VIP"}, cookies=ck)
    assert r.status_code in (200, 303)
    r = c.get("/comercial/clientes?clasificacion=VIP", cookies=ck)
    assert r.status_code == 200 and "Rosa" in r.text
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    cli = db.query(Client).filter(Client.nro_doc == "44556677").first()
    assert cli.clasificacion == "VIP"
    # ficha 360 muestra compras, medidas, citas
    r = c.get(f"/comercial/clientes/detalle?tipo=persona&id={cli.id}", cookies=ck)
    assert r.status_code == 200 and "Historial de compras" in r.text
    db.close()


def test_buscar_json_y_rapido():
    c, ck = _ventas()
    r = c.get("/comercial/buscar?q=Ro", cookies=ck)
    assert r.status_code == 200 and any(x["nombre"].startswith("Rosa") for x in r.json())
    assert c.get("/comercial/buscar?q=X", cookies=ck).json() == []
    r = c.post("/comercial/clientes/persona-rapido",
               json={"nombre": "Flash", "telefono": "999000111"}, cookies=ck)
    assert r.status_code == 200 and r.json()["id"] > 0
    r = c.post("/comercial/clientes/persona-rapido", json={}, cookies=ck)
    assert r.status_code == 400


def test_citas_calendario_y_cotizar():
    c, ck = _ventas()
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    cli = db.query(Client).filter(Client.nro_doc == "44556677").first()
    cid = cli.id
    db.close()
    r = c.post("/comercial/citas",
               data={"client_id": str(cid), "tipo": "TOMA_MEDIDAS",
                     "inicio": "2026-12-01T10:00", "fin": "2026-12-01T10:30"},
               cookies=ck)
    assert r.status_code in (200, 303)
    for vista in ("lista", "dia", "semana", "mes"):
        r = c.get(f"/comercial/citas?vista={vista}&fecha=2026-12-01", cookies=ck)
        assert r.status_code == 200
    assert "Calendario 2026-12" in c.get("/comercial/citas?vista=mes&fecha=2026-12-01",
                                         cookies=ck).text
    from datetime import date as _d
    hoy = _d.today().isoformat()
    c.post("/comercial/citas",
           data={"client_id": str(cid), "tipo": "TOMA_MEDIDAS",
                 "inicio": f"{hoy}T09:00", "fin": f"{hoy}T09:30"}, cookies=ck)
    assert "pendientes de confirmación" in c.get("/comercial/citas", cookies=ck).text
    from app.models.appointment import Appointment
    db = SessionLocal()
    a = db.query(Appointment).filter(Appointment.client_id == cid).first()
    aid = a.id
    db.close()
    # convertir toma de medidas en cotización
    r = c.post(f"/comercial/citas/{aid}/cotizar", cookies=ck)
    assert r.status_code in (200, 303)
    from app.models.crm import Quotation
    db = SessionLocal()
    q = db.query(Quotation).filter(Quotation.client_id == cid).order_by(
        Quotation.id.desc()).first()
    assert q is not None
    assert db.get(Appointment, aid).estado == "CONFIRMADA"
    db.close()


def test_crm_kanban_interacciones_motivo():
    c, ck = _ventas()
    r = c.post("/comercial/crm/leads",
               data={"nombre": "Kanban Lead", "canal": "instagram"}, cookies=ck)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.crm import Interaccion, Lead
    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.nombre == "Kanban Lead").first()
    assert lead.estado == "PROSPECTO" and lead.canal == "instagram"
    lid = lead.id
    db.close()
    r = c.get("/comercial/crm", cookies=ck)
    assert "PROSPECTO" in r.text and "instagram" in r.text
    r = c.post(f"/comercial/crm/leads/{lid}/interaccion",
               data={"tipo": "whatsapp", "texto": "hola"}, cookies=ck)
    assert r.status_code in (200, 303)
    r = c.post(f"/comercial/crm/leads/{lid}/estado",
               data={"estado": "PERDIDO", "motivo_perdida": "precio alto"}, cookies=ck)
    db = SessionLocal()
    lead = db.get(Lead, lid)
    assert lead.estado == "PERDIDO" and lead.motivo_perdida == "precio alto"
    assert db.query(Interaccion).filter(Interaccion.lead_id == lid).count() == 1
    db.close()
    assert "precio alto" in c.get("/comercial/crm", cookies=ck).text
