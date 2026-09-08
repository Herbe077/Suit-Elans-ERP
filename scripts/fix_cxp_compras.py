"""Repara CxP faltantes de Órdenes de Compra (RECEIVED/BILLED).

Uso:
    DATABASE_URL="sqlite:///suitelans.db" .venv/bin/python scripts/fix_cxp_compras.py
    DATABASE_URL="postgresql://..." .venv/bin/python scripts/fix_cxp_compras.py

Recorre las OCs y asegura su espejo en cuentas_por_pagar (crea o actualiza
sin duplicar ni generar asientos). También purga el proveedor ficticio
'Personal Destajo' si ninguna tabla lo referencia. Nunca imprime la URL.
"""
import os
import sys


def main() -> int:
    from app.core.database import Base, SessionLocal, engine
    import app.models  # noqa: F401 — registra tablas
    from app.services import compras_kardex as ck
    from app.services.purchasing import purgar_proveedor_dummy

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        rep = ck.reparar_cxp_compras(db)
        print(f"CxP compras: {rep['creadas']} creadas, "
              f"{rep['actualizadas']} actualizadas, "
              f"{len(rep['omitidas'])} omitidas")
        for folio, motivo in rep["omitidas"]:
            print(f"  omitida {folio}: {motivo}")
        purga = purgar_proveedor_dummy(db)
        print(f"Dummy: {purga['eliminados']} eliminado(s), "
              f"{len(purga['conservados'])} conservado(s)")
        for c in purga["conservados"]:
            print(f"  conservado id={c['id']} usos={','.join(c['usos'])}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        print("Aviso: sin DATABASE_URL, se usa la BD local por defecto")
    sys.exit(main())
