"""UX del tareo diario: buscador, toggle, grupos, chips y panel lateral."""
from datetime import date
from decimal import Decimal


def _client_admin():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login",
               data={"username": "admin@suitelans.mx", "password": "admin123"})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def test_tareo_ux_buscador_toggle_grupos():
    c, ck = _client_admin()
    t = c.get("/rendimiento/registro?todas=1", cookies=ck).text
    assert "Buscar operación (ej: OP-09, mangas)..." in t
    assert "Solo disponibles" in t and "Ver todas (" in t
    for grupo in ("Corte y Habilitación", "Confección y Ensamble",
                  "Fusionado y Planchado", "Acabados y Ojales"):
        assert grupo in t
    assert "tareo-card" in t and "op_" in t
    assert "Confirmar y guardado del tareo diario" in t


def test_tareo_panel_mis_registros_hoy():
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    c, ck = _client_admin()
    db = SessionLocal()
    admin = db.query(User).filter(User.email == "admin@suitelans.mx").first()
    cat = db.query(CatalogoOperacion).first()
    if not cat:
        cat = CatalogoOperacion(codigo="UX-OP", nombre_operacion="Op UX",
                                tarifa_base=Decimal("10"), activa=True)
        db.add(cat)
        db.flush()
    reg = RegistroJornada(operario_id=admin.id, fecha=date.today(),
                          estado="PENDIENTE")
    db.add(reg)
    db.flush()
    db.add(DetalleJornada(registro_jornada_id=reg.id, operacion_id=cat.id,
                          cantidad=2, tarifa_aplicada=Decimal("10"),
                          subtotal=Decimal("20")))
    db.commit()
    rid = reg.id
    db.close()
    t = c.get("/rendimiento/registro?todas=1", cookies=ck).text
    assert "Mis registros hoy" in t
    assert "S/ 20.00" in t
    assert "Ver mi destajo en Calculadora" in t
    db = SessionLocal()
    db.query(DetalleJornada).filter(
        DetalleJornada.registro_jornada_id == rid).delete()
    db.query(RegistroJornada).filter(RegistroJornada.id == rid).delete()
    db.commit()
    db.close()
