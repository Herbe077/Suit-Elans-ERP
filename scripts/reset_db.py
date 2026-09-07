#!/usr/bin/env python3
"""Reset de base de datos (mantenimiento) — purga operativa + reinicio.

Limpia en orden correcto de integridad referencial (hijas primero) todas las
tablas operativas:
  Ventas/POS (órdenes, cotizaciones, comprobantes, caja), Finanzas/Caja
  (CxC, CxP, gastos, flujo), Inventario/Almacén (kardex, telas, insumos,
  catálogo), Producción/Taller (fichas, medidas, tareo/destajo),
  Clientes/CRM (personas, empresas, contactos), Rendimiento.
PRESERVA `users` (RBAC por rol en código), `configuracion` (sede/empresa) y
`alembic_version`. Reinicia correlativos (RESTART IDENTITY / AUTOINCREMENT).

- PostgreSQL: `TRUNCATE ... RESTART IDENTITY CASCADE` en orden FK.
- SQLite: `DELETE` en orden FK + reinicio de `sqlite_sequence` + `VACUUM`.

SEGURIDAD:
- URLs PostgreSQL o `ENV=prod/production` exigen `--allow-prod`.
- Requiere confirmación `--force` (o interactiva BORRAR) — nunca corre solo.
- `--dry-run` solo lista lo que se borraría (en orden de purga).

Uso:
    python scripts/reset_db.py --dry-run
    python scripts/reset_db.py --force
    DATABASE_URL=sqlite:////tmp/demo.db python scripts/reset_db.py --force
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PRESERVED = {"users", "alembic_version", "configuracion"}
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


def purge_order(engine, tables: list[str]) -> list[str]:
    """Orden de purga FK-correcto: tablas hijas primero.

    Usa el orden topológico de los metadatos de la app (invertido); las
    tablas no registradas en metadatos van primero (asume dependencias).
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.models import Base  # noqa: F401 — registra todos los modelos
        topo = [t.name for t in Base.metadata.sorted_tables]  # padres primero
        rank = {name: i for i, name in enumerate(topo)}
        return sorted(tables, key=lambda t: rank.get(t, float("inf")),
                      reverse=True)
    except Exception:
        return sorted(tables)


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
    ap.add_argument("--force", action="store_true", help="Confirmación requerida (anti-producción accidental)")
    ap.add_argument("--yes", action="store_true", help="Alias de --force")
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
    orden = purge_order(engine, tables)
    with engine.connect() as c:
        counts = {t: c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                  for t in tables}
    total = sum(counts.values())
    print(f"Destino: {label}  (tablas operativas: {len(tables)}, filas: {total})")
    print("Preservadas: users (RBAC por rol), configuracion (sede), alembic_version")
    if args.dry_run:
        for t in orden:
            print(f"  - {t} ({counts[t]} filas)")
        print("[dry-run] sin cambios.")
        return 0
    if not (args.force or args.yes):
        print("ADVERTENCIA: esto BORRA todos los datos operativos (irreversible).")
        resp = input("Escribe BORRAR para confirmar: ").strip()
        if resp != "BORRAR":
            print("Cancelado.")
            return 4
    wipe(engine, orden, url.startswith("postgresql"))
    print(f"OK: {len(orden)} tablas limpiadas en orden FK, {total} filas borradas. users intactos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
