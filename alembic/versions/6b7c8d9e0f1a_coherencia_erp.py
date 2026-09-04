"""coherencia ERP: igv compras + ref MOD canonica

Revision ID: 6b7c8d9e0f1a
Revises: 5a6b7c8d9e0f
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '6b7c8d9e0f1a'
down_revision: Union[str, None] = '5a6b7c8d9e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    conn = op.get_bind()
    po = _cols(conn, "purchase_orders")
    if "subtotal" not in po:
        op.add_column("purchase_orders", sa.Column("subtotal", sa.Float(), nullable=False, server_default=sa.text("0")))
    if "igv" not in po:
        op.add_column("purchase_orders", sa.Column("igv", sa.Float(), nullable=False, server_default=sa.text("0")))
    oc = _cols(conn, "ordenes_compra")
    if "subtotal" not in oc:
        op.add_column("ordenes_compra", sa.Column("subtotal", sa.Float(), nullable=False, server_default=sa.text("0")))
    if "igv" not in oc:
        op.add_column("ordenes_compra", sa.Column("igv", sa.Float(), nullable=False, server_default=sa.text("0")))
    det = _cols(conn, "rendimiento_detalles")
    if "orden_produccion_ref_id" not in det:
        op.add_column("rendimiento_detalles", sa.Column("orden_produccion_ref_id", sa.Integer(), nullable=True))
        try:
            op.create_foreign_key(None, "rendimiento_detalles", "orden_produccion",
                                  ["orden_produccion_ref_id"], ["id"])
        except Exception:
            pass
        try:
            op.create_index(op.f("ix_rendimiento_detalles_orden_produccion_ref_id"),
                            "rendimiento_detalles", ["orden_produccion_ref_id"], unique=False)
        except Exception:
            pass


def downgrade() -> None:
    try:
        op.drop_index(op.f("ix_rendimiento_detalles_orden_produccion_ref_id"), table_name="rendimiento_detalles")
    except Exception:
        pass
    try:
        op.drop_constraint(None, "rendimiento_detalles", type_="foreignkey")
    except Exception:
        pass
    for tbl, col in (("rendimiento_detalles", "orden_produccion_ref_id"),
                     ("ordenes_compra", "igv"), ("ordenes_compra", "subtotal"),
                     ("purchase_orders", "igv"), ("purchase_orders", "subtotal")):
        try:
            op.drop_column(tbl, col)
        except Exception:
            pass
