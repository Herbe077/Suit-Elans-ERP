"""Motor contable — Plan Operativo con reglas y dimensiones analíticas.

Toda contabilización productiva pasa por `post_regla()`: resuelve cuentas
desde `regla_contable`, valida imputabilidad/dimensiones y crea el asiento
(flush; el llamante hace commit). `post_manual()` cubre asientos libres
(apertura, manuales, extornos espejo) con validación de imputabilidad.

Patrones: "4212" exacto | "602x" única hoja imputable bajo el prefijo,
múltiples líneas con "+". Montos alineados por posición a los tokens.
"""
from datetime import date as _date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.finanzas import (
    AsientoContable,
    CuentaContable,
    ReglaContable,
)

DIMENSIONES = (
    "centro_costo_id", "orden_produccion_id", "producto_id",
    "trabajador_id", "almacen_id", "cliente_id", "proveedor_id",
    "activo_fijo_id", "proyecto_id",
)


def dim_centro(db: Session, codigo: str) -> int:
    """ID del centro de costo por código (raise si no existe)."""
    from app.models.finanzas import CentroCosto
    cc = db.query(CentroCosto).filter(CentroCosto.codigo == codigo).first()
    if not cc:
        raise ValueError(f"Centro de costo {codigo} inexistente")
    return cc.id


def dim_almacen(db: Session, codigo: str = "ALM-01") -> int:
    """ID del almacén por código (raise si no existe)."""
    from app.models.finanzas import Almacen
    a = db.query(Almacen).filter(Almacen.codigo == codigo).first()
    if not a:
        raise ValueError(f"Almacén {codigo} inexistente")
    return a.id


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def get_regla(db: Session, codigo_regla: str) -> ReglaContable:
    r = db.query(ReglaContable).filter(
        ReglaContable.codigo_regla == (codigo_regla or "").upper()).first()
    if not r or not r.activa:
        raise ValueError(f"Regla contable inexistente o inactiva: {codigo_regla}")
    return r


def expandir_patron(db: Session, patron: str) -> CuentaContable:
    """Un patrón -> UNA cuenta imputable y activa (exacta o único prefijo-x)."""
    patron = (patron or "").strip()
    if not patron:
        raise ValueError("Patrón de cuenta vacío")
    if patron.endswith("x"):
        pref = patron[:-1]
        cands = db.query(CuentaContable).filter(
            CuentaContable.codigo.startswith(pref),
            CuentaContable.codigo != pref,
            CuentaContable.imputable.is_(True),
            CuentaContable.activo.is_(True)).all()
        # La propia hoja también califica (p.ej. 2311x -> 2311).
        propia = db.query(CuentaContable).filter(
            CuentaContable.codigo == pref,
            CuentaContable.imputable.is_(True),
            CuentaContable.activo.is_(True)).first()
        if propia:
            cands = [propia] + [c for c in cands if c.id != propia.id]
        if len(cands) != 1:
            raise ValueError(
                f"Patrón {patron}: {len(cands)} hojas imputables (se exige 1)")
        return cands[0]
    c = db.query(CuentaContable).filter(
        CuentaContable.codigo == patron).first()
    if not c:
        raise ValueError(f"Cuenta {patron} inexistente en el plan operativo")
    return c


def _exigir_imputable(cuenta: CuentaContable) -> None:
    if not cuenta.activo:
        raise ValueError(f"Cuenta {cuenta.codigo} inactiva")
    if not cuenta.acepta_movimientos:
        raise ValueError(f"Cuenta {cuenta.codigo} no acepta movimientos")
    if not cuenta.imputable:
        raise ValueError(
            f"Cuenta {cuenta.codigo} no imputable (solo hojas reciben asientos)")


def _normalizar_dims(dims: dict | None) -> dict:
    dims = dict(dims or {})
    return {k: dims.get(k) for k in DIMENSIONES if dims.get(k) is not None}


def validar_dimensiones_obligatorias(obligatorias: list | None,
                                     dims: dict) -> None:
    for d in obligatorias or []:
        if dims.get(d) is None:
            raise ValueError(f"Dimensión obligatoria sin valor: {d}")


