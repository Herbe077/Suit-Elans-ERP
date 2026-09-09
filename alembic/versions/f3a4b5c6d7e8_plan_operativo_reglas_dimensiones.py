"""plan operativo: flags imputable/analitica, regla_contable, dims en lineas

Revision ID: f3a4b5c6d7e8
Revises: c9d1e2f3a4b5
Create Date: 2026-09-08

Plan Contable Operativo + motor de reglas (Clean Slate):
- cuentas_contables: padre_codigo, imputable, analitica.
- regla_contable (matriz de contabilización).
- lineas_asiento: orden_produccion_id, producto_id, trabajador_id,
  almacen_id, cliente_id, proveedor_id, activo_fijo_id, proyecto_id.
- maestros mínimos: almacenes, proyectos, activos_fijos.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'c9d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(conn).get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    _insp = sa.inspect(conn)
    _tables = set(_insp.get_table_names())
    if not _has_column(conn, "cuentas_contables", "padre_codigo"):
        op.add_column("cuentas_contables",
                      sa.Column("padre_codigo", sa.String(20), nullable=True))
    if not _has_column(conn, "cuentas_contables", "imputable"):
        op.add_column("cuentas_contables",
                      sa.Column("imputable", sa.Boolean(), nullable=False,
                                server_default="1"))
    if not _has_column(conn, "cuentas_contables", "analitica"):
        op.add_column("cuentas_contables",
                      sa.Column("analitica", sa.Boolean(), nullable=False,
                                server_default="0"))
    if "regla_contable" not in _tables:
        op.create_table(
            "regla_contable",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("codigo_regla", sa.String(40), nullable=False),
            sa.Column("descripcion", sa.String(255), nullable=True),
            sa.Column("debe_patron", sa.String(120), nullable=False),
            sa.Column("haber_patron", sa.String(120), nullable=False),
            sa.Column("dimensiones_obligatorias", sa.JSON(), nullable=True),
            sa.Column("activa", sa.Boolean(), nullable=False,
                      server_default="1"),
            sa.Column("created_at", sa.DateTime(),
                      server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_regla_contable_codigo_regla", "regla_contable",
                        ["codigo_regla"], unique=True)
    for _t, _c, _ref in [
        ("almacenes", None, None),
        ("proyectos", None, None),
        ("activos_fijos", None, None),
    ]:
        if _t not in _tables:
            op.create_table(
                _t,
                sa.Column("id", sa.Integer(), nullable=False),
                sa.Column("codigo", sa.String(20 if _t != "activos_fijos" else 40),
                          nullable=False),
                sa.Column("nombre", sa.String(120 if _t != "activos_fijos" else 160),
                          nullable=False),
                sa.Column("activo", sa.Boolean(), nullable=False,
                          server_default="1"),
                sa.PrimaryKeyConstraint("id"),
            )
            op.create_index(f"ix_{_t}_codigo", _t, ["codigo"], unique=True)
    _dims = [
        ("orden_produccion_id", "orden_produccion"),
        ("producto_id", None),
        ("trabajador_id", "users"),
        ("almacen_id", "almacenes"),
        ("cliente_id", "clients"),
        ("proveedor_id", "suppliers"),
        ("activo_fijo_id", "activos_fijos"),
        ("proyecto_id", "proyectos"),
    ]
    with op.batch_alter_table("lineas_asiento") as batch:
        for _col, _ref in _dims:
            if not _has_column(conn, "lineas_asiento", _col):
                batch.add_column(sa.Column(_col, sa.Integer(), nullable=True))
    with op.batch_alter_table("lineas_asiento") as batch:
        for _col, _ref in _dims:
            if _ref:
                try:
                    batch.create_foreign_key(f"fk_lineas_{_col}", _ref,
                                             [_col], ["id"])
                except Exception:
                    pass
    for _col, _ in _dims:
        _ix = f"ix_lineas_{_col}"
        if not any(i["name"] == _ix for i in _insp.get_indexes("lineas_asiento")):
            try:
                op.create_index(_ix, "lineas_asiento", [_col], unique=False)
            except Exception:
                pass


def downgrade() -> None:
    for _ix in ["ix_lineas_orden_produccion_id", "ix_lineas_producto_id",
                "ix_lineas_trabajador_id", "ix_lineas_almacen_id",
                "ix_lineas_cliente_id", "ix_lineas_proveedor_id",
                "ix_lineas_activo_fijo_id", "ix_lineas_proyecto_id"]:
        try:
            op.drop_index(_ix, table_name="lineas_asiento")
        except Exception:
            pass
    with op.batch_alter_table("lineas_asiento") as batch:
        for _col in ["orden_produccion_id", "producto_id", "trabajador_id",
                     "almacen_id", "cliente_id", "proveedor_id",
                     "activo_fijo_id", "proyecto_id"]:
            try:
                batch.drop_column(_col)
            except Exception:
                pass
    for _t in ["activos_fijos", "proyectos", "almacenes"]:
        try:
            op.drop_index(f"ix_{_t}_codigo", table_name=_t)
        except Exception:
            pass
        try:
            op.drop_table(_t)
        except Exception:
            pass
    try:
        op.drop_index("ix_regla_contable_codigo_regla", table_name="regla_contable")
    except Exception:
        pass
    try:
        op.drop_table("regla_contable")
    except Exception:
        pass
    with op.batch_alter_table("cuentas_contables") as batch:
        for _col in ["padre_codigo", "imputable", "analitica"]:
            try:
                batch.drop_column(_col)
            except Exception:
                pass
