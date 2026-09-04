"""Siembra PCGE detallado (analíticas de negocio). Equivalente adaptado al modelo real.

Uso:
    .venv/bin/python seed_pcge.py
Idempotente: no duplica, rellena elemento/es_analitica si faltan.
"""
from app.core.database import Base, SessionLocal, engine
from app.services.finanzas import PCGE_ANALITICAS, seed_pcge_basico


def seed_pcge() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.models.finanzas import CuentaContable

        count_total = db.query(CuentaContable).count()
        seed_pcge_basico(db)
        count_nuevo = db.query(CuentaContable).count()
        creadas = count_nuevo - count_total
        print(f"✅ PCGE verificado. {creadas} cuentas nuevas (total {count_nuevo}).")
        # detalle analíticas
        for codigo, *_ in PCGE_ANALITICAS:
            c = db.query(CuentaContable).filter(CuentaContable.codigo == codigo).first()
            print(f"  - {codigo}: {'OK' if c else 'FALTA'} {c.nombre if c else ''}")
        return creadas
    except Exception as e:
        db.rollback()
        print(f"❌ Error al sembrar PCGE: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_pcge()
