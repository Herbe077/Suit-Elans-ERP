"""categorías de insumos estandarizadas + ancho categoria VARCHAR(30)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9

- productos_insumo.categoria: VARCHAR(20) -> VARCHAR(30).
- Backfill idempotente legacy -> canónico (TELA/TELAS->TELA_PRINCIPAL,
  FORRO/FORROS->FORROS, AVIO/AVÍOS/AVIOS->AVIOS_Y_FORNITURAS,
  EMPAQUE/EMPAQUES->EMPAQUES_Y_PRESENTACION).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAPEO = {
    "TELA": "TELA_PRINCIPAL", "TELAS": "TELA_PRINCIPAL",
    "FORRO": "FORROS", "FORROS": "FORROS",
    "AVIO": "AVIOS_Y_FORNITURAS", "AVÍOS": "AVIOS_Y_FORNITURAS",
    "AVIOS": "AVIOS_Y_FORNITURAS",
    "EMPAQUE": "EMPAQUES_Y_PRESENTACION", "EMPAQUES": "EMPAQUES_Y_PRESENTACION",
}


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"]: c for c in sa.inspect(conn).get_columns("productos_insumo")}
    if "categoria" not in cols:
        return
    if isinstance(cols["categoria"]["type"], sa.String) and (cols["categoria"]["type"].length or 0) >= 30:
        pass
    else:
        with op.batch_alter_table("productos_insumo") as batch:
            batch.alter_column("categoria", existing_type=sa.String(20),
                               type_=sa.String(30),
                               existing_nullable=False)
    for viejo, nuevo in MAPEO.items():
        conn.execute(sa.text(
            "UPDATE productos_insumo SET categoria=:nuevo WHERE categoria=:viejo"),
            {"nuevo": nuevo, "viejo": viejo})


def downgrade() -> None:
    conn = op.get_bind()
    inverso = {}
    for viejo, nuevo in MAPEO.items():
        inverso.setdefault(nuevo, viejo)
    for nuevo, viejo in inverso.items():
        conn.execute(sa.text(
            "UPDATE productos_insumo SET categoria=:viejo WHERE categoria=:nuevo"),
            {"viejo": viejo, "nuevo": nuevo})
    with op.batch_alter_table("productos_insumo") as batch:
        batch.alter_column("categoria", existing_type=sa.String(30),
                           type_=sa.String(20), existing_nullable=False)