def post_regla(db: Session, codigo_regla: str,
               montos_debe: list, montos_haber: list,
               dims: dict | None = None, glosa: str = "",
               origen_tipo: str = "MANUAL", origen_id: int | None = None,
               fecha=None) -> AsientoContable:
    """Contabiliza por regla (flush, sin commit).

    Resuelve patrones, valida imputabilidad + dimensiones obligatorias y
    exige cuadre. Montos en 0 omiten la pata (IGV/anticipos opcionales);
    negativos lanzan error. Las dims se escriben en las líneas de cuentas
    analíticas; la validación de obligatorias es global al asiento.
    """
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.finanzas import crear_asiento_flush

    fecha = fecha or _date.today()
    exigir_periodo_abierto(db, fecha)
    regla = get_regla(db, codigo_regla)
    tokens_debe = [t.strip() for t in (regla.debe_patron or "").split("+") if t.strip()]
    tokens_haber = [t.strip() for t in (regla.haber_patron or "").split("+") if t.strip()]
    if len(tokens_debe) != len(list(montos_debe or [])):
        raise ValueError(
            f"{codigo_regla}: {len(tokens_debe)} líneas al debe, "
            f"{len(list(montos_debe or []))} montos")
    if len(tokens_haber) != len(list(montos_haber or [])):
        raise ValueError(
            f"{codigo_regla}: {len(tokens_haber)} líneas al haber, "
            f"{len(list(montos_haber or []))} montos")
    dims_n = _normalizar_dims(dims)
    validar_dimensiones_obligatorias(regla.dimensiones_obligatorias, dims_n)

    lineas: list[dict] = []
    for tok, m in zip(tokens_debe, montos_debe):
        monto = _d(m)
        if monto < 0:
            raise ValueError(f"{codigo_regla}: monto al debe inválido ({m})")
        if monto == 0:
            continue  # pata opcional (IGV, aplicación de anticipos...)
        cta = expandir_patron(db, tok)
        _exigir_imputable(cta)
        linea = {"cuenta_id": cta.id, "debe": monto, "haber": Decimal("0")}
        if cta.analitica:
            linea.update(dims_n)
        lineas.append(linea)
    for tok, m in zip(tokens_haber, montos_haber):
        monto = _d(m)
        if monto < 0:
            raise ValueError(f"{codigo_regla}: monto al haber inválido ({m})")
        if monto == 0:
            continue
        cta = expandir_patron(db, tok)
        _exigir_imputable(cta)
        linea = {"cuenta_id": cta.id, "debe": Decimal("0"), "haber": monto}
        if cta.analitica:
            linea.update(dims_n)
        lineas.append(linea)
    if len(lineas) < 2:
        raise ValueError(f"{codigo_regla}: el asiento requiere al menos 2 líneas")
    total_debe = sum((l["debe"] for l in lineas), Decimal("0"))
    total_haber = sum((l["haber"] for l in lineas), Decimal("0"))
    if total_debe != total_haber:
        raise ValueError(
            f"{codigo_regla} descuadra: DEBE {total_debe} != HABER {total_haber}")
    return crear_asiento_flush(db, fecha, glosa or codigo_regla, origen_tipo,
                               origen_id, lineas)


