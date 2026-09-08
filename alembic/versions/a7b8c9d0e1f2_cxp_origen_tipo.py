"""origen_tipo en cuentas por pagar (COMPRAS/GASTOS/DESTAJO)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        cols = {c["name"] for c in inspect(op.get_bind()).get_columns("cuentas_por_pagar")}
    except Exception:
        cols = set()
    if "origen_tipo" not in cols:
        op.add_column("cuentas_por_pagar",
                      sa.Column("origen_tipo", sa.String(length=20),
                                nullable=False, server_default="COMPRAS"))


def downgrade() -> None:
    try:
        op.drop_column("cuentas_por_pagar", "origen_tipo")
    except Exception:
        pass
