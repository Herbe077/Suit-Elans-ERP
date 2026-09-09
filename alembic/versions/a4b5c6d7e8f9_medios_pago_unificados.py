"""medios de pago unificados: medio_pago + cuenta_contable_id

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8

- movimientos_financieros: +medio_pago, +cuenta_contable_id (backfill desde
  cuenta_origen: Caja→EFECTIVO/1011, resto→TRANSFERENCIA/1041).
- cash_movements: +medio_pago, +cuenta_contable_id; normaliza metodo legacy
  a enum (efectivo→EFECTIVO, yape/plin→YAPE_PLIN, etc.).
- payments: +cuenta_contable_id; normaliza metodo a enum.
Todo nullable: compatibilidad total con datos existentes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_MEDIO = {
    "efectivo": "EFECTIVO", "cash": "EFECTIVO", "caja": "EFECTIVO",
    "caja chica": "EFECTIVO", "caja_chica": "EFECTIVO",
    "transferencia": "TRANSFERENCIA", "banco": "TRANSFERENCIA",
    "banco bcp": "TRANSFERENCIA", "tarjeta": "TARJETA",
    "yape": "YAPE_PLIN", "plin": "YAPE_PLIN",
}


def _has_column(conn, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(conn).get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table("movimientos_financieros") as batch:
        if not _has_column(conn, "movimientos_financieros", "medio_pago"):
            batch.add_column(sa.Column("medio_pago", sa.String(20), nullable=True))
        if not _has_column(conn, "movimientos_financieros", "cuenta_contable_id"):
            batch.add_column(sa.Column("cuenta_contable_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("movimientos_financieros") as batch:
        try:
            batch.create_foreign_key("fk_movfin_cuenta", "cuentas_contables",
                                     ["cuenta_contable_id"], ["id"])
        except Exception:
            pass
    with op.batch_alter_table("cash_movements") as batch:
        if not _has_column(conn, "cash_movements", "medio_pago"):
            batch.add_column(sa.Column("medio_pago", sa.String(20), nullable=True))
        if not _has_column(conn, "cash_movements", "cuenta_contable_id"):
            batch.add_column(sa.Column("cuenta_contable_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("cash_movements") as batch:
        try:
            batch.create_foreign_key("fk_cashmov_cuenta", "cuentas_contables",
                                     ["cuenta_contable_id"], ["id"])
        except Exception:
            pass
    with op.batch_alter_table("payments") as batch:
        if not _has_column(conn, "payments", "cuenta_contable_id"):
            batch.add_column(sa.Column("cuenta_contable_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("payments") as batch:
        try:
            batch.create_foreign_key("fk_payments_cuenta", "cuentas_contables",
                                     ["cuenta_contable_id"], ["id"])
        except Exception:
            pass
    try:
        op.create_index("ix_movfin_medio", "movimientos_financieros",
                        ["medio_pago"], unique=False)
    except Exception:
        pass
    try:
        op.create_index("ix_cashmov_medio", "cash_movements",
                        ["medio_pago"], unique=False)
    except Exception:
        pass

    # ── Backfill idempotente (solo filas aún sin normalizar) ──
    id_1011 = conn.execute(sa.text(
        "SELECT id FROM cuentas_contables WHERE codigo='1011'")).scalar()
    id_1041 = conn.execute(sa.text(
        "SELECT id FROM cuentas_contables WHERE codigo='1041'")).scalar()
    # movimientos: cuenta_origen Caja→1011/EFECTIVO, resto→1041/TRANSFERENCIA
    conn.execute(sa.text(
        "UPDATE movimientos_financieros SET medio_pago='EFECTIVO' "
        "WHERE medio_pago IS NULL AND UPPER(COALESCE(cuenta_origen,'')) LIKE 'CAJA%'"))
    conn.execute(sa.text(
        "UPDATE movimientos_financieros SET medio_pago='TRANSFERENCIA' "
        "WHERE medio_pago IS NULL"))
    if id_1011:
        conn.execute(sa.text(
            "UPDATE movimientos_financieros SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND medio_pago='EFECTIVO'"),
            {"i": id_1011})
    if id_1041:
        conn.execute(sa.text(
            "UPDATE movimientos_financieros SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND medio_pago != 'EFECTIVO'"),
            {"i": id_1041})
    # cash/payments: normaliza metodo legacy → enum + espejo medio_pago/cuenta
    for tabla in ("cash_movements", "payments"):
        for legacy, enum in LEGACY_MEDIO.items():
            conn.execute(sa.text(
                f"UPDATE {tabla} SET metodo=:e WHERE LOWER(TRIM(COALESCE(metodo,'')))=:l "
                "AND metodo != :e"), {"e": enum, "l": legacy})
        conn.execute(sa.text(
            f"UPDATE {tabla} SET metodo='TRANSFERENCIA' "
            "WHERE metodo NOT IN ('EFECTIVO','TRANSFERENCIA','TARJETA','YAPE_PLIN')"))
    conn.execute(sa.text(
        "UPDATE cash_movements SET medio_pago=metodo WHERE medio_pago IS NULL"))
    if id_1011:
        conn.execute(sa.text(
            "UPDATE cash_movements SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND metodo='EFECTIVO'"), {"i": id_1011})
        conn.execute(sa.text(
            "UPDATE payments SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND metodo='EFECTIVO'"), {"i": id_1011})
    if id_1041:
        conn.execute(sa.text(
            "UPDATE cash_movements SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND metodo != 'EFECTIVO'"), {"i": id_1041})
        conn.execute(sa.text(
            "UPDATE payments SET cuenta_contable_id=:i "
            "WHERE cuenta_contable_id IS NULL AND metodo != 'EFECTIVO'"), {"i": id_1041})


def downgrade() -> None:
    for ix, tabla in (("ix_movfin_medio", "movimientos_financieros"),
                      ("ix_cashmov_medio", "cash_movements")):
        try:
            op.drop_index(ix, table_name=tabla)
        except Exception:
            pass
    with op.batch_alter_table("payments") as batch:
        try:
            batch.drop_column("cuenta_contable_id")
        except Exception:
            pass
    with op.batch_alter_table("cash_movements") as batch:
        for col in ("cuenta_contable_id", "medio_pago"):
            try:
                batch.drop_column(col)
            except Exception:
                pass
    with op.batch_alter_table("movimientos_financieros") as batch:
        for col in ("cuenta_contable_id", "medio_pago"):
            try:
                batch.drop_column(col)
            except Exception:
                pass
