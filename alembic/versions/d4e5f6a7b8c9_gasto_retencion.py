"""retencion en gastos registrados (IR 4ta RxH, espejo a CxP)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        cols = {c["name"] for c in inspect(op.get_bind()).get_columns("gastos_registrados")}
    except Exception:
        cols = set()
    if "retencion" not in cols:
        op.add_column("gastos_registrados",
                      sa.Column("retencion", sa.Numeric(10, 2),
                                nullable=False, server_default="0"))


def downgrade() -> None:
    try:
        op.drop_column("gastos_registrados", "retencion")
    except Exception:
        pass
