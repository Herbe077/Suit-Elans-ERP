"""CxP y Gastos toleran filas hostiles (nulos, destajo/RxH, proveedor huérfano).

Ambas pantallas deben responder 200 OK y mostrar fallbacks en vez de 500.
"""
from app.core.database import Base, SessionLocal, engine
from app.services.plan_operativo import cargar_plan_operativo
from datetime import date


def _hostiles(db):
    from sqlalchemy import text as _text
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    from app.models.purchasing import Supplier
    from app.services import finanzas as f
    cargar_plan_operativo(db)
    sup = Supplier(nombre="Personal Destajo / Sastre", ruc="87654321")
    db.add(sup)
    db.flush()
    db.add(CuentaPorPagar(proveedor_id=sup.id, numero_factura="RXH-HOSTIL-1",
                          monto_total=1000.0, monto_pagado=0.0,
                          saldo_pendiente=920.0, retencion=80.0,
                          fecha_emision=date.today(), estado="POR_PAGAR"))
    db.add(GastoRegistrado(proveedor_id=None, ruc_proveedor=None,
                           tipo_comprobante=None, numero_comprobante=None,
                           categoria="HONORARIOS_RXH", glosa=None,
                           monto_base=800.0, monto_igv=0.0, monto_total=800.0,
                           retencion=0.0, clasificacion="GASTO_ADMINISTRATIVO",
                           centro_costo_id=None, cuenta_id=None,
                           fecha_emision=date.today(), estado="PENDIENTE"))
    db.commit()
    # proveedor huérfano (destajo sin contraparte directa)
    db.execute(_text("UPDATE cuentas_por_pagar SET proveedor_id=999999 "
                     "WHERE numero_factura='RXH-HOSTIL-1'"))
    db.commit()


def test_ensure_retencion_usa_ddl_postgres():
    """En Neon/PG el helper debe usar ADD COLUMN IF NOT EXISTS (sin PRAGMA)."""
    from unittest.mock import MagicMock, patch
    from app.services import finanzas as f
    fake_bind = MagicMock()
    fake_bind.dialect.name = "postgresql"
    fake_db = MagicMock()
    fake_db.get_bind.return_value = fake_bind
    fake_inspect = MagicMock()
    fake_inspect.return_value.get_columns.return_value = [{"name": "id"}]
    with patch("sqlalchemy.inspect", fake_inspect):
        f.ensure_gasto_retencion_column(fake_db)
    ddl = str(fake_db.execute.call_args[0][0])
    assert "IF NOT EXISTS" in ddl and "retencion" in ddl
    assert "PRAGMA" not in ddl


def test_ensure_retencion_idempotente_si_columna_existe():
    """El DDL se emite a ciegas (idempotente): el inspect puede servir
    caché stale desde el pool, así que 'columna existe' no exime el ADD.
    En SQLite, 'duplicate column' se traga con rollback y la sesión
    queda usable."""
    from unittest.mock import MagicMock
    from sqlalchemy.exc import OperationalError
    from app.services import finanzas as f
    fake_bind = MagicMock()
    fake_bind.dialect.name = "sqlite"
    fake_db = MagicMock()
    fake_db.get_bind.return_value = fake_bind
    fake_db.execute.side_effect = OperationalError(
        "ALTER TABLE", None, Exception("duplicate column name: retencion"))
    f.ensure_gasto_retencion_column(fake_db)  # no lanza
    fake_db.execute.assert_called_once()
    fake_db.rollback.assert_called()


def test_cxp_200_con_huerfanos_y_rxh(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _hostiles(db)
    db.close()
    r = client.get("/finanzas/cuentas-por-pagar", cookies=auth_cookies)
    assert r.status_code == 200
    assert "Personal Destajo / Sastre" in r.text
    assert "RXH-HOSTIL-1" in r.text


def test_gastos_200_con_nulos_y_rxh(client, auth_cookies):
    from app.core.database import SessionLocal
    db = SessionLocal()
    _hostiles(db)
    db.close()
    r = client.get("/finanzas/gastos", cookies=auth_cookies)
    assert r.status_code == 200
    assert "FACTURA" in r.text  # fallback de comprobante nulo
    # limpieza de filas hostiles
    from sqlalchemy import text as _text
    db = SessionLocal()
    db.execute(_text("DELETE FROM cuentas_por_pagar "
                     "WHERE numero_factura='RXH-HOSTIL-1'"))
    db.execute(_text("DELETE FROM gastos_registrados "
                     "WHERE categoria='HONORARIOS_RXH' AND numero_comprobante IS NULL"))
    db.execute(_text("DELETE FROM suppliers WHERE nombre='Personal Destajo / Sastre'"))
    db.commit()
    db.close()
