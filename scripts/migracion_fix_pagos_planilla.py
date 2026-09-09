"""Data fix histórico: pagos de planilla 4212 → 4111 + flujo Sept 2026.

Corrige asientos PAGO que debitaron 4212 (Proveedores) cuando el pasivo
real es 4111 (Remuneraciones por pagar) u otro pasivo provisionado, y
re-procesa categorías/descripciones de EGRESOS del flujo de caja
(p.ej. 'Compra de Telas' genérico → 'Pago de Planilla / Personal').

Uso (SQLite local):
    .venv/bin/python scripts/migracion_fix_pagos_planilla.py --dry-run
    .venv/bin/python scripts/migracion_fix_pagos_planilla.py --db sqlite:///./suit_elans_dev.db

Uso (producción PostgreSQL/Neon, p.ej. Render Shell):
    NEON_DATABASE_URL="postgresql://..." \
      .venv/bin/python scripts/migracion_fix_pagos_planilla.py \
      --db "$NEON_DATABASE_URL" --dry-run
    NEON_DATABASE_URL="postgresql://..." \
      .venv/bin/python scripts/migracion_fix_pagos_planilla.py \
      --db "$NEON_DATABASE_URL" --apply

- En SQLite hace backup del archivo antes de escribir.
- En PostgreSQL solo escribe con --apply explícito (sin --apply es dry-run).
- Idempotente: re-ejecutar no duplica ni re-cambia nada ya corregido.
- No crea asientos nuevos: solo reasigna la cuenta del DEBE del pago y
  re-etiqueta movimientos (los montos y el cuadre no cambian).
"""
import argparse
import os
import shutil
import sys
from datetime import datetime


def _db_path_from_url(url: str) -> str | None:
    if url.startswith("sqlite:///"):
        p = url[len("sqlite:///"):]
        return p if os.path.isabs(p) else os.path.join(os.getcwd(), p)
    return None


