"""tipo de comprobante (TOTAL/ANTICIPO/FINAL) para facturación por anticipos

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        cols = {c["name"] for c in inspect(op.get_bind()).get_columns("invoices")}
    except Exception:
        cols = set()
    if "tipo" not in cols:
        op.add_column("invoices",
                      sa.Column("tipo", sa.String(length=20), nullable=True,
                                server_default=sa.text("'TOTAL'")))


def downgrade() -> None:
    try:
        op.drop_column("invoices", "tipo")
    except Exception:
        pass
