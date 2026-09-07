#!/usr/bin/env python3
"""Reset de base de datos (mantenimiento).

Limpia todas las tablas operativas (pedidos, ventas, inventario, kardex,
contabilidad, clientes, etc.) y PRESERVA `users` y `alembic_version`.

- PostgreSQL: un solo `TRUNCATE ... RESTART IDENTITY CASCADE`.
- SQLite: `DELETE` + reinicio de `sqlite_sequence` + `VACUUM`.

SEGURIDAD:
- URLs PostgreSQL o `ENV=prod/production` exigen `--allow-prod`.
- Sin `--yes` pide confirmación interactiva (escribir BORRAR).
- `--dry-run` solo lista lo que se borraría.

Uso:
    python scripts/reset_db.py --dry-run
    python scripts/reset_db.py --yes
    DATABASE_URL=sqlite:////tmp/demo.db python scripts/reset_db.py --yes
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PRESERVED = {"users", "alembic_version"}
SYSTEM_PREFIXES = ("sqlite_", "pg_", "sql_")


def resolve_url(database_url: str | None) -> str:
    if database_url:
        url = database_url.strip()
    else:
        url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        root = Path(__file__).resolve().parent.parent
        url = f"sqlite:///{root / 'suitelans.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def is_production_url(url: str) -> bool:
    if url.startswith("postgresql"):
        return True
    return (os.environ.get("ENV") or "").strip().lower() in ("prod", "production")


def target_tables(engine) -> list[str]:
    from sqlalchemy import inspect

    names = inspect(engine).get_table_names()
    out = [t for t in names
           if t not in PRESERVED
           and not any(t.startswith(p) for p in SYSTEM_PREFIXES)]
    return sorted(out)


def wipe(engine, tables: list[str], is_pg: bool) -> None:
    from sqlalchemy import text

    if is_pg:
        quoted = ", ".join(f'"{t}"' for t in tables)
        with engine.begin() as c:
            c.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    else:
        with engine.begin() as c:
            c.execute(text("PRAGMA foreign_keys=OFF"))
            for t in tables:
                c.execute(text(f'DELETE FROM "{t}"'))
            if tables:
                seq = c.execute(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name='sqlite_sequence'")).scalar()
                if seq:
                    placeholders = ", ".join(f"'{t}'" for t in tables)
                    c.execute(text(
                        "DELETE FROM sqlite_sequence WHERE name IN "
                        f"({placeholders})"))
            c.execute(text("PRAGMA foreign_keys=ON"))
        with engine.begin() as c:
            c.execute(text("VACUUM"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reset de BD (preserva users).")
    ap.add_argument("--database-url", default=None, help="URL SQLAlchemy (default: $DATABASE_URL o suitelans.db)")
    ap.add_argument("--yes", action="store_true", help="Omitir confirmación interactiva")
    ap.add_argument("--dry-run", action="store_true", help="Solo mostrar lo que se borraría")
    ap.add_argument("--allow-prod", action="store_true", help="Permitir URLs PostgreSQL / ENV prod")
    args = ap.parse_args(argv)

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("ERROR: SQLAlchemy no instalado (usa el .venv del proyecto).")
        return 2

    url = resolve_url(args.database_url)
    label = url.split("@")[-1] if "@" in url else url
    prod = is_production_url(url)
    if prod and not args.allow_prod:
        print(f"ABORTADO: el destino parece PRODUCCIÓN ({label}).")
        print("Re-ejecuta con --allow-prod si estás seguro (riesgo de pérdida total).")
        return 3

    engine = create_engine(url)
    tables = target_tables(engine)
    with engine.connect() as c:
        counts = {t: c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                  for t in tables}
    total = sum(counts.values())
    print(f"Destino: {label}  (tablas operativas: {len(tables)}, filas: {total})")
    print("Preservadas: users, alembic_version")
    if args.dry_run:
        for t in tables:
            print(f"  - {t} ({counts[t]} filas)")
        print("[dry-run] sin cambios.")
        return 0
    if not args.yes:
        print("ADVERTENCIA: esto BORRA todos los datos operativos (irreversible).")
        resp = input("Escribe BORRAR para confirmar: ").strip()
        if resp != "BORRAR":
            print("Cancelado.")
            return 4
    wipe(engine, tables, url.startswith("postgresql"))
    print(f"OK: {len(tables)} tablas limpiadas, {total} filas borradas. users intactos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
