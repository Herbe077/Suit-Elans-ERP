"""Tesorería/Caja: apertura/cierre de turnos nunca 500 + nivelación de esquema."""
from app.models.billing import CajaTurno
from app.services import ventas as ventas_svc


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _uid():
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == "admin@suitelans.mx").first()
    uid = u.id
    db.close()
    return uid


def test_abrir_duplicado_no_500_y_cerrar_reabrir():
    db = _db()
    uid = _uid()
    for t in db.query(CajaTurno).filter(CajaTurno.usuario_id == uid,
                                        CajaTurno.estado == "ABIERTA").all():
        t.estado = "CERRADA"
    db.commit()
    t = ventas_svc.abrir_turno(db, uid, 100.0)
    assert t.estado == "ABIERTA" and t.saldo_apertura == 100.0
    try:
        ventas_svc.abrir_turno(db, uid, 10.0)
        assert False, "debió rechazar turno duplicado"
    except ValueError:
        pass
    ventas_svc.cerrar_turno(db, t.id, 100.0, uid)
    assert db.get(CajaTurno, t.id).estado == "CERRADA"
    t2 = ventas_svc.abrir_turno(db, uid, 0.0)
    assert t2.estado == "ABIERTA"
    db.close()


def test_endpoint_abrir_nunca_500(client, auth_cookies):
    r = client.post("/ventas/caja/abrir", data={"saldo_apertura": "25"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code in (303, 200)
    assert "Error interno del servidor" not in (r.text or "")
    r = client.post("/ventas/caja/abrir", data={"saldo_apertura": "25"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code in (303, 200)  # duplicado -> ?error=turno, no 500
    assert "Error interno del servidor" not in (r.text or "")


def test_ensure_nivela_columna_faltante():
    """Regresión: BD longeva sin clients.es_corporativo se nivela sin 500."""
    from sqlalchemy import inspect, text
    from app.core.database import engine
    db = _db()
    try:
        with engine.begin() as c:
            c.execute(text("DROP INDEX IF EXISTS ix_clients_es_corporativo"))
            c.execute(text("ALTER TABLE clients DROP COLUMN es_corporativo"))
        from app.services.finanzas import ensure_caja_columns
        ensure_caja_columns(db)
        db.commit()
        cols = {x["name"] for x in inspect(engine).get_columns("clients")}
        assert "es_corporativo" in cols
    finally:
        with engine.begin() as c:
            c.execute(text("CREATE INDEX IF NOT EXISTS "
                            "ix_clients_es_corporativo ON clients (es_corporativo)"))
        db.close()
