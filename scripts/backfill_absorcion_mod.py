"""Backfill: absorción de MOD en pedidos bespoke ya cerrados sin ABSORCION_MOD.

Para cada pedido con prendas (bespoke) en estado entregado y sin absorción:
- Absorbe la MOD al WIP (2311 vs 9211, minutos tareo → sam → SAM estándar).
- Si ya tiene asiento COSTO_VENTAS, postea el complemento 6921/2311 por la
  MOD (el costo original se calculó sin mano de obra).
- Si no tiene costo ni salida, corre el flujo normal (_salida_venta_cta23:
  absorción + costo + SALIDA_VENTA).

Uso (SQLite local):
    .venv/bin/python scripts/backfill_absorcion_mod.py --dry-run
    .venv/bin/python scripts/backfill_absorcion_mod.py --db sqlite:///./suit_elans_dev.db

Uso (producción PostgreSQL/Neon, p.ej. Render Shell):
    NEON_DATABASE_URL="postgresql://..." \
      .venv/bin/python scripts/backfill_absorcion_mod.py \
      --db "$NEON_DATABASE_URL" --dry-run
    NEON_DATABASE_URL="postgresql://..." \
      .venv/bin/python scripts/backfill_absorcion_mod.py \
      --db "$NEON_DATABASE_URL" --apply

- En SQLite hace backup antes de escribir; en PostgreSQL solo escribe con
  --apply explícito. Idempotente.
"""
import argparse
import os
import shutil
import sys
from datetime import datetime


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill absorción MOD bespoke")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", "sqlite:///./suit_elans_dev.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--folio", default=None, help="limita a un folio (p.ej. SE-2026-0001)")
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = args.db
    es_pg = args.db.startswith("postgresql")
    if es_pg and not args.apply:
        args.dry_run = True
    if es_pg and args.apply and args.dry_run:
        print("--apply y --dry-run son excluyentes")
        return 2

    from app.core.database import Base, SessionLocal, engine
    import app.models  # noqa: F401
    from app.models.finanzas import AsientoContable
    from app.models.order import Garment, Order
    from app.services import contabilidad as contab

    db_path = None
    if args.db.startswith("sqlite:///"):
        p = args.db[len("sqlite:///"):]
        db_path = p if os.path.isabs(p) else os.path.join(os.getcwd(), p)
    if not args.dry_run and db_path and os.path.isfile(db_path):
        bak = f"{db_path}.bak-backfill-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(db_path, bak)
        print(f"backup: {bak}")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    rep = {"revisados": 0, "absorbidos": [], "complementos": [],
           "flujo_completo": [], "omitidos": []}
    try:
        q = db.query(Order).filter(Order.estado == "entregado").order_by(Order.id)
        if args.folio:
            q = q.filter(Order.folio == args.folio)
        for o in q.all():
            if not db.query(Garment).filter(Garment.order_id == o.id).first():
                continue  # no bespoke (p.ej. venta directa sin prendas)
            rep["revisados"] += 1
            if contab._existe_absorcion_mod(db, o.id):
                rep["omitidos"].append(f"{o.folio}: ya absorbido")
                continue
            minutos, monto, fuente = 0.0, 0.0, "sin_datos"
            try:
                minutos, monto, fuente = contab.mo_estimada_pedido(db, o.id)
            except Exception as e:
                rep["omitidos"].append(f"{o.folio}: sin base MOD ({e})")
                continue
            if not monto or monto <= 0:
                rep["omitidos"].append(f"{o.folio}: MOD 0 (sin tareo/sam/SAM)")
                continue
            tiene_costo = db.query(AsientoContable).filter(
                AsientoContable.origen_tipo == "COSTO_VENTAS",
                AsientoContable.origen_id == o.id).first() is not None
            if args.dry_run:
                rep["absorbidos"].append(
                    f"{o.folio}: absorbería S/ {monto:.2f} ({minutos:g}min {fuente})"
                    + (" + complemento COGS" if tiene_costo else " + flujo completo"))
                continue
            abs_res = contab.absorber_mod_pedido(db, o.id, None)
            rep["absorbidos"].append(
                f"{o.folio}: absorbido S/ {abs_res['monto']:.2f} "
                f"({abs_res['minutos']:g}min {abs_res['fuente']}) "
                f"asiento {abs_res.get('asiento_numero')}")
            if tiene_costo:
                from app.services.motor_contable import post_regla
                comp = post_regla(
                    db, "COSTO_VENTA_BESPOKE", [abs_res["monto"]],
                    [abs_res["monto"]],
                    {"cliente_id": contab.resolver_cliente_orden(db, o)},
                    f"Complemento MOD {o.folio}", "COSTO_VENTAS", o.id, None)
                db.commit()
                rep["complementos"].append(
                    f"{o.folio}: complemento 6921/2311 S/ {abs_res['monto']:.2f} "
                    f"({comp.numero})")
            else:
                res = contab._salida_venta_cta23_idempotente(db, o.id, None)
                rep["flujo_completo"].append(f"{o.folio}: flujo completo {res}")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(f"pedidos revisados: {rep['revisados']}")
    for x in rep["absorbidos"]:
        print("  =", x)
    for x in rep["complementos"]:
        print("  +", x)
    for x in rep["flujo_completo"]:
        print("  *", x)
    for x in rep["omitidos"]:
        print("  -", x)
    print("dry-run: sin cambios" if args.dry_run else "OK: cambios persistidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
