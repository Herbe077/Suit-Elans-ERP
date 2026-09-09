"""personal planilla (sueldo/asigfam/pensiones/régimen) + tablas planilla

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0

- empleados: +sueldo_basico, +asignacion_familiar, +sistema_pensiones,
  +regimen_laboral (todo nullable/con default: compatibilidad total).
- planilla_cabecera + planilla_detalle (nuevas).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(conn).get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    with op.batch_alter_table("empleados") as batch:
        if not _has_column(conn, "empleados", "sueldo_basico"):
            batch.add_column(sa.Column("sueldo_basico", sa.Float(),
                                       nullable=True, server_default="0"))
        if not _has_column(conn, "empleados", "asignacion_familiar"):
            batch.add_column(sa.Column("asignacion_familiar", sa.Boolean(),
                                       nullable=True, server_default="0"))
        if not _has_column(conn, "empleados", "sistema_pensiones"):
            batch.add_column(sa.Column("sistema_pensiones", sa.String(20),
                                       nullable=True, server_default="ONP"))
        if not _has_column(conn, "empleados", "regimen_laboral"):
            batch.add_column(sa.Column("regimen_laboral", sa.String(20),
                                       nullable=True, server_default="General"))
    if "planilla_cabecera" not in tables:
        op.create_table(
            "planilla_cabecera",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("anio", sa.Integer(), nullable=False),
            sa.Column("mes", sa.Integer(), nullable=False),
            sa.Column("estado", sa.String(20), nullable=False,
                      server_default="PROCESADA"),
            sa.Column("total_bruto", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("total_descuento", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("total_neto", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("total_essalud", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("gasto_id", sa.Integer(), nullable=True),
            sa.Column("asiento_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(),
                      server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("anio", "mes", name="uq_planilla_periodo"),
        )
        op.create_index("ix_planilla_cabecera_anio", "planilla_cabecera",
                        ["anio"], unique=False)
    if "planilla_detalle" not in tables:
        op.create_table(
            "planilla_detalle",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("cabecera_id", sa.Integer(), nullable=False),
            sa.Column("empleado_id", sa.Integer(), nullable=True),
            sa.Column("nombres", sa.String(160), nullable=False,
                      server_default=""),
            sa.Column("sueldo_basico", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("asignacion_familiar", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("total_bruto", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("fondo_pension", sa.String(20), nullable=True),
            sa.Column("pension_pct", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("descuento_pension", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("neto_pagar", sa.Float(), nullable=False,
                      server_default="0"),
            sa.Column("essalud", sa.Float(), nullable=False,
                      server_default="0"),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["cabecera_id"], ["planilla_cabecera.id"]),
            sa.ForeignKeyConstraint(["empleado_id"], ["empleados.id"]),
        )
        op.create_index("ix_planilla_detalle_cabecera", "planilla_detalle",
                        ["cabecera_id"], unique=False)


def downgrade() -> None:
    try:
        op.drop_index("ix_planilla_detalle_cabecera",
                      table_name="planilla_detalle")
    except Exception:
        pass
    try:
        op.drop_table("planilla_detalle")
    except Exception:
        pass
    try:
        op.drop_index("ix_planilla_cabecera_anio",
                      table_name="planilla_cabecera")
    except Exception:
        pass
    try:
        op.drop_table("planilla_cabecera")
    except Exception:
        pass
    with op.batch_alter_table("empleados") as batch:
        for col in ("sueldo_basico", "asignacion_familiar",
                    "sistema_pensiones", "regimen_laboral"):
            try:
                batch.drop_column(col)
            except Exception:
                pass
