"""fk caja-turno batch (SQLite no soporta ADD CONSTRAINT).

Revision ID: 3f8c2d91a6e4
Revises: 7b058dc99ab3
"""
from typing import Sequence, Union

from alembic import op

revision: str = '3f8c2d91a6e4'
down_revision: Union[str, None] = '7b058dc99ab3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("cash_movements", recreate="always") as batch_op:
        batch_op.create_foreign_key("fk_cash_turno_id_turnos", "caja_turnos",
                                    ["turno_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("cash_movements", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_cash_turno_id_turnos", type_="foreignkey")
