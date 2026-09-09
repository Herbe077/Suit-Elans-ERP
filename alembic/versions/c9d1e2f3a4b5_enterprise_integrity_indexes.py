"""enterprise integrity, finance flow fields and performance indexes"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(conn).get_columns(table))


def _has_index(conn, table: str, name: str) -> bool:
    return any(i["name"] == name for i in sa.inspect(conn).get_indexes(table))


def _add_column(conn, table: str, column: sa.Column) -> None:
    if not _has_column(conn, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    conn = op.get_bind()

    # Reintegrates the previously orphaned Peru-localization migration safely.
    _add_column(conn, "clients", sa.Column("tipo_doc", sa.String(12), nullable=True, server_default="DNI"))
    _add_column(conn, "clients", sa.Column("nro_doc", sa.String(20), nullable=True))
    _add_column(conn, "clients", sa.Column("distrito", sa.String(100), nullable=True))
    _add_column(conn, "companies", sa.Column("distrito", sa.String(100), nullable=True))
    op.execute(sa.text("UPDATE clients SET tipo_doc='DNI' WHERE tipo_doc IS NULL"))
    if conn.dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE clients ALTER COLUMN tipo_doc SET NOT NULL"))

    _add_column(conn, "gastos_registrados", sa.Column("fecha_vencimiento", sa.Date(), nullable=True))
    _add_column(conn, "gastos_registrados", sa.Column("actividad_flujo", sa.String(20), nullable=True, server_default="OPERATIVO"))
    op.execute(sa.text("UPDATE gastos_registrados SET actividad_flujo='INVERSION' WHERE UPPER(COALESCE(categoria,''))='ACTIVO_FIJO'"))
    op.execute(sa.text("UPDATE gastos_registrados SET actividad_flujo='OPERATIVO' WHERE actividad_flujo IS NULL OR actividad_flujo NOT IN ('OPERATIVO','INVERSION','FINANCIAMIENTO')"))
    op.execute(sa.text("UPDATE gastos_registrados SET fecha_vencimiento=fecha_emision + INTERVAL '30 days' WHERE fecha_vencimiento IS NULL AND fecha_emision IS NOT NULL")) if conn.dialect.name == "postgresql" else op.execute(sa.text("UPDATE gastos_registrados SET fecha_vencimiento=date(fecha_emision, '+30 day') WHERE fecha_vencimiento IS NULL AND fecha_emision IS NOT NULL"))

    # Canonicalize legacy finance values before enforcing database constraints.
    op.execute(sa.text("UPDATE cuentas_por_pagar SET origen_tipo='PROVEEDORES MATERIA PRIMA' WHERE UPPER(REPLACE(COALESCE(origen_tipo,''),'_',' ')) IN ('COMPRAS','PROVEEDORES MATERIA PRIMA')"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET origen_tipo='GASTOS OPERATIVOS' WHERE UPPER(REPLACE(COALESCE(origen_tipo,''),'_',' ')) IN ('GASTOS','GASTOS OPERATIVOS')"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET origen_tipo='SERVICIOS TERCERIZADOS' WHERE UPPER(REPLACE(COALESCE(origen_tipo,''),'_',' ')) IN ('DESTAJO','SERVICIOS TERCERIZADOS')"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET origen_tipo='ACTIVOS Y MAQUINARIA' WHERE UPPER(REPLACE(COALESCE(origen_tipo,''),'_',' ')) IN ('ACTIVOS Y MAQUINARIA','ACTIVOS')"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET origen_tipo='GASTOS OPERATIVOS' WHERE origen_tipo IS NULL OR origen_tipo NOT IN ('PROVEEDORES MATERIA PRIMA','SERVICIOS TERCERIZADOS','GASTOS OPERATIVOS','ACTIVOS Y MAQUINARIA')"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET actividad_flujo=UPPER(actividad_flujo) WHERE actividad_flujo IS NOT NULL"))
    op.execute(sa.text("UPDATE cuentas_por_pagar SET actividad_flujo='OPERATIVO' WHERE actividad_flujo IS NULL OR actividad_flujo NOT IN ('OPERATIVO','INVERSION','FINANCIAMIENTO')"))

    indexes = [
        ("ix_cxp_proveedor_estado", "cuentas_por_pagar", ["proveedor_id", "estado"]),
        ("ix_cxp_vencimiento_estado", "cuentas_por_pagar", ["fecha_vencimiento", "estado"]),
        ("ix_cxp_origen_estado", "cuentas_por_pagar", ["origen_tipo", "estado"]),
        ("ix_gasto_fecha_estado", "gastos_registrados", ["fecha_emision", "estado"]),
        ("ix_gasto_proveedor_estado", "gastos_registrados", ["proveedor_id", "estado"]),
        ("ix_gasto_centro_fecha", "gastos_registrados", ["centro_costo_id", "fecha_emision"]),
        ("ix_gasto_actividad_fecha", "gastos_registrados", ["actividad_flujo", "fecha_emision"]),
        ("ix_asiento_fecha_origen", "asientos_contables", ["fecha", "origen_tipo"]),
        ("ix_asiento_origen_ref", "asientos_contables", ["origen_tipo", "origen_id"]),
        ("ix_kardex_producto_fecha", "movimientos_kardex", ["producto_id", "fecha"]),
        ("ix_kardex_oc_fecha", "movimientos_kardex", ["orden_compra_id", "fecha"]),
        ("ix_oc_proveedor_estado_fecha", "ordenes_compra", ["proveedor_id", "estado", "fecha_emision"]),
        ("ix_clients_nro_doc", "clients", ["nro_doc"]),
    ]
    for name, table, cols in indexes:
        if not _has_index(conn, table, name):
            op.create_index(name, table, cols, unique=False)

    # Strong database-level invariants on PostgreSQL. SQLite receives the same
    # invariants through the ORM on fresh databases; existing SQLite is handled
    # by the runtime normalizer without destructive table rebuilds.
    if conn.dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE cuentas_por_pagar DROP CONSTRAINT IF EXISTS ck_cxp_origen_tipo"))
        op.create_check_constraint("ck_cxp_origen_tipo", "cuentas_por_pagar", "origen_tipo IN ('PROVEEDORES MATERIA PRIMA','SERVICIOS TERCERIZADOS','GASTOS OPERATIVOS','ACTIVOS Y MAQUINARIA')")
        op.execute(sa.text("ALTER TABLE cuentas_por_pagar DROP CONSTRAINT IF EXISTS ck_cxp_actividad_flujo"))
        op.create_check_constraint("ck_cxp_actividad_flujo", "cuentas_por_pagar", "actividad_flujo IN ('OPERATIVO','INVERSION','FINANCIAMIENTO')")
        op.execute(sa.text("ALTER TABLE gastos_registrados DROP CONSTRAINT IF EXISTS ck_gasto_actividad_flujo"))
        op.create_check_constraint("ck_gasto_actividad_flujo", "gastos_registrados", "actividad_flujo IN ('OPERATIVO','INVERSION','FINANCIAMIENTO')")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        for name in ("ck_gasto_actividad_flujo", "ck_cxp_actividad_flujo", "ck_cxp_origen_tipo"):
            op.drop_constraint(name, "gastos_registrados" if name.startswith("ck_gasto") else "cuentas_por_pagar", type_="check")
    for name, table in (
        ("ix_clients_nro_doc", "clients"), ("ix_oc_proveedor_estado_fecha", "ordenes_compra"),
        ("ix_kardex_oc_fecha", "movimientos_kardex"), ("ix_kardex_producto_fecha", "movimientos_kardex"),
        ("ix_asiento_origen_ref", "asientos_contables"), ("ix_asiento_fecha_origen", "asientos_contables"),
        ("ix_gasto_actividad_fecha", "gastos_registrados"), ("ix_gasto_centro_fecha", "gastos_registrados"),
        ("ix_gasto_proveedor_estado", "gastos_registrados"), ("ix_gasto_fecha_estado", "gastos_registrados"),
        ("ix_cxp_origen_estado", "cuentas_por_pagar"), ("ix_cxp_vencimiento_estado", "cuentas_por_pagar"),
        ("ix_cxp_proveedor_estado", "cuentas_por_pagar"), ("ix_clients_nro_doc", "clients"),
    ):
        if _has_index(conn, table, name):
            op.drop_index(name, table_name=table)
