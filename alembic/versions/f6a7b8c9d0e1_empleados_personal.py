"""tabla empleados (Personal y Contratos, RxH 4ta)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        has = inspect(op.get_bind()).has_table("empleados")
    except Exception:
        has = False
    if not has:
        op.create_table(
            'empleados',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('nombres', sa.String(length=80), nullable=False),
            sa.Column('apellidos', sa.String(length=80), nullable=False),
            sa.Column('dni', sa.String(length=15), nullable=True),
            sa.Column('ruc', sa.String(length=11), nullable=True),
            sa.Column('telefono', sa.String(length=40), nullable=True),
            sa.Column('puesto', sa.String(length=20), nullable=False),
            sa.Column('tipo_contrato', sa.String(length=20), nullable=False),
            sa.Column('aplica_retencion_8', sa.Boolean(), nullable=False),
            sa.Column('cv_filename', sa.String(length=255), nullable=True),
            sa.Column('contrato_filename', sa.String(length=255), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('activo', sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('dni'),
            sa.UniqueConstraint('ruc'),
            sa.UniqueConstraint('user_id'),
        )


def downgrade() -> None:
    try:
        op.drop_table("empleados")
    except Exception:
        pass
