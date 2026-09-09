"""kardex: taxonomía canónica + producto_id nullable (PT sin insumo)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1

- movimientos_kardex.producto_id: NOT NULL -> NULL (ENTRADA_PRODUCTO_TERMINADO
  de Kanban no referencia insumo).
- Remap idempotente de tipos legacy a canónicos:
  INGRESO_COMPRA/ENTRADA->ENTRADA_COMPRA, INGRESO/AJUSTE_INVENTARIO/AJUSTE->
  ENTRADA_AJUSTE, DEVOLUCION->ENTRADA_DEVOLUCION, SALIDA_TALLER/SALIDA->
  SALIDA_CONSUMO_TALLER, AJUSTE_MERMA/MERMA->SALIDA_MERMA.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REMAP = {
    "INGRESO_COMPRA": "ENTRADA_COMPRA",
    "ENTRADA": "ENTRADA_COMPRA",
    "INGRESO": "ENTRADA_AJUSTE",
    "AJUSTE_INVENTARIO": "ENTRADA_AJUSTE",
    "AJUSTE": "ENTRADA_AJUSTE",
    "DEVOLUCION": "ENTRADA_DEVOLUCION",
    "SALIDA_TALLER": "SALIDA_CONSUMO_TALLER",
    "SALIDA": "SALIDA_CONSUMO_TALLER",
    "AJUSTE_MERMA": "SALIDA_MERMA",
    "MERMA": "SALIDA_MERMA",
}


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"]: c for c in sa.inspect(conn).get_columns("movimientos_kardex")}
    if "producto_id" in cols and not cols["producto_id"].get("nullable", True):
        with op.batch_alter_table("movimientos_kardex") as batch:
            batch.alter_column("producto_id", existing_type=sa.Integer(),
                               nullable=True,
                               existing_nullable=False)
    for viejo, nuevo in REMAP.items():
        conn.execute(sa.text(
            "UPDATE movimientos_kardex SET tipo_movimiento=:nuevo "
            "WHERE tipo_movimiento=:viejo"),
            {"nuevo": nuevo, "viejo": viejo})


def downgrade() -> None:
    conn = op.get_bind()
    inverso = {}
    for viejo, nuevo in REMAP.items():
        inverso.setdefault(nuevo, viejo)
    for nuevo, viejo in inverso.items():
        conn.execute(sa.text(
            "UPDATE movimientos_kardex SET tipo_movimiento=:viejo "
            "WHERE tipo_movimiento=:nuevo"),
            {"viejo": viejo, "nuevo": nuevo})
    with op.batch_alter_table("movimientos_kardex") as batch:
        try:
            batch.alter_column("producto_id", existing_type=sa.Integer(),
                               nullable=False, existing_nullable=True)
        except Exception:
            pass