def _resolver_pago(db, asiento):
    """Resuelve (gasto|None, cxp|None, via) para un asiento origen PAGO."""
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    glosa = asiento.glosa or ""
    cand_gasto = db.get(GastoRegistrado, asiento.origen_id)
    cand_cxp = db.get(CuentaPorPagar, asiento.origen_id)
    if cand_gasto is not None and (cand_gasto.numero_comprobante or "") \
            and f"gasto {cand_gasto.numero_comprobante}" in glosa:
        return cand_gasto, None, "gasto"
    if cand_cxp is not None and (cand_cxp.numero_factura or "") \
            and cand_cxp.numero_factura in glosa:
        return None, cand_cxp, "cxp"
    if cand_gasto is not None:
        return cand_gasto, None, "gasto"
    if cand_cxp is not None:
        return None, cand_cxp, "cxp"
    return None, None, "desconocido"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fix pagos planilla 4212→4111 + flujo")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", "sqlite:///./suit_elans_dev.db"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true",
                        help="requerido para persistir en PostgreSQL")
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
    from app.models.billing import CashMovement
    from app.models.finanzas import (
        AsientoContable,
        CuentaPorPagar,
        GastoRegistrado,
        LineaAsientoContable,
        MovimientoFinanciero,
    )
    from app.services import contabilidad as contab
    from app.services import finanzas as fin
    from app.services import tesoreria_service as tes

    db_path = _db_path_from_url(args.db)
    if not args.dry_run and db_path and os.path.isfile(db_path):
        bak = f"{db_path}.bak-migracion-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(db_path, bak)
        print(f"backup: {bak}")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    rep = {"asientos_revisados": 0, "lineas_4212_a_4111": [],
           "flujo_recategorizados": [], "cash_actualizados": [],
           "omitidos": []}
    try:
        pagos = db.query(AsientoContable).filter(
            AsientoContable.origen_tipo == "PAGO").order_by(AsientoContable.id).all()
        for a in pagos:
            rep["asientos_revisados"] += 1
            gasto, cxp, via = _resolver_pago(db, a)
            if via == "gasto":
                c_prov = tes.cuenta_pasivo_gasto(db, gasto)
                esperado = c_prov.codigo if c_prov is not None else fin.pasivo_por_categoria(gasto.categoria)
            elif via == "cxp":
                esperado, _cat = contab._pasivo_para_cxp(db, cxp)
            else:
                rep["omitidos"].append(f"{a.numero}: sin gasto/CxP (origen_id={a.origen_id})")
                continue
            for l in db.query(LineaAsientoContable).filter(
                    LineaAsientoContable.asiento_id == a.id).all():
                c = db.get(fin.CuentaContable, l.cuenta_id)
                if c is None or c.codigo != "4212" or float(l.debe or 0) <= 0:
                    continue
                if esperado == "4212":
                    continue
                dest = fin.get_cuenta_by_codigo(db, esperado)
                if dest is None:
                    rep["omitidos"].append(f"{a.numero}: cuenta {esperado} inexistente")
                    continue
                rep["lineas_4212_a_4111"].append(
                    f"{a.numero} {a.glosa}: 4212 → {esperado} S/ {float(l.debe):.2f}")
                if not args.dry_run:
                    l.cuenta_id = dest.id
            db.flush()

        # Flujo: EGRESOS (alcance: todos; el reporte destaca Sept 2026).
        movs = db.query(MovimientoFinanciero).filter(
            MovimientoFinanciero.tipo == "EGRESO").order_by(MovimientoFinanciero.id).all()
        for m in movs:
            ref = (m.comprobante_ref or "").strip()
            if not ref:
                continue
            gasto = db.query(GastoRegistrado).filter(
                GastoRegistrado.numero_comprobante == ref).first()
            cxp = db.query(CuentaPorPagar).filter(
                CuentaPorPagar.numero_factura == ref).first()
            # La vía real la define el asiento PAGO que contiene la ref.
            pago = db.query(AsientoContable).filter(
                AsientoContable.origen_tipo == "PAGO",
                AsientoContable.glosa.contains(ref)).order_by(AsientoContable.id).first()
            via = None
            if pago is not None:
                _g, _c, via = _resolver_pago(db, pago)
            if via == "gasto" and gasto is not None:
                cat = fin.categoria_flujo_pago(gasto.categoria, None, ref)
                desc = fin.descripcion_flujo_pago(
                    cat, f"{gasto.categoria} {ref}".strip(), None)
            else:
                cat = fin.categoria_flujo_pago(
                    gasto.categoria if gasto is not None else None,
                    cxp.origen_tipo if cxp is not None else None, ref)
                desc = fin.descripcion_flujo_pago(cat, f"CxP {ref}", None)
            # Conserva voucher histórico si la descripción vieja lo traía.
            old = m.descripcion or ""
            if "V:" in old and "V:" not in desc:
                desc += old[old.index("V:") - 1:] if old[old.index("V:") - 1] == " " else " " + old[old.index("V:"):]
                desc = desc.strip()
            if (m.categoria or "") != cat or (m.descripcion or "") != desc:
                rep["flujo_recategorizados"].append(
                    f"mov#{m.id} {ref}: {m.categoria!r} → {cat!r} | {desc!r}")
                if not args.dry_run:
                    m.categoria = cat
                    m.descripcion = desc
            # Cash espejo: match inequívoco por (egreso, monto, fecha).
            cash = db.query(CashMovement).filter(
                CashMovement.tipo == "egreso",
                CashMovement.monto == float(m.monto or 0)).all()
            cash = [k for k in cash
                    if str(getattr(k, "created_at", "") or "")[:10] == str(m.fecha or "")[:10]]
            nuevo_concepto = desc
            if len(cash) == 1 and (cash[0].concepto or "") != nuevo_concepto:
                rep["cash_actualizados"].append(
                    f"cash#{cash[0].id}: {cash[0].concepto!r} → {nuevo_concepto!r}")
                if not args.dry_run:
                    cash[0].concepto = nuevo_concepto
        # Cuadre: todo PAGO debe seguir balanceado.
        for a in pagos:
            ls = db.query(LineaAsientoContable).filter(
                LineaAsientoContable.asiento_id == a.id).all()
            debe = sum(float(l.debe or 0) for l in ls)
            haber = sum(float(l.haber or 0) for l in ls)
            if abs(debe - haber) > 0.01 or debe <= 0:
                rep["omitidos"].append(f"{a.numero}: DESEQUILIBRADO debe={debe} haber={haber}")
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"asientos PAGO revisados: {rep['asientos_revisados']}")
    print(f"líneas 4212→pasivo real: {len(rep['lineas_4212_a_4111'])}")
    for x in rep["lineas_4212_a_4111"]:
        print("  =", x)
    print(f"flujo recategorizados: {len(rep['flujo_recategorizados'])}")
    for x in rep["flujo_recategorizados"]:
        print("  =", x)
    print(f"cash actualizados: {len(rep['cash_actualizados'])}")
    for x in rep["cash_actualizados"]:
        print("  =", x)
    if rep["omitidos"]:
        print("omitidos/alertas:")
        for x in rep["omitidos"]:
            print("  !", x)
    print("dry-run: sin cambios" if args.dry_run else "OK: cambios persistidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
