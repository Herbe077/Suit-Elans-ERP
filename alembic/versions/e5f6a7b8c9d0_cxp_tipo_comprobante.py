"""tipo_comprobante en cuentas por pagar (RxH destajo a demanda)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        cols = {c["name"] for c in inspect(op.get_bind()).get_columns("cuentas_por_pagar")}
    except Exception:
        cols = set()
    if "tipo_comprobante" not in cols:
        op.add_column("cuentas_por_pagar",
                      sa.Column("tipo_comprobante", sa.String(length=20),
                                nullable=False, server_default="FACTURA"))


def downgrade() -> None:
    try:
        op.drop_column("cuentas_por_pagar", "tipo_comprobante")
    except Exception:
        pass
