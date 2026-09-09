"""Lectura/escritura de configuración de sede."""
from sqlalchemy.orm import Session

from app.models.config import DEFAULTS, Configuracion


def get(db: Session, clave: str, default: str = "") -> str:
    row = db.get(Configuracion, clave)
    if row is None:
        return DEFAULTS.get(clave, default)
    return row.valor


def set(db: Session, clave: str, valor: str) -> None:
    row = db.get(Configuracion, clave)
    if row is None:
        db.add(Configuracion(clave=clave, valor=valor or ""))
    else:
        row.valor = valor or ""
    db.commit()


def seed_defaults(db: Session) -> None:
    for clave, valor in DEFAULTS.items():
        if not db.get(Configuracion, clave):
            db.add(Configuracion(clave=clave, valor=valor))
    db.commit()


def _f(db, clave: str, default: str) -> float:
    try:
        return float((get(db, clave, default) or default).strip() or default)
    except (TypeError, ValueError):
        return float(default)


def parametros_laborales(db) -> dict:
    """Parámetros de planilla/RxH desde Administración (nunca hardcoded)."""
    from app.models.config import DEFAULTS
    return {
        "asignacion_familiar": _f(db, "asignacion_familiar",
                                  DEFAULTS["asignacion_familiar"]),
        "essalud_pct": _f(db, "essalud_pct", DEFAULTS["essalud_pct"]),
        "retencion_4ta_pct": _f(db, "retencion_4ta_pct",
                                DEFAULTS["retencion_4ta_pct"]),
    }


def fondos_pension(db) -> list[dict]:
    """Fondos configurados: [{sistema, nombre, pct}]."""
    from app.models.config import FONDOS_PENSION
    out = []
    for sistema, nombre, clave in FONDOS_PENSION:
        from app.models.config import DEFAULTS
        out.append({"sistema": sistema, "nombre": nombre,
                    "pct": _f(db, clave, DEFAULTS[clave])})
    return out


def fondo_pct(db, sistema: str | None) -> float:
    """% del fondo del empleado (default ONP si no existe)."""
    for f in fondos_pension(db):
        if f["sistema"] == (sistema or "ONP"):
            return f["pct"]
    return fondos_pension(db)[0]["pct"]
