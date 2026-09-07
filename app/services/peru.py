"""Localización Perú: documentos de identidad, moneda y validaciones SUNAT básicas."""
from app.core.constants import MONEDA

DOC_TYPES_CLIENTE = ("DNI", "RUC", "CE", "PASAPORTE")
PESOS_RUC = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def validar_ruc(ruc: str) -> bool:
    """RUC de 11 dígitos con dígito verificador módulo 11 (SUNAT)."""
    if not ruc or len(ruc) != 11 or not ruc.isdigit():
        return False
    suma = sum(int(d) * p for d, p in zip(ruc[:10], PESOS_RUC))
    resto = suma % 11
    verif = 11 - resto
    if verif == 10:
        verif = 0
    elif verif == 11:
        verif = 1
    return verif == int(ruc[10])


def validar_dni(dni: str) -> bool:
    return bool(dni) and len(dni) == 8 and dni.isdigit()


def validar_doc(tipo: str, numero: str | None) -> None:
    """Lanza ValueError si el documento es inválido. Vacío = opcional, se acepta."""
    if not numero:
        return
    numero = numero.strip()
    if tipo == "RUC" and not validar_ruc(numero):
        raise ValueError(f"RUC inválido: {numero} (11 dígitos con verificador)")
    if tipo == "DNI" and not validar_dni(numero):
        raise ValueError(f"DNI inválido: {numero} (8 dígitos)")
    if tipo in ("CE", "PASAPORTE") and len(numero) < 6:
        raise ValueError(f"{tipo} inválido: {numero}")


def normalizar_ruc(ruc: str | None) -> str | None:
    """Normaliza un RUC sin bloquear: quita espacios, guiones y puntos.

    Devuelve el string normalizado (o None si vacío). Acepta cualquier
    formato/longitud para no frenar el guardado; la verificación SUNAT
    (11 dígitos + checksum) queda como advertencia no bloqueante.
    """
    if not ruc:
        return None
    norm = ruc.strip().replace(" ", "").replace("-", "").replace(".", "")
    return norm or None


def soles(monto: float) -> str:
    return f"{MONEDA} {monto:,.2f}"
