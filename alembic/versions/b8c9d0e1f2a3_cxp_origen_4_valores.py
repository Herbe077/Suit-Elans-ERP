"""CxP: origen en 4 valores + actividad_flujo + observacion

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORIGEN_MAP = {"COMPRAS": "PROVEEDORES MATERIA PRIMA",
              "GASTOS": "GASTOS OPERATIVOS",
              "DESTAJO": "SERVICIOS TERCERIZADOS"}


def upgrade() -> None:
    bind = op.get_bind()
    try:
        cols = {c["name"] for c in inspect(bind).get_columns("cuentas_por_pagar")}
    except Exception:
        cols = set()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE cuentas_por_pagar "
            "ALTER COLUMN origen_tipo TYPE VARCHAR(40)"))
    if "actividad_flujo" not in cols:
        op.add_column("cuentas_por_pagar",
                      sa.Column("actividad_flujo", sa.String(length=20),
                                nullable=False, server_default="OPERATIVO"))
    if "observacion" not in cols:
        op.add_column("cuentas_por_pagar",
                      sa.Column("observacion", sa.String(length=255),
                                nullable=True))
    for viejo, nuevo in ORIGEN_MAP.items():
        try:
            op.execute(sa.text(
                "UPDATE cuentas_por_pagar SET origen_tipo = :nuevo "
                "WHERE origen_tipo = :viejo").bindparams(nuevo=nuevo,
                                                         viejo=viejo))
        except Exception:
            pass


def downgrade() -> None:
    try:
        op.drop_column("cuentas_por_pagar", "observacion")
    except Exception:
        pass
    try:
        op.drop_column("cuentas_por_pagar", "actividad_flujo")
    except Exception:
        pass
