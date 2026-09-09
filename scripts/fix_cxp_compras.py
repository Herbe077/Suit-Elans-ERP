"""Nivelación idempotente de compras, gastos y CxP.

Uso: define la variable de conexión de base de datos y ejecuta el script.
    python scripts/fix_cxp_compras.py
    python scripts/fix_cxp_compras.py --dry-run

La nivelación corrige solo inconsistencias deterministas: espejos CxP de OCs,
actividad de flujo, fechas de vencimiento, saldos derivados y orígenes legacy.
No genera asientos duplicados y no imprime credenciales ni DATABASE_URL.
"""
import argparse
from datetime import timedelta
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Nivelación financiera Suit Elans")
    parser.add_argument("--dry-run", action="store_true", help="calcula cambios pero no los persiste")
    args = parser.parse_args()

    from app.core.database import Base, SessionLocal, engine
    import app.models  # noqa: F401
    from app.models.finanzas import CuentaPorPagar, GastoRegistrado
    from app.services import compras_kardex as ck
    from app.services import finanzas as fin
    from app.services import contabilidad
    from app.services.purchasing import purgar_proveedor_dummy

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    report = {"cxp_reparadas": 0, "gastos_nivelados": 0, "cxp_niveladas": 0,
              "saldos_corregidos": 0, "asientos_desequilibrados": [], "dummy": None}
    try:
        # 1) Espejos OC -> CxP + provisiones faltantes, idempotente.
        rep = ck.reparar_cxp_compras(db, commit=False)
        report["cxp_reparadas"] = rep["creadas"] + rep["actualizadas"]

        # 2) Completa los campos de tesorería en gastos existentes.
        for g in db.query(GastoRegistrado).all():
            changed = False
            if not getattr(g, "actividad_flujo", None) or g.actividad_flujo not in {"OPERATIVO", "INVERSION", "FINANCIAMIENTO"}:
                g.actividad_flujo = "INVERSION" if (g.categoria or "").upper() == "ACTIVO_FIJO" else "OPERATIVO"
                changed = True
            if g.fecha_vencimiento is None and g.fecha_emision:
                g.fecha_vencimiento = g.fecha_emision + timedelta(days=30)
                changed = True
            if changed:
                report["gastos_nivelados"] += 1

        # 3) Completa/normaliza CxP y recalcula campos derivados sin tocar pagos.
        for c in db.query(CuentaPorPagar).all():
            normalized = fin.normalizar_origen_cxp(c.origen_tipo)
            if c.origen_tipo != normalized:
                c.origen_tipo = normalized
                report["cxp_niveladas"] += 1
            if not c.actividad_flujo or c.actividad_flujo not in {"OPERATIVO", "INVERSION", "FINANCIAMIENTO"}:
                c.actividad_flujo = "INVERSION" if normalized == fin.ORIGEN_ACTIVOS else "OPERATIVO"
                report["cxp_niveladas"] += 1
            if c.fecha_vencimiento is None and c.fecha_emision:
                c.fecha_vencimiento = c.fecha_emision + timedelta(days=30)
                report["cxp_niveladas"] += 1
            saldo = max(float(c.monto_total or 0) - float(c.monto_pagado or 0) - float(c.retencion or 0), 0.0)
            if abs(float(c.saldo_pendiente or 0) - saldo) > 0.01:
                c.saldo_pendiente = round(saldo, 2)
                report["saldos_corregidos"] += 1
            if (c.tipo_comprobante or "").upper() == "GUIA_RECEPCION":
                c.estado = "POR_FACTURAR" if saldo > 0.01 else "PAGADO"
            elif saldo <= 0.01:
                c.estado = "PAGADO"
            elif float(c.monto_pagado or 0) > 0:
                c.estado = "PARCIAL"
            else:
                c.estado = "POR_PAGAR"

        # 4) CxP derivada de gastos que todavía no tiene espejo.
        contabilidad.sincronizar_cxp_desde_gastos(db, commit=False)

        # 5) Auditoría de partida doble: nunca corrige asientos automáticamente.
        from sqlalchemy import func
        from app.models.finanzas import AsientoContable, LineaAsientoContable
        rows = db.query(
            AsientoContable.id,
            func.coalesce(func.sum(LineaAsientoContable.debe), 0),
            func.coalesce(func.sum(LineaAsientoContable.haber), 0),
        ).join(LineaAsientoContable).group_by(AsientoContable.id).all()
        report["asientos_desequilibrados"] = [a.id for a in rows if abs(float(a[1]) - float(a[2])) > 0.005]

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
            report["dummy"] = purgar_proveedor_dummy(db)
        print("Nivelación Suit Elans:", report)
        return 2 if report["asientos_desequilibrados"] else 0
    except Exception as exc:
        db.rollback()
        print(f"Nivelación fallida: {type(exc).__name__}: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        print("Aviso: sin DATABASE_URL, se usa la BD local por defecto")
    sys.exit(main())
