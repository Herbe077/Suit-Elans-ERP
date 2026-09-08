"""Limpieza de transacciones de prueba en Neon PostgreSQL.

Uso:
    NEON_DATABASE_URL="postgresql://..." .venv/bin/python scripts/reset_transaccional_neon.py --dry-run
    NEON_DATABASE_URL="postgresql://..." .venv/bin/python scripts/reset_transaccional_neon.py --apply

- --dry-run (defecto): solo cuenta filas por tabla, no borra nada.
- --apply: TRUNCATE ... RESTART IDENTITY CASCADE en una sola transacción
  y reporta conteos antes/después (después debe ser 0).

Mantiene intactos: usuarios, clientes, empresas, proveedores, empleados,
medidas, CRM, catálogos, PCGE, centros, periodos, operaciones SAM,
tarifario, configuración y alembic_version. Nunca imprime la URL.
"""
import argparse
import os
import sys

TABLAS_TRANSACCIONALES = [
    "appointments",
    "asientos_contables",
    "lineas_asiento",
    "caja_turnos",
    "cash_movements",
    "comprobantes_venta",
    "control_calidad_produccion",
    "controles_calidad",
    "cuentas_por_cobrar",
    "cuentas_por_pagar",
    "detalles_orden_compra",
    "fichas_medidas",
    "garments",
    "gastos_registrados",
    "invoices",
    "movimientos_financieros",
    "movimientos_kardex",
    "orden_produccion",
    "ordenes_compra",
    "ordenes_venta",
    "orders",
    "pagos_orden",
    "payments",
    "prueba_entalle_produccion",
    "pruebas_entalle",
    "purchase_lines",
    "purchase_orders",
    "rendimiento_detalles",
    "rendimiento_registros",
    "stock_movements",
    "taller_cierre_detalle",
    "taller_cierres_jornada",
    "work_logs",
]


def _engine(url: str):
    from sqlalchemy import create_engine
    if not url.startswith("postgresql"):
        raise SystemExit("Rehúso operar fuera de PostgreSQL (URL debe ser postgresql://...)")
    return create_engine(url, pool_pre_ping=True)


def _tablas_existentes(conn, tablas: list) -> list:
    from sqlalchemy import inspect
    have = set(inspect(conn).get_table_names())
    return [t for t in tablas if t in have]


def conteos(conn, tablas: list) -> dict:
    from sqlalchemy import text
    out = {}
    for t in tablas:
        out[t] = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset transaccional en Neon (preserva maestros)")
    ap.add_argument("--apply", action="store_true", help="Ejecuta el TRUNCATE (sin esto: solo conteo)")
    args = ap.parse_args()
    url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        raise SystemExit("Define NEON_DATABASE_URL (nunca la pegues en archivos)")
    eng = _engine(url)
    from sqlalchemy import text
    with eng.connect() as conn:
        tablas = _tablas_existentes(conn, TABLAS_TRANSACCIONALES)
        faltan = sorted(set(TABLAS_TRANSACCIONALES) - set(tablas))
        if faltan:
            print(f"Aviso: no existen y se omiten: {', '.join(faltan)}")
        antes = conteos(conn, tablas)
        total = sum(antes.values())
        print(f"Tablas objetivo: {len(tablas)} | filas transaccionales: {total}")
        for t, n in antes.items():
            if n:
                print(f"  {t}: {n}")
        if not args.apply:
            print("Dry-run: sin cambios. Re-ejecuta con --apply para borrar.")
            return 0
        lista = ", ".join(f'"{t}"' for t in tablas)
        with conn.begin():
            conn.execute(text(f"TRUNCATE TABLE {lista} RESTART IDENTITY CASCADE"))
        despues = conteos(conn, tablas)
        resto = sum(despues.values())
        print(f"TRUNCATE aplicado. Filas restantes: {resto}")
        for t, n in despues.items():
            if n:
                print(f"  PENDIENTE {t}: {n}")
        return 0 if resto == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
