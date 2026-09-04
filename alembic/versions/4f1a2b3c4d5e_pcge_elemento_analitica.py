"""pcge elemento + es_analitica

Revision ID: 4f1a2b3c4d5e
Revises: 3d8d4e59c8ac
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '4f1a2b3c4d5e'
down_revision: Union[str, None] = '3d8d4e59c8ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    cols = _cols(conn, "cuentas_contables")
    if "elemento" not in cols:
        op.add_column("cuentas_contables", sa.Column("elemento", sa.Integer(), nullable=True))
        try:
            op.create_index(op.f("ix_cuentas_contables_elemento"), "cuentas_contables", ["elemento"], unique=False)
        except Exception:
            pass
    if "es_analitica" not in cols:
        op.add_column("cuentas_contables", sa.Column("es_analitica", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    conn = op.get_bind()
    cols = _cols(conn, "cuentas_contables")
    if "es_analitica" in cols:
        try:
            op.drop_column("cuentas_contables", "es_analitica")
        except Exception:
            pass
    if "elemento" in cols:
        try:
            op.drop_index(op.f("ix_cuentas_contables_elemento"), table_name="cuentas_contables")
        except Exception:
            pass
        try:
            op.drop_column("cuentas_contables", "elemento")
        except Exception:
            pass
