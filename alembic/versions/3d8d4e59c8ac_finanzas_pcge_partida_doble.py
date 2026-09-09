"""finanzas pcge partida doble

Revision ID: 3d8d4e59c8ac
Revises: 0aa114401beb
Create Date: 2026-09-03 13:44:24.750747

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '3d8d4e59c8ac'
down_revision: Union[str, None] = '0aa114401beb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def upgrade() -> None:
    _bind = op.get_bind()
    _insp = sa.inspect(_bind)
    _ex_tables = set(_insp.get_table_names())
    _ex_cols = {t: {c["name"] for c in _insp.get_columns(t)} for t in _ex_tables}
    _ex_idxs = {t: {i["name"] for i in _insp.get_indexes(t)} for t in _ex_tables}
    conn = op.get_bind()
    # centros_costo
    if not _has_table(conn, "centros_costo"):
        if 'centros_costo' not in _ex_tables:
            op.create_table('centros_costo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(length=20), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_centros_costo_codigo' not in _ex_idxs.get('centros_costo', set()):
            op.create_index(op.f('ix_centros_costo_codigo'), 'centros_costo', ['codigo'], unique=True)
        if 'ix_centros_costo_tipo' not in _ex_idxs.get('centros_costo', set()):
            op.create_index(op.f('ix_centros_costo_tipo'), 'centros_costo', ['tipo'], unique=False)
    if not _has_table(conn, "cuentas_contables"):
        if 'cuentas_contables' not in _ex_tables:
            op.create_table('cuentas_contables',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(length=20), nullable=False),
        sa.Column('nombre', sa.String(length=120), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('nivel', sa.Integer(), nullable=False),
        sa.Column('cuenta_padre_id', sa.Integer(), nullable=True),
        sa.Column('acepta_movimientos', sa.Boolean(), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['cuenta_padre_id'], ['cuentas_contables.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_cuentas_contables_codigo' not in _ex_idxs.get('cuentas_contables', set()):
            op.create_index(op.f('ix_cuentas_contables_codigo'), 'cuentas_contables', ['codigo'], unique=True)
        if 'ix_cuentas_contables_cuenta_padre_id' not in _ex_idxs.get('cuentas_contables', set()):
            op.create_index(op.f('ix_cuentas_contables_cuenta_padre_id'), 'cuentas_contables', ['cuenta_padre_id'], unique=False)
        if 'ix_cuentas_contables_tipo' not in _ex_idxs.get('cuentas_contables', set()):
            op.create_index(op.f('ix_cuentas_contables_tipo'), 'cuentas_contables', ['tipo'], unique=False)
    if not _has_table(conn, "periodos_contables"):
        if 'periodos_contables' not in _ex_tables:
            op.create_table('periodos_contables',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('mes', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('fecha_apertura', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('fecha_cierre', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_periodos_contables_anio' not in _ex_idxs.get('periodos_contables', set()):
            op.create_index(op.f('ix_periodos_contables_anio'), 'periodos_contables', ['anio'], unique=False)
        if 'ix_periodos_contables_estado' not in _ex_idxs.get('periodos_contables', set()):
            op.create_index(op.f('ix_periodos_contables_estado'), 'periodos_contables', ['estado'], unique=False)
        if 'ix_periodos_contables_mes' not in _ex_idxs.get('periodos_contables', set()):
            op.create_index(op.f('ix_periodos_contables_mes'), 'periodos_contables', ['mes'], unique=False)
    if not _has_table(conn, "asientos_contables"):
        if 'asientos_contables' not in _ex_tables:
            op.create_table('asientos_contables',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('numero', sa.String(length=20), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('periodo_id', sa.Integer(), nullable=True),
        sa.Column('glosa', sa.String(length=255), nullable=False),
        sa.Column('origen_tipo', sa.String(length=20), nullable=False),
        sa.Column('origen_id', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['periodo_id'], ['periodos_contables.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_asientos_contables_estado' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_estado'), 'asientos_contables', ['estado'], unique=False)
        if 'ix_asientos_contables_fecha' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_fecha'), 'asientos_contables', ['fecha'], unique=False)
        if 'ix_asientos_contables_numero' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_numero'), 'asientos_contables', ['numero'], unique=True)
        if 'ix_asientos_contables_origen_id' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_origen_id'), 'asientos_contables', ['origen_id'], unique=False)
        if 'ix_asientos_contables_origen_tipo' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_origen_tipo'), 'asientos_contables', ['origen_tipo'], unique=False)
        if 'ix_asientos_contables_periodo_id' not in _ex_idxs.get('asientos_contables', set()):
            op.create_index(op.f('ix_asientos_contables_periodo_id'), 'asientos_contables', ['periodo_id'], unique=False)
    if not _has_table(conn, "gastos_registrados"):
        if 'gastos_registrados' not in _ex_tables:
            op.create_table('gastos_registrados',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proveedor_id', sa.Integer(), nullable=True),
        sa.Column('numero_comprobante', sa.String(length=40), nullable=True),
        sa.Column('monto_total', sa.Float(), nullable=False),
        sa.Column('clasificacion', sa.String(length=30), nullable=False),
        sa.Column('variabilidad', sa.String(length=20), nullable=False),
        sa.Column('centro_costo_id', sa.Integer(), nullable=True),
        sa.Column('cuenta_id', sa.Integer(), nullable=True),
        sa.Column('fecha_emision', sa.Date(), nullable=False),
        sa.Column('fecha_vencimiento', sa.Date(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['centro_costo_id'], ['centros_costo.id'], ),
        sa.ForeignKeyConstraint(['cuenta_id'], ['cuentas_contables.id'], ),
        sa.ForeignKeyConstraint(['proveedor_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_gastos_registrados_clasificacion' not in _ex_idxs.get('gastos_registrados', set()):
            op.create_index(op.f('ix_gastos_registrados_clasificacion'), 'gastos_registrados', ['clasificacion'], unique=False)
    if not _has_table(conn, "lineas_asiento"):
        if 'lineas_asiento' not in _ex_tables:
            op.create_table('lineas_asiento',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('asiento_id', sa.Integer(), nullable=False),
        sa.Column('cuenta_id', sa.Integer(), nullable=False),
        sa.Column('debe', sa.Float(), nullable=False),
        sa.Column('haber', sa.Float(), nullable=False),
        sa.Column('centro_costo_id', sa.Integer(), nullable=True),
        sa.Column('centro_gestion_id', sa.Integer(), nullable=True),
        sa.Column('descripcion', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['asiento_id'], ['asientos_contables.id'], ),
        sa.ForeignKeyConstraint(['centro_costo_id'], ['centros_costo.id'], ),
        sa.ForeignKeyConstraint(['centro_gestion_id'], ['centros_costo.id'], ),
        sa.ForeignKeyConstraint(['cuenta_id'], ['cuentas_contables.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        if 'ix_lineas_asiento_asiento_id' not in _ex_idxs.get('lineas_asiento', set()):
            op.create_index(op.f('ix_lineas_asiento_asiento_id'), 'lineas_asiento', ['asiento_id'], unique=False)
        if 'ix_lineas_asiento_cuenta_id' not in _ex_idxs.get('lineas_asiento', set()):
            op.create_index(op.f('ix_lineas_asiento_cuenta_id'), 'lineas_asiento', ['cuenta_id'], unique=False)
    # SQLite batch for alter column / FK (idempotente)
    try:
        with op.batch_alter_table('fabrics', recreate='auto') as batch_op:
            # nullable change is safe via batch
            batch_op.alter_column('stock_reservado', existing_type=sa.FLOAT(), nullable=False, existing_server_default=sa.text('0'))
            # FK may already exist; try to create, ignore if exists
            try:
                batch_op.create_foreign_key(None, 'suppliers', ['proveedor_id'], ['id'])
            except Exception:
                pass
    except Exception:
        pass
    try:
        with op.batch_alter_table('supplies', recreate='auto') as batch_op:
            batch_op.alter_column('stock_reservado', existing_type=sa.FLOAT(), nullable=False, existing_server_default=sa.text('0'))
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    # batch for SQLite
    try:
        with op.batch_alter_table('supplies', recreate='auto') as batch_op:
            batch_op.alter_column('stock_reservado', existing_type=sa.FLOAT(), nullable=True, existing_server_default=sa.text('0'))
    except Exception:
        pass
    try:
        with op.batch_alter_table('fabrics', recreate='auto') as batch_op:
            try:
                batch_op.drop_constraint(None, type_='foreignkey')
            except Exception:
                pass
            batch_op.alter_column('stock_reservado', existing_type=sa.FLOAT(), nullable=True, existing_server_default=sa.text('0'))
    except Exception:
        pass
    for tbl in ["lineas_asiento", "gastos_registrados", "asientos_contables", "periodos_contables", "cuentas_contables", "centros_costo"]:
        if _has_table(conn, tbl):
            # drop indexes first if exists
            try:
                op.drop_table(tbl)
            except Exception:
                pass
