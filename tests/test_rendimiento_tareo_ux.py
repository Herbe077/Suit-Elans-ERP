import os
"""UX del tareo diario: buscador, toggle, grupos, chips y panel lateral."""
from datetime import date
from decimal import Decimal


def _client_admin():
    from app.main import app
    from fastapi.testclient import TestClient
    c = TestClient(app, follow_redirects=False)
    r = c.post("/auth/login",
               data={"username": "admin@suitelans.mx", "password": os.environ.get("TEST_ADMIN_PASSWORD", "test-admin-password")})
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


ETAPAS_ESPERADAS = [
    "Etapa 1 - Corte y Habilitación Base",
    "Etapa 2 - Pre-costuras y Estructura",
    "Etapa 3 - Fusionado y Adhesivos",
    "Etapa 4 - Armado de Delantero y Refuerzos",
    "Etapa 5 - Ensamble de Contrapechos e Internos",
    "Etapa 6 - Planchado de Montaje e Internos",
    "Etapa 7 - Hilvanado y Uniones Principales",
    "Etapa 8 - Costuras de Refuerzo y Bastas",
    "Etapa 9 - Sisa y Montaje de Mangas",
    "Etapa 10 - Ojales, Limpieza y Acabado Final",
]


def test_tareo_ux_buscador_etapas_continuas():
    c, ck = _client_admin()
    t = c.get("/rendimiento/registro?todas=1", cookies=ck).text
    assert "Buscar operación (ej: OP-09, mangas)..." in t
    # cada cabecera exactamente una vez y en orden 1..10
    pos = [t.index(e) for e in ETAPAS_ESPERADAS]
    assert pos == sorted(pos)
    for e in ETAPAS_ESPERADAS:
        assert t.count(e) == 1, e
    # lista continua: sin acordeones que oculten la secuencia
    assert "<details" not in t
    assert "tareo-card" in t and "op_" in t
    assert "Confirmar y guardado del tareo diario" in t


def test_tareo_secuencia_01_44_y_badges():
    import re
    from app.core.database import SessionLocal
    from app.modules.rendimiento.models import CatalogoOperacion
    c, ck = _client_admin()
    t = c.get("/rendimiento/registro?todas=1", cookies=ck).text
    # secuencia correlativa desde 01, sin saltos ni repetidos
    seq = [int(x) for x in re.findall(r'>(\d{2})</span>\s*<span[^>]*>(?:OP-|CAT-)', t)]
    db = SessionLocal()
    n_cat = db.query(CatalogoOperacion).filter(
        CatalogoOperacion.activa.is_(True)).count()
    db.close()
    assert len(seq) == n_cat and seq == list(range(1, n_cat + 1)), seq
    # las 44 base están presentes del 01 al 44
    assert list(range(1, 45)) == [x for x in seq if x <= 44]
    # badges E1..E10 presentes y alineados a su etapa
    for n in range(1, 11):
        assert f"E{n}" in t
    # E7 solo dentro del bloque de la Etapa 7 (entre su cabecera y la Etapa 8)
    bloque7 = t.split("Etapa 7 - Hilvanado")[1].split("Etapa 8 -")[0]
    assert "E7" in bloque7 and "E6" not in bloque7 and "E8" not in bloque7


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
