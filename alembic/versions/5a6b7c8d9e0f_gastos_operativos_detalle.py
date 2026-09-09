"""gastos operativos detalle (base/igv/ruc/categoria)

Revision ID: 5a6b7c8d9e0f
Revises: 4f1a2b3c4d5e
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '5a6b7c8d9e0f'
down_revision: Union[str, None] = '4f1a2b3c4d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


_COLS = [
    ("ruc_proveedor", sa.Column("ruc_proveedor", sa.String(length=20), nullable=True)),
    ("tipo_comprobante", sa.Column("tipo_comprobante", sa.String(length=20), nullable=True)),
    ("categoria", sa.Column("categoria", sa.String(length=30), nullable=True)),
    ("monto_base", sa.Column("monto_base", sa.Float(), nullable=False, server_default=sa.text("0"))),
    ("monto_igv", sa.Column("monto_igv", sa.Float(), nullable=False, server_default=sa.text("0"))),
]


def upgrade() -> None:
    conn = op.get_bind()
    cols = _cols(conn, "gastos_registrados")
    for name, col in _COLS:
        if name not in cols:
            op.add_column("gastos_registrados", col)
    if "categoria" in _cols(conn, "gastos_registrados"):
        try:
            op.create_index(op.f("ix_gastos_registrados_categoria"), "gastos_registrados", ["categoria"], unique=False)
        except Exception:
            pass


def downgrade() -> None:
    for name, _col in reversed(_COLS):
        try:
            op.drop_column("gastos_registrados", name)
        except Exception:
            pass
    try:
        op.drop_index(op.f("ix_gastos_registrados_categoria"), table_name="gastos_registrados")
    except Exception:
        pass
