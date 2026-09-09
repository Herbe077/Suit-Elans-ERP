"""Migra pedidos/cotizaciones B2B estancados en COTIZACION a EN_PRODUCCION.

Corporativo = Order.company_id vinculado, o Client con flag es_corporativo
o con company_id (colaborador de empresa).

Uso:
    python scripts/migrar_corporativos_produccion.py [--dry-run]

Idempotente: solo toca orders en 'cotizado' y espejos en 'COTIZACION'
corporativos. Aprobación vía ventas_svc.aprobar_produccion (reserva telas
para taller sin exigir adelanto).
"""
import sys

sys.path.insert(0, ".")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: F401,F402 — registra mappers  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.ventas import OrdenVenta  # noqa: E402
from app.services import ventas as ventas_svc  # noqa: E402


def _ensure_flag() -> None:
    """Crea clients.es_corporativo si la BD aún no corrió alembic (dev)."""
    from sqlalchemy import inspect, text
    with engine.begin() as conn:
        try:
            cols = {c["name"] for c in inspect(conn).get_columns("clients")}
        except Exception:
            return
        if "es_corporativo" not in cols:
            conn.execute(text("ALTER TABLE clients ADD COLUMN "
                              "es_corporativo BOOLEAN DEFAULT FALSE"))
            print("columna clients.es_corporativo creada (sin alembic)")


def migrar(dry_run: bool = False) -> dict:
    Base.metadata.create_all(bind=engine)
    _ensure_flag()
    db = SessionLocal()
    movidos, espejos, ops_creadas = [], 0, 0
    try:
        pendientes = db.query(Order).filter(
            Order.estado == "cotizado").all()
        for o in pendientes:
            try:
                corporativo = ventas_svc.es_pedido_corporativo(db, o)
            except Exception:
                corporativo = bool(o.company_id)
            if not corporativo:
                continue
            movidos.append(o.folio or f"#{o.id}")
            if not dry_run:
                ventas_svc.aprobar_produccion(db, o)
                ov = db.query(OrdenVenta).filter(
                    OrdenVenta.legacy_order_id == o.id).first()
                if not ov and o.folio:
                    ov = db.query(OrdenVenta).filter(
                        OrdenVenta.folio == o.folio).first()
                if ov:
                    ov.estado = "EN_PRODUCCION"
                    ov.total = o.total or 0
                    ov.monto_adelantado = o.anticipo or 0
                    ov.saldo_pendiente = round((o.total or 0) - (o.anticipo or 0), 2)
                    espejos += 1
        if not dry_run:
            db.commit()
        # Backfill: asegura espejo orden_produccion para todo pedido ya en
        # taller (confirmado/en_produccion/entregado) aunque se creó antes
        # del espejo automático (p. ej. SE-2026-0001 en Render).
        if not dry_run:
            try:
                taller = db.query(Order).filter(
                    Order.estado.in_(["confirmado", "en_produccion", "entregado"])).all()
                for o in taller:
                    try:
                        ops_creadas += ventas_svc._asegurar_orden_produccion(db, o)
                    except Exception:
                        pass
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        db.close()
    return {"pedidos_movidos": len(movidos), "espejos": espejos,
            "folios": movidos, "ops_creadas": ops_creadas}


if __name__ == "__main__":
    seco = "--dry-run" in sys.argv[1:]
    res = migrar(dry_run=seco)
    print(f"{'[DRY-RUN] ' if seco else ''}pedidos a EN_PRODUCCION: "
          f"{res['pedidos_movidos']} (espejos: {res['espejos']})")
    for f in res["folios"]:
        print(f"  - {f}")
