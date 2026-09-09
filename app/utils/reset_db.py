#!/usr/bin/env python3
"""Reset Transaccional — limpia operaciones preservando maestros.

Vacía (DELETE/TRUNCATE) ÚNICAMENTE tablas transaccionales y reinicia a 0
los stocks. PRESERVA datos maestros y estructurales.

PURGA (transaccional):
  Kardex ........... movimientos_kardex, stock_movements
  Producción ....... orden_produccion, fichas_medidas,
                     prueba_entalle_produccion, control_calidad_produccion,
                     work_logs
  Contabilidad ..... lineas_asiento, asientos_contables
  Pedidos/Ventas ... orders, garments, pruebas_entalle, controles_calidad,
                     payments, ordenes_venta, pagos_orden, comprobantes_venta,
                     invoices, cash_movements, caja_turnos
  CxC/CxP/Finanzas . cuentas_por_cobrar, cuentas_por_pagar,
                     movimientos_financieros, gastos_registrados
  Compras .......... detalles_orden_compra, ordenes_compra,
                     purchase_lines, purchase_orders

  NOTA: no existe tabla `reservas` (las reservas se derivan del Kardex y se
  eliminan con movimientos_kardex); `orden_produccion` es singular.

PRESERVA (maestros/estructura):
  cuentas_contables, periodos_contables, centros_costo, users, clients,
  suppliers, companies, contacts, price_lists, collections, products,
  recetas_bom, recetas_bom_lineas, operations, taller_tareas, empleados,
  measurements, leads, quotations, quotation_lines, appointments,
  interacciones, configuracion, alembic_version (+ tablas de rendimiento).

STOCK (UPDATE a 0.00, sin borrar SKUs):
  productos_insumo (stock_fisico, stock_reservado),
  product_variants (stock), fabrics (stock_metros, stock_reservado),
  supplies (stock, stock_reservado).

- SQLite: DELETE en orden FK (hijas primero) + reinicio sqlite_sequence.
- PostgreSQL: TRUNCATE ... RESTART IDENTITY CASCADE + UPDATE stocks.

SEGURIDAD: URLs PostgreSQL o ENV=prod exigen --allow-prod; requiere
--force/--yes (o escribir BORRAR); --dry-run solo informa.

Uso:
    .venv/bin/python -m app.utils.reset_db --dry-run
    .venv/bin/python -m app.utils.reset_db --force
    .venv/bin/python -m app.utils.reset_db --force --include-terceros
    DATABASE_URL=sqlite:////tmp/demo.db .venv/bin/python -m app.utils.reset_db --force
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Tablas transaccionales a vaciar (nombres reales del metadata).
PURGE_TABLES = [
    # Kardex / ajustes de stock
    "movimientos_kardex", "stock_movements",
    # Producción taller (las reservas viven en el Kardex)
    "orden_produccion", "fichas_medidas",
    "prueba_entalle_produccion", "control_calidad_produccion",
    "work_logs",
    # Libro diario
    "lineas_asiento", "asientos_contables",
    # Pedidos y ventas (+ espejos spec y documentos)
    "orders", "garments", "pruebas_entalle", "controles_calidad",
    "payments", "ordenes_venta", "pagos_orden", "comprobantes_venta",
    "invoices", "cash_movements", "caja_turnos",
    # CxC / CxP / finanzas
    "cuentas_por_cobrar", "cuentas_por_pagar",
    "movimientos_financieros", "gastos_registrados",
    # Compras (canónicas + legacy espejo por folio)
    "detalles_orden_compra", "ordenes_compra",
    "purchase_lines", "purchase_orders",
]

# Terceros (OPCIONAL, solo con --include-terceros).
TERCEROS_TABLES = [
    "measurements", "contacts", "companies", "suppliers", "clients",
    "interacciones", "appointments",
    "quotation_lines", "quotations", "leads",
]

# Stocks a reiniciar: tabla -> columnas a 0.00 (verifica existencia).
STOCK_RESET: dict[str, list[str]] = {
    "productos_insumo": ["stock_fisico", "stock_reservado"],
    "product_variants": ["stock"],
    "fabrics": ["stock_metros", "stock_reservado"],
    "supplies": ["stock", "stock_reservado"],
}

PRESERVED_NOTE = (
    "cuentas_contables, periodos_contables, centros_costo, users, clients, "
    "suppliers, collections/products, recetas_bom, configuracion, alembic_version"
)


def resolve_url(database_url: str | None) -> str:
    if database_url and database_url.strip():
        url = database_url.strip()
    else:
        url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        root = Path(__file__).resolve().parent.parent.parent
        url = f"sqlite:///{root / 'suit_elans_dev.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def is_production_url(url: str) -> bool:
    if url.startswith("postgresql"):
        return True
    return (os.environ.get("ENV") or "").strip().lower() in ("prod", "production")


def existing_tables(engine) -> set[str]:
    from sqlalchemy import inspect
    return set(inspect(engine).get_table_names())


def purge_order(tables: list[str]) -> list[str]:
    """Hijas primero según el orden topológico del metadata de la app."""
    try:
        from app.models import Base  # noqa: F401 — registra modelos
        topo = [t.name for t in Base.metadata.sorted_tables]  # padres primero
        rank = {name: i for i, name in enumerate(topo)}
        # desconocidas primero (se asumen dependientes), luego hijas->padres
        return sorted(tables, key=lambda t: (rank.get(t, 10 ** 9), t),
                      reverse=True)
    except Exception:
        return sorted(tables)


def table_counts(engine, tables: list[str]) -> dict[str, int]:
    from sqlalchemy import text
    with engine.connect() as c:
        return {t: c.execute(
            text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            for t in tables}


def wipe(engine, tables: list[str], is_pg: bool) -> None:
    from sqlalchemy import text
    if not tables:
        return
    if is_pg:
        quoted = ", ".join(f'"{t}"' for t in tables)
        with engine.begin() as c:
            c.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    else:
        with engine.begin() as c:
            c.execute(text("PRAGMA foreign_keys=OFF"))
            for t in tables:
                c.execute(text(f'DELETE FROM "{t}"'))
            seq = c.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='sqlite_sequence'")).scalar()
            if seq:
                placeholders = ", ".join(f"'{t}'" for t in tables)
                c.execute(text("DELETE FROM sqlite_sequence WHERE name IN "
                               f"({placeholders})"))
            c.execute(text("PRAGMA foreign_keys=ON"))
        with engine.begin() as c:
            c.execute(text("VACUUM"))


def reset_stocks(engine, is_pg: bool) -> dict[str, int]:
    """UPDATE a 0.00 en columnas de stock existentes. Retorna filas tocadas."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    touched: dict[str, int] = {}
    with engine.begin() as c:
        for table, cols in STOCK_RESET.items():
            try:
                real = {col["name"] for col in insp.get_columns(table)}
            except Exception:
                continue  # tabla inexistente en esta BD: se omite
            sets = [f'"{col}" = 0' for col in cols if col in real]
            if not sets:
                continue
            r = c.execute(text(f'UPDATE "{table}" SET {", ".join(sets)}'))
            touched[table] = r.rowcount or 0
    return touched


