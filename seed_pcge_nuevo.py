#!/usr/bin/env python3
"""Seeder único del Plan Contable Operativo + motor de reglas (Clean Slate).

Reemplaza a seed_pcge_basico/ensure_cuenta_*. VACÍA las tablas del plan
(cuentas_contables, centros_costo, regla_contable) y carga plan + reglas.
Exige BD sin asientos (ejecuta antes el reset transaccional).

Uso:
    .venv/bin/python seed_pcge_nuevo.py
    .venv/bin/python seed_pcge_nuevo.py --database-url sqlite:////tmp/x.db
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def seed_nuevo(database_url: str | None = None, verbose: bool = True) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base  # noqa: F401 — registra modelos
    from app.models.finanzas import (
        Almacen,
        AsientoContable,
        CentroCosto,
        CuentaContable,
        Proyecto,
        ReglaContable,
    )
    from app.services.plan_operativo import REGLAS, cargar_plan_operativo

    url = (database_url or os.environ.get("DATABASE_URL")
           or "sqlite:///./suit_elans_dev.db").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    db = Session(engine)
    try:
        if db.query(AsientoContable).count():
            raise ValueError(
                "La BD tiene asientos: ejecuta el reset transaccional "
                "antes del seed (Clean Slate).")
        resumen = cargar_plan_operativo(db, desde_cero=True)
        db.commit()
        if verbose:
            print(f"OK plan operativo: {resumen['cuentas']} cuentas, "
                  f"{resumen['reglas']} reglas "
                  f"(+{db.query(CentroCosto).count()} centros, "
                  f"{db.query(Almacen).count()} almacenes, "
                  f"{db.query(Proyecto).count()} proyectos).")
            for cod, _d, debe, haber, _m in REGLAS:
                print(f"  {cod:22s} D[{debe}] H[{haber}]")
        return resumen
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed Plan Contable Operativo.")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args(argv)
    try:
        seed_nuevo(args.database_url)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
