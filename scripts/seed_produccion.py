#!/usr/bin/env python3
"""Seeder de PRODUCCIÓN — limpieza total + usuario inicial + sede por defecto.

Deja la base lista para el despliegue:

1. TRUNCA/ELIMINA todas las tablas de datos:
   Gastos (gastos_registrados), CxP (cuentas_por_pagar), CxC
   (cuentas_por_cobrar), Inventario (fabrics, supplies, productos_insumo,
   product_variants, movimientos_kardex, stock_movements, ...),
   Asientos Contables (asientos_contables, lineas_asiento), Personal
   (empleados, planilla_cabecera, planilla_detalle), Movimientos
   (movimientos_financieros, cash_movements, caja_turnos, ...) y, en
   general, TODA tabla operativa y maestra salvo las preservadas abajo.
2. PRESERVA únicamente:
   - `users` (se vacía y se recrea con un solo ADMIN),
   - `alembic_version` (versión de migración, nunca se toca).
   Crea como único usuario inicial:
   - Email: suit@elans | Rol: ADMIN | Contraseña: $ADMIN_PASSWORD
     (hash bcrypt vía app.core.security.hash_password; jamás en plano).
3. RESETEA `configuracion` (parámetros de sede) a los valores por defecto
   de app.models.config.DEFAULTS.

Compatibilidad:
- PostgreSQL: TRUNCATE ... RESTART IDENTITY CASCADE (una transacción).
- SQLite: DELETE en orden FK + reinicio de sqlite_sequence + VACUUM.

SEGURIDAD:
- URLs PostgreSQL o ENV=prod/production exigen --allow-prod.
- Requiere --force/--yes (o escribir BORRAR) — nunca corre solo.
- --dry-run solo lista lo que se borraría, sin cambios.
- La contraseña NUNCA se imprime ni se guarda en archivos: viene de
  $ADMIN_PASSWORD o de --admin-password. Si no se provee, se genera una
  aleatoria segura y se muestra UNA vez por stdout para entregarla al
  cliente (cámbiala luego en /admin/usuarios).

Uso:
    .venv/bin/python scripts/seed_produccion.py --dry-run
    ADMIN_PASSWORD='...' .venv/bin/python scripts/seed_produccion.py --force
    DATABASE_URL='sqlite:///./suit_elans_dev.db' .venv/bin/python scripts/seed_produccion.py --force
    NEON_DATABASE_URL='postgresql://...' .venv/bin/python scripts/seed_produccion.py --force --allow-prod
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

ADMIN_EMAIL_DEFAULT = "suit@elans"
ADMIN_ROLE = "ADMIN"
ADMIN_NAME_DEFAULT = "Administrador"

# Tablas que jamás se borran (ni siquiera en producción).
PRESERVED = {"alembic_version"}
# `users` y `configuracion` se tratan de forma especial (recreación/reset),
# no como preservadas intactas.
SYSTEM_PREFIXES = ("sqlite_", "pg_", "sql_")


def resolve_url(database_url: str | None) -> str:
    if database_url and database_url.strip():
        url = database_url.strip()
    else:
        url = (os.environ.get("NEON_DATABASE_URL")
               or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        root = Path(__file__).resolve().parent.parent
        url = f"sqlite:///{root / 'suit_elans_dev.db'}"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def is_production_url(url: str) -> bool:
    if url.startswith("postgresql"):
        return True
    return (os.environ.get("ENV") or os.environ.get("ENVIRONMENT", "")).strip().lower() in (
        "prod", "production")


def target_tables(engine) -> list[str]:
    from sqlalchemy import inspect

    names = inspect(engine).get_table_names()
    return sorted(
        t for t in names
        if t not in PRESERVED
        and t not in ("users", "configuracion")
        and not any(t.startswith(p) for p in SYSTEM_PREFIXES)
    )


def purge_order(tables: list[str]) -> list[str]:
    """Hijas primero según el orden topológico del metadata (invertido)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.models import Base  # noqa: F401 — registra todos los modelos
        topo = [t.name for t in Base.metadata.sorted_tables]  # padres primero
        rank = {name: i for i, name in enumerate(topo)}
        return sorted(tables, key=lambda t: (rank.get(t, 10 ** 9), t), reverse=True)
    except Exception:
        return sorted(tables)


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
                c.execute(text(
                    "DELETE FROM sqlite_sequence WHERE name IN "
                    f"({placeholders})"))
            c.execute(text("PRAGMA foreign_keys=ON"))
        with engine.begin() as c:
            c.execute(text("VACUUM"))


