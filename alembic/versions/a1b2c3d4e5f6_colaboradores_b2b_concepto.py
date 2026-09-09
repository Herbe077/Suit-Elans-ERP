"""colaboradores b2b y concepto de pedido

Revision ID: a1b2c3d4e5f6
Revises: 9e0f1a2b3c4d
Create Date: 2026-09-07

- clients.company_id: colaborador / beneficiario final de una empresa B2B
  (la facturación sigue a nombre de la empresa vía orders.company_id).
- orders.concepto: detalle del servicio (alta desde Clientes y POS).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9e0f1a2b3c4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set:
    try:
        return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    if "company_id" not in _columns("clients"):
        op.add_column("clients", sa.Column("company_id", sa.Integer(), nullable=True))
    try:
        op.create_index(op.f("ix_clients_company_id"), "clients", ["company_id"],
                        unique=False)
    except Exception:
        pass
    try:
        op.create_foreign_key("fk_clients_company_id", "clients", "companies",
                              ["company_id"], ["id"])
    except Exception:
        pass
    if "concepto" not in _columns("orders"):
        op.add_column("orders", sa.Column("concepto", sa.String(length=255),
                                          nullable=True))


def downgrade() -> None:
    try:
        op.drop_constraint("fk_clients_company_id", "clients", type_="foreignkey")
    except Exception:
        pass
    try:
        op.drop_index(op.f("ix_clients_company_id"), table_name="clients")
    except Exception:
        pass
    try:
        op.drop_column("clients", "company_id")
    except Exception:
        pass
    try:
        op.drop_column("orders", "concepto")
    except Exception:
        pass
