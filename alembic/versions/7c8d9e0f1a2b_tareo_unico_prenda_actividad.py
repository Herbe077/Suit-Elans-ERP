"""tareo unico por prenda+actividad (memoria de marcados)

Revision ID: 7c8d9e0f1a2b
Revises: 6b7c8d9e0f1a
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7c8d9e0f1a2b'
down_revision: Union[str, None] = '6b7c8d9e0f1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1) consolida duplicados previos: conserva el registro más antiguo
    try:
        conn.execute(sa.text(
            "DELETE FROM rendimiento_detalles WHERE id NOT IN ("
            "SELECT MIN(id) FROM rendimiento_detalles "
            "WHERE orden_produccion_id IS NOT NULL "
            "GROUP BY orden_produccion_id, operacion_id)"
            " AND orden_produccion_id IS NOT NULL"
        ))
    except Exception:
        pass
    # 2) candado único (NULLs múltiples permitidos: tareo general sin prenda)
    try:
        op.create_unique_constraint("uq_detalle_prenda_operacion", "rendimiento_detalles",
                                    ["orden_produccion_id", "operacion_id"])
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_constraint("uq_detalle_prenda_operacion", "rendimiento_detalles", type_="unique")
    except Exception:
        pass