def reset_users_and_config(engine, admin_email: str, admin_password: str,
                           admin_name: str) -> dict:
    """Vacía users, crea el ADMIN único (bcrypt) y resetea sede a DEFAULTS."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sqlalchemy import text

    from app.core import security
    from app.models.config import DEFAULTS

    admin_email = admin_email.lower().strip()
    is_pg = engine.dialect.name.startswith("postgres")
    with engine.begin() as c:
        # --- users: borrado total + reinicio de correlativo ---
        # (users no se TRUNCAnca por FKs potenciales; DELETE + reset secuencia)
        c.execute(text('DELETE FROM "users"'))
        if is_pg:
            try:
                c.execute(text(
                    "SELECT setval(pg_get_serial_sequence('\"users\"', 'id'), 1, false)"))
            except Exception:
                pass  # columna identity o sin secuencia: el id autoavanza igual
        else:
            c.execute(text("PRAGMA foreign_keys=OFF"))
            seq = c.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='sqlite_sequence'")).scalar()
            if seq:
                c.execute(text("DELETE FROM sqlite_sequence WHERE name='users'"))
            c.execute(text("PRAGMA foreign_keys=ON"))
        # --- admin único con hash bcrypt (nunca en plano) ---
        hashed = security.hash_password(admin_password)
        active = "TRUE" if is_pg else "1"
        c.execute(
            text(f'INSERT INTO "users" (email, full_name, hashed_password, role, is_active) '
                 f'VALUES (:email, :name, :hp, :role, {active})'),
            {"email": admin_email, "name": admin_name, "hp": hashed, "role": ADMIN_ROLE},
        )
        # --- configuracion: reseteo total a DEFAULTS ---
        c.execute(text('DELETE FROM "configuracion"'))
        for clave, valor in DEFAULTS.items():
            c.execute(
                text('INSERT INTO "configuracion" (clave, valor) VALUES (:k, :v)'),
                {"k": clave, "v": valor},
            )
    return {"admin_email": admin_email, "admin_role": ADMIN_ROLE,
            "config_keys": len(DEFAULTS)}


def resolve_password(cli_password: str | None) -> tuple[str, bool]:
    """Retorna (password, fue_generado). Nunca sale de memoria/archivos."""
    if cli_password:
        return cli_password, False
    env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env_pw:
        return env_pw, False
    if not sys.stdin.isatty():
        generated = secrets.token_urlsafe(16)
        return generated, True
    typed = getpass.getpass("Contraseña para suit@elans (vacío = generar): ").strip()
    if typed:
        return typed, False
    return secrets.token_urlsafe(16), True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seeder de producción: wipe total + admin + sede.")
    ap.add_argument("--database-url", default=None,
                    help="URL SQLAlchemy (default: $NEON_DATABASE_URL/$DATABASE_URL o suit_elans_dev.db)")
    ap.add_argument("--admin-email", default=os.environ.get("ADMIN_EMAIL", ADMIN_EMAIL_DEFAULT),
                    help="Email del admin inicial (default: suit@elans)")
    ap.add_argument("--admin-password", default=None,
                    help="Contraseña del admin (default: $ADMIN_PASSWORD o generar)")
    ap.add_argument("--admin-name", default=os.environ.get("ADMIN_NAME", ADMIN_NAME_DEFAULT))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--yes", action="store_true", help="Alias de --force")
    ap.add_argument("--dry-run", action="store_true", help="Solo mostrar, sin cambios")
    ap.add_argument("--allow-prod", action="store_true", help="Permitir PostgreSQL / ENV prod")
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
        print("Re-ejecuta con --allow-prod si estás seguro (pérdida total).")
        return 3

    engine = create_engine(url)
    tables = target_tables(engine)
    orden = purge_order(tables)
    with engine.connect() as c:
        counts = {t: c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
                  for t in tables}
        try:
            n_users = c.execute(text('SELECT COUNT(*) FROM "users"')).scalar() or 0
        except Exception:
            n_users = 0
        try:
            n_cfg = c.execute(text('SELECT COUNT(*) FROM "configuracion"')).scalar() or 0
        except Exception:
            n_cfg = 0
    total = sum(counts.values())

    print(f"Destino: {label}")
    print(f"Tablas a vaciar: {len(orden)} ({total} filas) + users({n_users}) + configuracion({n_cfg})")
    print("Preservada: alembic_version. Se recreará: users -> suit@elans/ADMIN, configuracion -> DEFAULTS.")
    if args.dry_run:
        for t in orden:
            if counts[t]:
                print(f"  - {t} ({counts[t]} filas)")
        print("[dry-run] sin cambios.")
        return 0

    if not (args.force or args.yes):
        print("ADVERTENCIA: BORRA TODOS los datos (Gastos, CxP, CxC, Inventario, "
              "Asientos, Personal, Movimientos...). Irreversible.")
        try:
            resp = input("Escribe BORRAR para confirmar: ").strip()
        except EOFError:
            resp = ""
        if resp != "BORRAR":
            print("Cancelado.")
            return 4

    password, generated = resolve_password(args.admin_password)
    wipe(engine, orden, url.startswith("postgresql"))
    summary = reset_users_and_config(engine, args.admin_email, password, args.admin_name)

    # Verificación: solo 1 usuario + sede completa.
    with engine.connect() as c:
        users = c.execute(text('SELECT email, role, is_active FROM "users"')).all()
        n_cfg = c.execute(text('SELECT COUNT(*) FROM "configuracion"')).scalar()
        resto = sum(c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
                    for t in orden)

    print(f"OK: {len(orden)} tablas vaciadas ({total} filas). Filas restantes: {resto}.")
    print(f"OK: users = {[(u[0], u[1]) for u in users]} | configuracion = {n_cfg} claves "
          f"(esperadas {summary['config_keys']}).")
    if generated:
        print(">>> Contraseña ADMIN generada (guárdala y cámbiala en /admin/usuarios):")
        print(f">>> {password}")
    else:
        print("Admin suit@elans creado con la contraseña provista (hash bcrypt).")
    return 0 if (len(users) == 1 and resto == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
