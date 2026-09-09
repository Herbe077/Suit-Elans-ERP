"""glosa en gastos registrados (descripción libre del formulario)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        cols = {c["name"] for c in inspect(op.get_bind()).get_columns("gastos_registrados")}
    except Exception:
        cols = set()
    if "glosa" not in cols:
        op.add_column("gastos_registrados",
                      sa.Column("glosa", sa.String(length=255), nullable=True))


def downgrade() -> None:
    try:
        op.drop_column("gastos_registrados", "glosa")
    except Exception:
        pass
