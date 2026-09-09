"""flag corporativo en clientes (B2B con aprobacion automatica a produccion)

Revision ID: c9d0e1f2a3b4
Revises: d7e8f9a0b1c2
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    if "es_corporativo" not in _cols(conn, "clients"):
        op.add_column("clients", sa.Column(
            "es_corporativo", sa.Boolean(), nullable=False,
            server_default=sa.text("false")))
        try:
            op.create_index("ix_clients_es_corporativo", "clients",
                            ["es_corporativo"], unique=False)
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    if "es_corporativo" in _cols(conn, "clients"):
        try:
            op.drop_index("ix_clients_es_corporativo", table_name="clients")
        except Exception:
            pass
        op.drop_column("clients", "es_corporativo")
