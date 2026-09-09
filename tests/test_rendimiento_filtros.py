import os
"""Calculadora destajo: presets hoy/semana/mes y fechas mandan sobre preset."""
import re
from datetime import date


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login", data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _rango(html):
    m = re.search(r"Rango: (\S+) → (\S+)", html)
    assert m, "sin rango visible"
    return m.group(1), m.group(2).split("<")[0]


def test_preset_hoy():
    c, ck = _client()
    hoy = date.today().isoformat()
    ini, fin = _rango(c.get("/rendimiento/calculadora?preset=hoy", cookies=ck).text)
    assert (ini, fin) == (hoy, hoy)


def test_fechas_mandan_sobre_preset():
    c, ck = _client()
    ini, fin = _rango(c.get("/rendimiento/calculadora?preset=semana&desde=2026-01-05&hasta=2026-01-20", cookies=ck).text)
    assert (ini, fin) == ("2026-01-05", "2026-01-20")


def test_personalizado_filtra_datos():
    from decimal import Decimal
    from app.core.database import Base, SessionLocal, engine
    from app.modules.rendimiento.models import CatalogoOperacion, DetalleJornada, RegistroJornada
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    cat = db.query(CatalogoOperacion).first()
    if not cat:
        cat = CatalogoOperacion(codigo="T-FILT", nombre_operacion="Op filtro", tarifa_base=Decimal("7"), activa=True)
        db.add(cat); db.commit(); db.refresh(cat)
    reg = RegistroJornada(operario_id=1, fecha=date(2026, 5, 10), estado="REGISTRADO", observaciones="FILT-TEST")
    db.add(reg); db.flush()
    db.add(DetalleJornada(registro_jornada_id=reg.id, operacion_id=cat.id, cantidad=1, tarifa_aplicada=Decimal("7"), subtotal=Decimal("7")))
    db.commit()
    c, ck = _client()
    # rango que incluye mayo 2026 trae el registro; enero 2000 no
    html_all = c.get("/rendimiento/calculadora?preset=personalizado&desde=2026-05-01&hasta=2026-05-31", cookies=ck).text
    html_none = c.get("/rendimiento/calculadora?preset=personalizado&desde=2000-01-01&hasta=2000-01-31", cookies=ck).text
    assert "FILT-TEST" in html_all
    assert "Sin registros en rango" in html_none
    db.query(DetalleJornada).filter(DetalleJornada.registro_jornada_id == reg.id).delete()
    db.query(RegistroJornada).filter(RegistroJornada.id == reg.id).delete()
    db.commit(); db.close()
