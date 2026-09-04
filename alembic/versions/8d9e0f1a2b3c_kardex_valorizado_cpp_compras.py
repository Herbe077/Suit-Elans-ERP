"""kardex valorizado + CPP + landed + estados OC canónicos

Revision ID: 8d9e0f1a2b3c
Revises: 7c8d9e0f1a2b
Create Date: 2026-09-04

- productos_insumo: costo_promedio, ultimo_costo
- movimientos_kardex: costo_unitario, costo_total, saldo_fisico, saldo_valorizado,
  orden_compra_id, detalle_oc_id, asiento_id, doc_ref
- ordenes_compra: numero_factura, fecha_factura, moneda, igv_rate,
  landed_flete/seguro/otros
- detalles_orden_compra: descuento_unitario, landed_unitario, costo_unitario_final
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '8d9e0f1a2b3c'
down_revision: Union[str, None] = '7c8d9e0f1a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(conn, table: str) -> set:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:
        return set()


def _add(conn, table: str, column: sa.Column):
    if column.name not in _cols(conn, table):
        op.add_column(table, column)


def upgrade() -> None:
    conn = op.get_bind()
    _add(conn, "productos_insumo", sa.Column("costo_promedio", sa.Float(), nullable=False, server_default=sa.text("0")))
    _add(conn, "productos_insumo", sa.Column("ultimo_costo", sa.Float(), nullable=False, server_default=sa.text("0")))
    for col in (
        sa.Column("costo_unitario", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("costo_total", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("saldo_fisico", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("saldo_valorizado", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("orden_compra_id", sa.Integer(), nullable=True),
        sa.Column("detalle_oc_id", sa.Integer(), nullable=True),
        sa.Column("asiento_id", sa.Integer(), nullable=True),
        sa.Column("doc_ref", sa.String(60), nullable=True),
    ):
        _add(conn, "movimientos_kardex", col)
    for col in (
        sa.Column("numero_factura", sa.String(40), nullable=True),
        sa.Column("fecha_factura", sa.Date(), nullable=True),
        sa.Column("moneda", sa.String(10), nullable=False, server_default=sa.text("'PEN'")),
        sa.Column("igv_rate", sa.Float(), nullable=False, server_default=sa.text("0.18")),
        sa.Column("landed_flete", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("landed_seguro", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("landed_otros", sa.Float(), nullable=False, server_default=sa.text("0")),
    ):
        _add(conn, "ordenes_compra", col)
    for col in (
        sa.Column("descuento_unitario", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("landed_unitario", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("costo_unitario_final", sa.Float(), nullable=False, server_default=sa.text("0")),
    ):
        _add(conn, "detalles_orden_compra", col)
    # Índices de trazabilidad (idempotentes)
    for name, table, cols in (
        ("ix_movkardex_oc", "movimientos_kardex", ["orden_compra_id"]),
        ("ix_movkardex_asiento", "movimientos_kardex", ["asiento_id"]),
        ("ix_movkardex_docref", "movimientos_kardex", ["doc_ref"]),
        ("ix_oc_factura", "ordenes_compra", ["numero_factura"]),
    ):
        try:
            op.create_index(name, table, cols, unique=False)
        except Exception:
            pass
    # Normaliza estados legacy a canónicos (solo valores conocidos, no toca datos raros)
    try:
        conn.execute(sa.text(
            "UPDATE ordenes_compra SET estado='DRAFT' WHERE estado IN ('BORRADOR','borrador')"))
        conn.execute(sa.text(
            "UPDATE ordenes_compra SET estado='APPROVED' WHERE estado IN ('ENVIADA','enviada')"))
        conn.execute(sa.text(
            "UPDATE ordenes_compra SET estado='PARTIALLY_RECEIVED' WHERE estado IN ('RECIBIDA_PARCIAL','recibida_parcial')"))
        conn.execute(sa.text(
            "UPDATE ordenes_compra SET estado='RECEIVED' WHERE estado IN ('RECIBIDA','recibida','COMPLETADA')"))
        conn.execute(sa.text(
            "UPDATE ordenes_compra SET estado='CANCELLED' WHERE estado IN ('CANCELADA','cancelada')"))
        conn.execute(sa.text(
            "UPDATE purchase_orders SET estado='borrador' WHERE estado IN ('BORRADOR')"))
        conn.execute(sa.text(
            "UPDATE purchase_orders SET estado='enviada' WHERE estado IN ('ENVIADA')"))
    except Exception:
        pass


def downgrade() -> None:
    for name in ("ix_oc_factura", "ix_movkardex_docref", "ix_movkardex_asiento", "ix_movkardex_oc"):
        try:
            op.drop_index(name)
        except Exception:
            pass
    for tbl, col in (
        ("detalles_orden_compra", "costo_unitario_final"),
        ("detalles_orden_compra", "landed_unitario"),
        ("detalles_orden_compra", "descuento_unitario"),
        ("ordenes_compra", "landed_otros"),
        ("ordenes_compra", "landed_seguro"),
        ("ordenes_compra", "landed_flete"),
        ("ordenes_compra", "igv_rate"),
        ("ordenes_compra", "moneda"),
        ("ordenes_compra", "fecha_factura"),
        ("ordenes_compra", "numero_factura"),
        ("movimientos_kardex", "doc_ref"),
        ("movimientos_kardex", "asiento_id"),
        ("movimientos_kardex", "detalle_oc_id"),
        ("movimientos_kardex", "orden_compra_id"),
        ("movimientos_kardex", "saldo_valorizado"),
        ("movimientos_kardex", "saldo_fisico"),
        ("movimientos_kardex", "costo_total"),
        ("movimientos_kardex", "costo_unitario"),
        ("productos_insumo", "ultimo_costo"),
        ("productos_insumo", "costo_promedio"),
    ):
        try:
            op.drop_column(tbl, col)
        except Exception:
            pass
