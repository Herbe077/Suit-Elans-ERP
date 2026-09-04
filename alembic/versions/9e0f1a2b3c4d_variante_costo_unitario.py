"""variante costo unitario (valorización PT Cta 23)

Revision ID: 9e0f1a2b3c4d
Revises: 8d9e0f1a2b3c
Create Date: 2026-09-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '9e0f1a2b3c4d'
down_revision: Union[str, None] = '8d9e0f1a2b3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    try:
        cols = {c["name"] for c in inspect(conn).get_columns("product_variants")}
    except Exception:
        cols = set()
    if "costo_unitario" not in cols:
        op.add_column("product_variants",
                      sa.Column("costo_unitario", sa.Float(), nullable=False,
                                server_default=sa.text("0")))


def downgrade() -> None:
    try:
        op.drop_column("product_variants", "costo_unitario")
    except Exception:
        pass
