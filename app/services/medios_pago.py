"""Medios de pago unificados del ERP (única fuente de verdad).

Enum estandarizado (mayúsculas): EFECTIVO, TRANSFERENCIA, TARJETA, YAPE_PLIN
(Yape y Plin comparten cuenta 1041). Cada medio mapea a UNA cuenta del plan:
EFECTIVO → 1011 Caja Operativa; el resto → 1041 Banco BCP.

`normalizar_medio()` acepta variantes legacy (minúsculas, espacios,
"banco", "caja"...) y las canoniza; lo desconocido lanza ValueError.
`cuenta_por_medio()` resuelve el código de cuenta (un override explícito
1011/1041/101/104 siempre gana).
"""
from __future__ import annotations

MEDIOS_PAGO: tuple[str, ...] = (
    "EFECTIVO", "TRANSFERENCIA", "TARJETA", "YAPE_PLIN",
)

MEDIO_CUENTA: dict[str, str] = {
    "EFECTIVO": "1011",
    "TRANSFERENCIA": "1041",
    "TARJETA": "1041",
    "YAPE_PLIN": "1041",
}

MEDIO_LABEL: dict[str, str] = {
    "EFECTIVO": "Efectivo → Caja Operativa (1011)",
    "TRANSFERENCIA": "Transferencia → Banco BCP (1041)",
    "TARJETA": "Tarjeta → Banco BCP (1041)",
    "YAPE_PLIN": "Yape / Plin → Banco BCP (1041)",
}

CUENTA_NOMBRE_CORTO: dict[str, str] = {
    "1011": "Caja Operativa",
    "1041": "Banco BCP",
}

# Aliases legacy (formularios viejos, APIs, data histórica) → enum.
_ALIAS: dict[str, str] = {
    "efectivo": "EFECTIVO", "cash": "EFECTIVO",
    "caja": "EFECTIVO", "caja chica": "EFECTIVO", "caja_chica": "EFECTIVO",
    "caja-chica": "EFECTIVO", "101": "EFECTIVO", "1011": "EFECTIVO",
    "transferencia": "TRANSFERENCIA", "transfer": "TRANSFERENCIA",
    "banco": "TRANSFERENCIA", "banco bcp": "TRANSFERENCIA",
    "banco_bcp": "TRANSFERENCIA", "banco-bcp": "TRANSFERENCIA",
    "bcp": "TRANSFERENCIA", "104": "TRANSFERENCIA", "1041": "TRANSFERENCIA",
    "tarjeta": "TARJETA", "card": "TARJETA",
    "tarjeta_credito": "TARJETA", "tarjeta_debito": "TARJETA",
    "yape": "YAPE_PLIN", "plin": "YAPE_PLIN",
    "yape_plin": "YAPE_PLIN", "yape-plin": "YAPE_PLIN",
    "yape/plin": "YAPE_PLIN",
}


def normalizar_medio(raw: str | None, default: str = "TRANSFERENCIA") -> str:
    """Canoniza cualquier variante al enum. None/"" → default.

    Lanza ValueError si el valor no es reconocible (nada de listas
    informales propagándose a la BD).
    """
    if raw is None or not str(raw).strip():
        return default
    key = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    if key in _ALIAS:
        return _ALIAS[key]
    up = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    if up in MEDIOS_PAGO:
        return up
    raise ValueError(
        f"Medio de pago inválido: {raw!r} (válidos: {', '.join(MEDIOS_PAGO)})")


def cuenta_por_medio(medio: str | None = None,
                     cuenta_codigo: str | None = None) -> str:
    """Código de cuenta ("1011"/"1041") para un medio (y override opcional)."""
    if (cuenta_codigo or "").strip() in ("1011", "1041", "101", "104"):
        cod = cuenta_codigo.strip()
        return {"101": "1011", "104": "1041"}.get(cod, cod)
    return MEDIO_CUENTA[normalizar_medio(medio)]


def es_efectivo_cuenta(codigo: str | None) -> bool:
    """True si el código de cuenta es caja (1011/101)."""
    return (codigo or "").strip() in ("1011", "101")


def etiqueta_cuenta(codigo: str | None) -> str:
    """Nombre corto operativo de la cuenta de tesorería."""
    return CUENTA_NOMBRE_CORTO.get((codigo or "").strip(), (codigo or "").strip())


def id_cuenta(db, codigo: str | None) -> int | None:
    """ID de la cuenta por código (None si no existe; el motor valida)."""
    if not (codigo or "").strip():
        return None
    from app.services.finanzas import get_cuenta_by_codigo
    c = get_cuenta_by_codigo(db, codigo.strip())
    return c.id if c else None