def post_regla_extra(db: Session, codigo_regla: str,
                     montos_debe: list, montos_haber: list,
                     extra_haber: list[tuple[str, object]] | None = None,
                     extra_debe: list[tuple[str, object]] | None = None,
                     dims: dict | None = None, glosa: str = "",
                     origen_tipo: str = "MANUAL",
                     origen_id: int | None = None,
                     fecha=None) -> AsientoContable:
    """post_regla + patas adicionales por código exacto (p.ej. retención
    40172 en RxH/gastos). Las extras también validan imputabilidad y
    reciben dims si la cuenta es analítica."""
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.finanzas import crear_asiento_flush

    fecha = fecha or _date.today()
    exigir_periodo_abierto(db, fecha)
    regla = get_regla(db, codigo_regla)
    dims_n = _normalizar_dims(dims)
    validar_dimensiones_obligatorias(regla.dimensiones_obligatorias, dims_n)

    def _legs(tokens: list[str], montos: list, al_debe: bool,
              extras: list[tuple[str, object]] | None) -> list[dict]:
        if len(tokens) != len(list(montos or [])):
            raise ValueError(f"{codigo_regla}: aridad de montos inválida")
        out: list[dict] = []
        pares = list(zip(tokens, montos)) + [
            (c, m) for c, m in (extras or [])]
        for tok, m in pares:
            monto = _d(m)
            if monto < 0:
                raise ValueError(f"{codigo_regla}: monto inválido ({m})")
            if monto == 0:
                continue
            cta = expandir_patron(db, tok.strip())
            _exigir_imputable(cta)
            linea = {"cuenta_id": cta.id,
                     "debe": monto if al_debe else Decimal("0"),
                     "haber": Decimal("0") if al_debe else monto}
            if cta.analitica:
                linea.update(dims_n)
            out.append(linea)
        return out

    tokens_debe = [t.strip() for t in (regla.debe_patron or "").split("+") if t.strip()]
    tokens_haber = [t.strip() for t in (regla.haber_patron or "").split("+") if t.strip()]
    lineas = (_legs(tokens_debe, montos_debe, True, extra_debe)
              + _legs(tokens_haber, montos_haber, False, extra_haber))
    if len(lineas) < 2:
        raise ValueError(f"{codigo_regla}: el asiento requiere al menos 2 líneas")
    total_debe = sum((l["debe"] for l in lineas), Decimal("0"))
    total_haber = sum((l["haber"] for l in lineas), Decimal("0"))
    if total_debe != total_haber:
        raise ValueError(
            f"{codigo_regla} descuadra: DEBE {total_debe} != HABER {total_haber}")
    return crear_asiento_flush(db, fecha, glosa or codigo_regla, origen_tipo,
                               origen_id, lineas)


def post_manual(db: Session, items_debe: list[tuple[str, object]],
                items_haber: list[tuple[str, object]],
                dims: dict | None = None, glosa: str = "",
                origen_tipo: str = "MANUAL", origen_id: int | None = None,
                fecha=None) -> AsientoContable:
    """Asiento libre por códigos exactos (apertura, manuales, extornos).

    Valida imputabilidad/actividad de cada cuenta y cuadre. Sin dimensiones
    obligatorias (no hay regla); las dims dadas se escriben en analíticas.
    items: [(codigo_cuenta, monto)].
    """
    from app.services.contabilidad import exigir_periodo_abierto
    from app.services.finanzas import crear_asiento_flush

    fecha = fecha or _date.today()
    exigir_periodo_abierto(db, fecha)
    dims_n = _normalizar_dims(dims)
    lineas: list[dict] = []
    for codigo, m in items_debe or []:
        monto = _d(m)
        if monto <= 0:
            raise ValueError(f"Monto al debe inválido en {codigo}")
        cta = expandir_patron(db, codigo)
        _exigir_imputable(cta)
        linea = {"cuenta_id": cta.id, "debe": monto, "haber": Decimal("0")}
        if cta.analitica:
            linea.update(dims_n)
        lineas.append(linea)
    for codigo, m in items_haber or []:
        monto = _d(m)
        if monto <= 0:
            raise ValueError(f"Monto al haber inválido en {codigo}")
        cta = expandir_patron(db, codigo)
        _exigir_imputable(cta)
        linea = {"cuenta_id": cta.id, "debe": Decimal("0"), "haber": monto}
        if cta.analitica:
            linea.update(dims_n)
        lineas.append(linea)
    if len(lineas) < 2:
        raise ValueError("Asiento requiere al menos 2 líneas")
    total_debe = sum((l["debe"] for l in lineas), Decimal("0"))
    total_haber = sum((l["haber"] for l in lineas), Decimal("0"))
    if total_debe != total_haber:
        raise ValueError(
            f"Asiento descuadrado: DEBE {total_debe} != HABER {total_haber}")
    return crear_asiento_flush(db, fecha, glosa or "Asiento manual",
                               origen_tipo, origen_id, lineas)
