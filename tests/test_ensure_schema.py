"""Regresión: BD longeva sin columnas de planilla ni tabla configuracion.

Simula el desfase prod (UndefinedColumn/UndefinedTable) y verifica que
ensure_runtime_schema restaura esquema + seeds, y que las vistas
responden 200 con guardado persistente.
"""
PLANILLA_COLS = ("sueldo_basico", "asignacion_familiar",
                 "sistema_pensiones", "regimen_laboral")


def _degradar():
    from sqlalchemy import inspect, text
    from app.core.database import engine
    engine.dispose()
    with engine.begin() as c:
        for col in PLANILLA_COLS:
            c.execute(text(f"ALTER TABLE empleados DROP COLUMN {col}"))
        c.execute(text("DROP TABLE configuracion"))
    engine.dispose()
    cols = {x["name"] for x in inspect(engine).get_columns("empleados")}
    assert all(c not in cols for c in PLANILLA_COLS)
    assert "configuracion" not in inspect(engine).get_table_names()


def test_ensure_restaura_personal_y_config():
    from sqlalchemy import inspect
    from app.core.database import engine
    from app.core.database import SessionLocal
    _degradar()
    db = SessionLocal()
    try:
        from app.services.finanzas import ensure_runtime_schema
        estado = ensure_runtime_schema(db)
        assert estado["personal"] is True and estado["config"] is True
        cols = {x["name"] for x in inspect(engine).get_columns("empleados")}
        assert all(c in cols for c in PLANILLA_COLS)
        assert "configuracion" in inspect(engine).get_table_names()
        from app.models.config import DEFAULTS, Configuracion
        assert db.query(Configuracion).count() >= len(DEFAULTS)
    finally:
        db.close()


def test_personal_y_configuracion_200_y_guardado_persiste(client,
                                                         auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        from app.services.finanzas import ensure_runtime_schema
        ensure_runtime_schema(db)
    finally:
        db.close()
    for url in ("/admin/personal", "/admin/configuracion"):
        r = client.get(url, cookies=auth_cookies)
        assert r.status_code == 200
        assert "Error interno" not in r.text
    r = client.post("/admin/configuracion",
                    data={"sede_nombre": "Sede QA", "direccion": "Av QA 1",
                          "igv_default": "18"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/admin/configuracion", cookies=auth_cookies)
    assert "Sede QA" in r.text