def run_reset(database_url: str | None = None, force: bool = False,
              dry_run: bool = False,
              include_terceros: bool = False) -> dict:
    """Ejecuta el reset transaccional. Retorna resumen (o raise ValueError)."""
    from sqlalchemy import create_engine

    url = resolve_url(database_url)
    if is_production_url(url):
        raise ValueError(f"Destino de PRODUCCIÓN ({url}): re-ejecuta con "
                         "--allow-prod si estás seguro.")
    engine = create_engine(url)
    is_pg = url.startswith("postgresql")
    existing = existing_tables(engine)
    targets = [t for t in PURGE_TABLES if t in existing]
    if include_terceros:
        targets += [t for t in TERCEROS_TABLES if t in existing]
    orden = purge_order(targets)
    counts = table_counts(engine, orden)
    total = sum(counts.values())
    summary = {"url": url, "tablas": orden, "filas": total,
               "conteos": counts, "stocks": {}, "dry_run": dry_run}
    if dry_run:
        return summary
    if not force:
        raise ValueError("Falta confirmación (--force). Nada se modificó.")
    wipe(engine, orden, is_pg)
    summary["stocks"] = reset_stocks(engine, is_pg)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reset transaccional: purga operaciones, preserva maestros.")
    ap.add_argument("--database-url", default=None,
                    help="URL SQLAlchemy (default: $DATABASE_URL o suit_elans_dev.db)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--yes", action="store_true", help="Alias de --force")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo mostrar lo que se borraría")
    ap.add_argument("--include-terceros", action="store_true",
                    help="Vaciar también clients/suppliers/companies/contacts (+measurements/CRM)")
    ap.add_argument("--allow-prod", action="store_true",
                    help="Permitir URLs PostgreSQL / ENV prod")
    args = ap.parse_args(argv)

    url = resolve_url(args.database_url)
    label = url.split("@")[-1] if "@" in url else url
    if is_production_url(url) and not args.allow_prod:
        print(f"ABORTADO: el destino parece PRODUCCIÓN ({label}).")
        print("Re-ejecuta con --allow-prod si estás seguro.")
        return 3
    try:
        from sqlalchemy import create_engine
        engine = create_engine(url)
        existing = existing_tables(engine)
    except ImportError:
        print("ERROR: SQLAlchemy no instalado (usa el .venv del proyecto).")
        return 2
    targets = [t for t in PURGE_TABLES if t in existing]
    if args.include_terceros:
        targets += [t for t in TERCEROS_TABLES if t in existing]
    orden = purge_order(targets)
    counts = table_counts(engine, orden)
    total = sum(counts.values())

    print(f"Destino: {label}")
    print(f"Transaccionales: {len(orden)} tablas, {total} filas. "
          f"Preservadas (maestros): {PRESERVED_NOTE}")
    if args.dry_run:
        for t in orden:
            print(f"  - {t} ({counts[t]} filas)")
        print("  + stock a 0.00: " +
              ", ".join(f"{t}({', '.join(c)})"
                        for t, c in STOCK_RESET.items()))
        print("[dry-run] sin cambios.")
        return 0
    if not (args.force or args.yes):
        print("ADVERTENCIA: BORRA datos operativos y reinicia stocks (irreversible).")
        try:
            resp = input("Escribe BORRAR para confirmar: ").strip()
        except EOFError:
            resp = ""
        if resp != "BORRAR":
            print("Cancelado.")
            return 4
    try:
        summary = run_reset(url, force=True, dry_run=False,
                            include_terceros=args.include_terceros)
    except ValueError as e:
        print(f"ABORTADO: {e}")
        return 3
    print(f"OK: {len(orden)} tablas vaciadas, {total} filas borradas.")
    for t, n in summary["stocks"].items():
        print(f"  stock 0.00: {t} ({n} filas)")
    print("Maestros intactos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
