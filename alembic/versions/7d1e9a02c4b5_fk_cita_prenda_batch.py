"""fk cita-prenda batch (SQLite no soporta ADD CONSTRAINT).

Revision ID: 7d1e9a02c4b5
Revises: df3f2939eea3
"""
from typing import Sequence, Union

from alembic import op

revision: str = '7d1e9a02c4b5'
down_revision: Union[str, None] = 'df3f2939eea3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("appointments", recreate="always") as batch_op:
        batch_op.create_foreign_key("fk_appointments_garment_id_garments", "garments",
                                    ["garment_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("appointments", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_appointments_garment_id_garments", type_="foreignkey")
