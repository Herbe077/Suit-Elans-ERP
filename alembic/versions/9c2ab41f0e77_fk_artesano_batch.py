"""fk artesano sqlite batch

Revision ID: 9c2ab41f0e77
Revises: 66330a0d74a9
Create Date: 2026-09-03

FK garments.artesano_id -> users.id vía batch recreate (SQLite no
soporta ADD CONSTRAINT). En PostgreSQL el batch es no-op equivalente.
"""
from typing import Sequence, Union

from alembic import op

revision: str = '9c2ab41f0e77'
down_revision: Union[str, None] = '66330a0d74a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("garments", recreate="always") as batch_op:
        batch_op.create_foreign_key("fk_garments_artesano_id_users", "users",
                                    ["artesano_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("garments", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_garments_artesano_id_users", type_="foreignkey")
