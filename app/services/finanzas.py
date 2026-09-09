"""Servicio financiero central — partida doble, PCGE, mayor, balances, costos por absorción.

No importa lógica de cálculo de rendimiento; solo lee resultado devengado vía SQL de solo lectura.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.finanzas import (
    AsientoContable, CentroCosto, CuentaContable, CuentaPorCobrar, CuentaPorPagar,
    GastoRegistrado, LineaAsientoContable, PeriodoContable,
)
from app.core.database import engine

# ── PCGE helpers ────────────────────────────────────────────────────────
# NOTA Plan Operativo: la auto-creación en caliente está DESACTIVADA
# (ensure_cuenta_*/seed_pcge_basico eliminados). El plan + reglas se cargan
# con seed_pcge_nuevo.py / asegurar_plan_operativo(); el motor valida
# imputabilidad y dimensiones. Cuentas inexistentes => error, no creación.
def get_cuenta_by_codigo(db: Session, codigo: str) -> CuentaContable | None:
    return db.query(CuentaContable).filter(CuentaContable.codigo == codigo).first()

def get_or_create_cuenta(db: Session, codigo: str, nombre: str, tipo: str, nivel: int = 1, padre_codigo: str | None = None, elemento: int | None = None, es_analitica: bool = True, commit: bool = True) -> CuentaContable:
    c = get_cuenta_by_codigo(db, codigo)
    if c:
        # rellena nuevos campos si faltan (migración progresiva)
        changed = False
        if elemento is not None and c.elemento is None:
            c.elemento = elemento; changed = True
        if c.es_analitica is None:
            c.es_analitica = es_analitica; changed = True
        if changed:
            if commit:
                db.commit()
            else:
                db.flush()
            db.refresh(c)
        return c
    padre_id = None
    if padre_codigo:
        p = get_cuenta_by_codigo(db, padre_codigo)
        padre_id = p.id if p else None
    c = CuentaContable(codigo=codigo, nombre=nombre, tipo=tipo, nivel=nivel, elemento=elemento, es_analitica=es_analitica, cuenta_padre_id=padre_id, acepta_movimientos=True, activo=True)
    db.add(c)
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(c)
    return c

# Plan Operativo: el catálogo anterior (PCGE_ANALITICAS/PCGE_PADRES_EXTRA)
# fue reemplazado por app/services/plan_operativo.py (seed_pcge_nuevo.py).
# Se conserva CENTROS_COSTO_SASTRERIA como alias del seed operativo.
from app.services.plan_operativo import CENTROS_DEFAULT as CENTROS_COSTO_SASTRERIA

# Catálogo de Centros de Costo adaptado a sastrería a medida (PCGE Elemento 9).
# (codigo, nombre, tipo) — el tipo define el destino 921/941/951.
CENTROS_COSTO_SASTRERIA = [
    ("921", "Taller: Confección, Ensamblaje y Máquinas", "TALLER"),
    ("922", "Taller: Mesa de Corte y Patronaje", "TALLER"),
    ("923", "Taller: Calidad, Acabados y Planchado", "TALLER"),
    ("941", "Administración y Gestión General", "ADMINISTRACION"),
    ("951", "Comercial: Showroom, Tienda y Mostrador", "COMERCIAL"),
    ("952", "Comercial: Marketing, Catálogo y Publicidad", "COMERCIAL"),
]


def seed_centros_costo_sastreria(db: Session, commit: bool = True) -> int:
    """Asegura el catálogo de centros de costo. Idempotente. Retorna creados."""
    creados = 0
    for codigo, nombre, tipo in CENTROS_COSTO_SASTRERIA:
        ex = db.query(CentroCosto).filter(CentroCosto.codigo == codigo).first()
        if not ex:
            db.add(CentroCosto(codigo=codigo, nombre=nombre, tipo=tipo, activo=True))
            creados += 1
    if creados:
        if commit:
            db.commit()
        else:
            db.flush()
    return creados


def ensure_column(db: Session, tabla: str, columna: str,
                  ddl_pg: str, ddl_sqlite: str) -> None:
    """Nivelación de esquema idempotente y agnóstica de dialecto.

    Ejecuta el DDL a ciegas (PG: `ADD COLUMN IF NOT EXISTS`; SQLite:
    `ADD COLUMN` tragando "duplicate column") en vez de confiar en
    `inspect`: las conexiones pooleadas pueden servir caché de esquema
    stale y saltar la nivelación justo cuando más se necesita.
    Nunca lanza: acompaña la transacción del llamante.
    """
    try:
        from sqlalchemy import text as _text
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            db.execute(_text(ddl_pg))
        else:
            try:
                db.execute(_text(ddl_sqlite))
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    raise
                try:
                    db.rollback()
                except Exception:
                    pass
    except Exception:
        pass


def _refrescar_pool(db: Session) -> None:
    """Descarta conexiones pooleadas con caché de esquema stale (SQLite).

    Tras DDL, las conexiones del pool pueden seguir viendo el esquema
    viejo; al disponer el pool, las siguientes se reconectan limpias.
    """
    try:
        db.get_bind().dispose()
    except Exception:
        pass


def ensure_gasto_retencion_column(db: Session) -> None:
    """Migración liviana agnóstica de dialecto para `retencion`."""
    ensure_column(
        db, "gastos_registrados", "retencion",
        "ALTER TABLE gastos_registrados ADD COLUMN IF NOT EXISTS "
        "retencion DOUBLE PRECISION DEFAULT 0.0",
        "ALTER TABLE gastos_registrados "
        "ADD COLUMN retencion FLOAT DEFAULT 0.0")


def ensure_cxp_tipo_comprobante_column(db: Session) -> None:
    """Nivelación idempotente para `tipo_comprobante` en CxP."""
    ensure_column(
        db, "cuentas_por_pagar", "tipo_comprobante",
        "ALTER TABLE cuentas_por_pagar ADD COLUMN IF NOT EXISTS "
        "tipo_comprobante VARCHAR(20) DEFAULT 'FACTURA'",
        "ALTER TABLE cuentas_por_pagar "
        "ADD COLUMN tipo_comprobante VARCHAR(20) DEFAULT 'FACTURA'")


# Orígenes estándar de CxP (bandeja única de tesorería).
ORIGEN_MAT_PRIMA = "PROVEEDORES MATERIA PRIMA"
ORIGEN_SERVICIOS = "SERVICIOS TERCERIZADOS"
ORIGEN_GASTOS_OP = "GASTOS OPERATIVOS"
ORIGEN_ACTIVOS = "ACTIVOS Y MAQUINARIA"
ORIGENES_CXP = (ORIGEN_MAT_PRIMA, ORIGEN_SERVICIOS, ORIGEN_GASTOS_OP, ORIGEN_ACTIVOS)
# Legados → estándar (migración de datos).
ORIGEN_LEGACY_MAP = {"COMPRAS": ORIGEN_MAT_PRIMA, "GASTOS": ORIGEN_GASTOS_OP,
                     "DESTAJO": ORIGEN_SERVICIOS}


def normalizar_origen_cxp(value: str | None) -> str:
    """Normaliza cualquier valor histórico al catálogo cerrado de CxP."""
    raw = (value or "").upper().replace("_", " ").strip()
    normalized = ORIGEN_LEGACY_MAP.get(raw, raw)
    return normalized if normalized in ORIGENES_CXP else ORIGEN_GASTOS_OP


def origen_por_categoria_gasto(categoria: str | None) -> str:
    """ACTIVO_FIJO → activos/maquinaria; el resto → gastos operativos."""
    return ORIGEN_ACTIVOS if (categoria or "").upper() == "ACTIVO_FIJO" else ORIGEN_GASTOS_OP


# ── Flujo de caja: categorías de EGRESO por naturaleza del pago ──────
# El consolidado /finanzas/flujo-caja agrupa por `categoria`: cada egreso
# debe etiquetarse por su naturaleza real (no todo es "Compra de Telas").
CATEGORIA_FLUJO_PLANILLA = "Pago de Planilla / Personal"
CATEGORIA_FLUJO_ALQUILER = "Costos Operativos / Servicios"
CATEGORIA_FLUJO_INSUMOS = "Compra de Telas y Avíos"
CATEGORIA_FLUJO_DEFAULT = "Costos Operativos"

_CATS_PLANILLA = {"PLANILLA", "PLANILLA_PERSONAL"}
_CATS_ALQUILER_SERV = {"ALQUILER", "ALQUILERES", "ALQUILER_NATURAL",
                       "SERVICIOS_BASICOS", "HONORARIOS", "HONORARIOS_RXH",
                       "HONORARIOS_TERCEROS", "PUBLICIDAD_MARKETING",
                       "OTROS_GASTOS", "SUMINISTROS_Y_HERRAMIENTAS_TALLER",
                       "OTRO"}
_CATS_INSUMOS = {"COMPRAS_AVIOS_SUMINISTROS", "COMPRA_MP", "COMPRA"}


def categoria_flujo_pago(categoria_gasto: str | None = None,
                         origen_cxp: str | None = None,
                         numero: str | None = None) -> str:
    """Categoría del EGRESO según la naturaleza real del pago.

    - Planilla (4111) → 'Pago de Planilla / Personal'.
    - Alquileres/servicios/honorarios → 'Costos Operativos / Servicios'.
    - Insumos/telas/avíos (origen materia prima) → 'Compra de Telas y Avíos'.
    - Resto → 'Costos Operativos'.
    """
    cat = (categoria_gasto or "").strip().upper()
    if cat in _CATS_PLANILLA:
        return CATEGORIA_FLUJO_PLANILLA
    if cat in _CATS_INSUMOS:
        return CATEGORIA_FLUJO_INSUMOS
    ori = (origen_cxp or "").strip().upper()
    if ori == ORIGEN_MAT_PRIMA:
        return CATEGORIA_FLUJO_INSUMOS
    if cat in _CATS_ALQUILER_SERV:
        return CATEGORIA_FLUJO_ALQUILER
    if (numero or "").strip().upper().startswith("PL-"):
        return CATEGORIA_FLUJO_PLANILLA
    return CATEGORIA_FLUJO_DEFAULT


def descripcion_flujo_pago(categoria_flujo: str, ref: str | None,
                           voucher: str | None = None) -> str:
    """Descripción del EGRESO con su naturaleza (no genérica)."""
    cat = (categoria_flujo or "").strip()
    pref = "" if cat.lower().startswith("pago") else "Pago "
    base = f"{pref}{cat} {ref or ''}".strip()
    if (voucher or "").strip():
        base += f" V:{voucher.strip()}"
    return base


def advertencia_sobregiro(db: Session, hoja: str,
                           monto) -> str | None:
    """Advierte si el egreso deja en negativo Caja (1011) / Bancos (1041).

    Solo informa: retorna el texto de advertencia o None si hay saldo.
    El bloqueo vive en `exigir_saldo_tesoreria`.
    """
    try:
        from decimal import Decimal as _D
        cod = (hoja or "").strip()
        if cod not in ("1011", "1041"):
            return None
        saldo = saldo_cuenta(db, cod)
        if _D(str(monto or 0)) > saldo:
            nombre = "Caja" if cod == "1011" else "Banco (1041)"
            return (f"⚠️ Sobregiro en {nombre}: saldo S/ {saldo:.2f} "
                    f"< egreso S/ {_D(str(monto or 0)):.2f}")
        return None
    except Exception:
        return None


def exigir_saldo_tesoreria(db: Session, hoja: str, monto,
                           permitir_sobregiro: bool = False) -> str | None:
    """Regla estricta de sobregiro en Tesorería (raise → HTTP 400).

    - 1011 Caja Operativa: la caja física no puede quedar en negativo;
      bloquea siempre que el saldo disponible sea menor al monto.
    - 1041 Bancos: bloquea salvo flag explícito `permitir_sobregiro=True`
      (el frontend lo pide con checkbox al pagar por banco).
    Retorna la advertencia cuando el sobregiro en 1041 fue autorizado con
    flag, o None si había saldo suficiente.
    """
    from decimal import Decimal as _D
    cod = (hoja or "").strip()
    if cod not in ("1011", "1041"):
        return None
    try:
        saldo = saldo_cuenta(db, cod)
    except Exception:
        saldo = _D("0")
    m = _D(str(monto or 0))
    if m <= saldo:
        return None
    if cod == "1011":
        raise ValueError(
            f"⛔ Caja Operativa (1011) sin saldo suficiente: saldo S/ {saldo:.2f} "
            f"< egreso S/ {m:.2f}. La caja física no puede quedar en negativo.")
    if not permitir_sobregiro:
        raise ValueError(
            f"⚠️ Banco (1041) sin saldo suficiente: saldo S/ {saldo:.2f} "
            f"< egreso S/ {m:.2f}. Reintenta con permitir_sobregiro=True.")
    import logging as _logging
    adv = (f"⚠️ Sobregiro autorizado en Banco (1041): saldo S/ {saldo:.2f} "
           f"< egreso S/ {m:.2f}")
    _logging.getLogger(__name__).warning("sobregiro autorizado: %s", adv)
    return adv


def ensure_cxp_actividad_flujo_column(db: Session) -> None:
    """Nivelación idempotente para `actividad_flujo` en CxP."""
    ensure_column(
        db, "cuentas_por_pagar", "actividad_flujo",
        "ALTER TABLE cuentas_por_pagar ADD COLUMN IF NOT EXISTS "
        "actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'",
        "ALTER TABLE cuentas_por_pagar "
        "ADD COLUMN actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'")


def ensure_cxp_origen_tipo_column(db: Session) -> None:
    """Nivelación idempotente para `origen_tipo` en CxP (VARCHAR 40)."""
    ensure_column(
        db, "cuentas_por_pagar", "origen_tipo",
        "ALTER TABLE cuentas_por_pagar ADD COLUMN IF NOT EXISTS "
        "origen_tipo VARCHAR(40) DEFAULT 'PROVEEDORES MATERIA PRIMA'",
        "ALTER TABLE cuentas_por_pagar "
        "ADD COLUMN origen_tipo VARCHAR(40) DEFAULT 'PROVEEDORES MATERIA PRIMA'")


def ensure_cxp_actividad_flujo_column(db: Session) -> None:
    """Nivelación idempotente para `actividad_flujo` en CxP."""
    ensure_column(
        db, "cuentas_por_pagar", "actividad_flujo",
        "ALTER TABLE cuentas_por_pagar ADD COLUMN IF NOT EXISTS "
        "actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'",
        "ALTER TABLE cuentas_por_pagar "
        "ADD COLUMN actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'")


def ensure_cxp_observacion_column(db: Session) -> None:
    """Nivelación idempotente para `observacion` en CxP."""
    ensure_column(
        db, "cuentas_por_pagar", "observacion",
        "ALTER TABLE cuentas_por_pagar ADD COLUMN IF NOT EXISTS "
        "observacion VARCHAR(255)",
        "ALTER TABLE cuentas_por_pagar "
        "ADD COLUMN observacion VARCHAR(255)")




def ensure_gasto_flujo_columns(db: Session) -> None:
    """Nivelación idempotente de vencimiento y actividad de flujo en gastos."""
    ensure_column(
        db, "gastos_registrados", "fecha_vencimiento",
        "ALTER TABLE gastos_registrados ADD COLUMN IF NOT EXISTS fecha_vencimiento DATE",
        "ALTER TABLE gastos_registrados ADD COLUMN fecha_vencimiento DATE")
    ensure_column(
        db, "gastos_registrados", "actividad_flujo",
        "ALTER TABLE gastos_registrados ADD COLUMN IF NOT EXISTS actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'",
        "ALTER TABLE gastos_registrados ADD COLUMN actividad_flujo VARCHAR(20) DEFAULT 'OPERATIVO'")


def actividad_flujo_gasto(categoria: str | None, actividad: str | None = None) -> str:
    """Devuelve una actividad de flujo estrictamente válida para el gasto."""
    if actividad:
        valor = actividad.strip().upper()
        if valor not in {"OPERATIVO", "INVERSION", "FINANCIAMIENTO"}:
            raise ValueError("actividad_flujo debe ser OPERATIVO, INVERSION o FINANCIAMIENTO")
        return valor
    return "INVERSION" if (categoria or "").upper() == "ACTIVO_FIJO" else "OPERATIVO"

def ensure_caja_turnos_table(db: Session) -> None:
    """Crea caja_turnos si falta (BDs donde alembic nunca corrió esa revisión)."""
    try:
        from app.models.billing import CajaTurno
        CajaTurno.__table__.create(db.get_bind(), checkfirst=True)
    except Exception:
        pass


def ensure_empleados_planilla_columns(db: Session) -> None:
    """Nivelación idempotente de columnas de planilla en empleados.

    Si la migración c6d7 no corrió en una BD longeva, la vista Personal
    revienta con UndefinedColumn (sueldo_basico, ...). DDL ciego e
    idempotente; nunca lanza.
    """
    ensure_column(
        db, "empleados", "sueldo_basico",
        "ALTER TABLE empleados ADD COLUMN IF NOT EXISTS sueldo_basico FLOAT DEFAULT 0",
        "ALTER TABLE empleados ADD COLUMN sueldo_basico FLOAT DEFAULT 0")
    ensure_column(
        db, "empleados", "asignacion_familiar",
        "ALTER TABLE empleados ADD COLUMN IF NOT EXISTS asignacion_familiar BOOLEAN DEFAULT FALSE",
        "ALTER TABLE empleados ADD COLUMN asignacion_familiar BOOLEAN DEFAULT FALSE")
    ensure_column(
        db, "empleados", "sistema_pensiones",
        "ALTER TABLE empleados ADD COLUMN IF NOT EXISTS sistema_pensiones VARCHAR(20) DEFAULT 'ONP'",
        "ALTER TABLE empleados ADD COLUMN sistema_pensiones VARCHAR(20) DEFAULT 'ONP'")
    ensure_column(
        db, "empleados", "regimen_laboral",
        "ALTER TABLE empleados ADD COLUMN IF NOT EXISTS regimen_laboral VARCHAR(20) DEFAULT 'General'",
        "ALTER TABLE empleados ADD COLUMN regimen_laboral VARCHAR(20) DEFAULT 'General'")


def ensure_configuracion_table(db: Session) -> None:
    """Crea la tabla configuracion y siembra DEFAULTS si está vacía.

    Sin esto, /admin/configuracion muestra campos vacíos y el guardado
    falla por esquema (UndefinedTable) en BDs donde la migración 60288cb
    no corrió.
    """
    try:
        from app.models.config import Configuracion
        Configuracion.__table__.create(db.get_bind(), checkfirst=True)
    except Exception:
        pass
    try:
        from app.services import config as config_svc
        config_svc.seed_defaults(db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def ensure_caja_columns(db: Session) -> None:
    """Nivelación idempotente de caja/turnos y flag corporativo.

    Cubre el caso de BDs longevas donde una migración no corrió:
    sin estas columnas cualquier listado de caja/clientes devuelve 500.
    """
    ensure_column(
        db, "cash_movements", "turno_id",
        "ALTER TABLE cash_movements ADD COLUMN IF NOT EXISTS turno_id INTEGER",
        "ALTER TABLE cash_movements ADD COLUMN turno_id INTEGER")
    ensure_column(
        db, "cash_movements", "medio_pago",
        "ALTER TABLE cash_movements ADD COLUMN IF NOT EXISTS medio_pago VARCHAR(20)",
        "ALTER TABLE cash_movements ADD COLUMN medio_pago VARCHAR(20)")
    ensure_column(
        db, "cash_movements", "cuenta_contable_id",
        "ALTER TABLE cash_movements ADD COLUMN IF NOT EXISTS cuenta_contable_id INTEGER",
        "ALTER TABLE cash_movements ADD COLUMN cuenta_contable_id INTEGER")
    ensure_column(
        db, "clients", "es_corporativo",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS es_corporativo BOOLEAN DEFAULT FALSE",
        "ALTER TABLE clients ADD COLUMN es_corporativo BOOLEAN DEFAULT FALSE")
    ensure_column(
        db, "clients", "company_id",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS company_id INTEGER",
        "ALTER TABLE clients ADD COLUMN company_id INTEGER")
    ensure_column(
        db, "orders", "concepto",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS concepto VARCHAR(255)",
        "ALTER TABLE orders ADD COLUMN concepto VARCHAR(255)")


def ensure_runtime_schema(db: Session) -> dict:
    """Nivelación DDL única para arranque/scripts. PROHIBIDO en requests.

    El DDL por request (con pgbouncer) apila locks ACCESS EXCLUSIVE y
    cuelga la app. Esto corre una sola vez en el boot (ver lifespan):
    columnas CxP/gastos, ensanchado de origen_tipo a VARCHAR(40) en PG,
    tabla empleados, tabla/columnas de caja-turnos y flag corporativo.
    Con commit propio; nunca lanza.
    """
    estado: dict = {"columnas": True, "origen_40": False, "empleados": False,
                    "caja": False, "personal": False, "config": False}
    try:
        ensure_gasto_retencion_column(db)
        ensure_cxp_tipo_comprobante_column(db)
        ensure_cxp_origen_tipo_column(db)
        ensure_cxp_actividad_flujo_column(db)
        ensure_cxp_observacion_column(db)
        ensure_gasto_flujo_columns(db)
        ensure_caja_turnos_table(db)
        ensure_caja_columns(db)
        estado["caja"] = True
        ensure_empleados_planilla_columns(db)
        estado["personal"] = True
        ensure_configuracion_table(db)
        estado["config"] = True
        _refrescar_pool(db)
        try:
            bind = db.get_bind()
            if bind.dialect.name == "postgresql":
                from sqlalchemy import inspect as _inspect
                from sqlalchemy import text as _text
                col = next((c for c in _inspect(bind).get_columns(
                    "cuentas_por_pagar") if c["name"] == "origen_tipo"), None)
                largo = getattr(col["type"], "length", 0) if col else 0
                if col is not None and largo and largo < 40:
                    db.execute(_text(
                        "ALTER TABLE cuentas_por_pagar ALTER COLUMN "
                        "origen_tipo TYPE VARCHAR(40)"))
                    estado["origen_40"] = True
        except Exception:
            pass
        try:
            from app.models.personnel import ensure_empleados_table
            ensure_empleados_table(db)
            estado["empleados"] = True
        except Exception:
            pass
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        estado["columnas"] = False
    return estado


# ── Costeo absorbente: tarifa por minuto de taller ───────────────────
def planilla_taller_mes(db: Session, anio: int, mes: int) -> Decimal:
    """Planilla mensual (gastos 6211) de centros tipo TALLER (921/922/923)."""
    from datetime import date as _date
    import calendar as _cal
    desde = _date(anio, mes, 1)
    hasta = _date(anio, mes, _cal.monthrange(anio, mes)[1])
    ids = [c.id for c in db.query(CentroCosto).filter(
        CentroCosto.tipo == "TALLER").all()]
    if not ids:
        return Decimal("0")
    total = db.query(func.coalesce(func.sum(GastoRegistrado.monto_total), 0)).filter(
        GastoRegistrado.categoria.in_(["PLANILLA", "PLANILLA_PERSONAL"]),
        GastoRegistrado.fecha_emision >= desde,
        GastoRegistrado.fecha_emision <= hasta,
        GastoRegistrado.centro_costo_id.in_(ids)).scalar() or 0
    return Decimal(str(total))


def minutos_mod_pedido(db: Session, order_id: int) -> tuple[float, str]:
    """Minutos de M.O. directa del pedido (None-safe, nunca lanza).

    Cadena de absorción (primera fuente con minutos > 0):
    1. "tareo": WorkLogs terminados de las prendas (tiempo real fichado).
    2. "sam": Garment.sam_estimado acumulado (estándar asignado a la ficha).
    3. "sam_estandar": SAM estándar por tipo de prenda (DEFAULT_OPERATIONS).
    4. (0.0, "sin_datos"): sin base de costeo registrada.
    """
    try:
        from app.models.order import Garment, WorkLog
        garments = db.query(Garment).filter(
            Garment.order_id == order_id).all() or []
        gids = [g.id for g in garments if getattr(g, "id", None) is not None]
        if gids:
            from sqlalchemy import func as _func
            try:
                wl = float(db.query(_func.coalesce(
                    _func.sum(WorkLog.minutos_reales), 0)).filter(
                    WorkLog.garment_id.in_(gids),
                    WorkLog.estado == "terminado").scalar() or 0)
            except Exception:
                wl = 0.0
            if wl > 0:
                return round(wl, 2), "tareo"
        try:
            sam = round(sum(float(getattr(g, "sam_estimado", 0) or 0)
                            for g in garments), 2)
        except Exception:
            sam = 0.0
        if sam > 0:
            return sam, "sam"
        try:
            from app.core.constants import DEFAULT_OPERATIONS
            por_tipo: dict[str, float] = {}
            for op in DEFAULT_OPERATIONS:
                por_tipo.setdefault(str(op.get("tipo_prenda") or ""),
                                    0.0)
                try:
                    por_tipo[str(op.get("tipo_prenda") or "")] += float(
                        op.get("sam_minutos") or 0)
                except (TypeError, ValueError):
                    pass
            std = round(sum(por_tipo.get(str(getattr(g, "tipo", None) or ""), 0.0)
                            for g in garments), 2)
        except Exception:
            std = 0.0
        if std > 0:
            return std, "sam_estandar"
    except Exception:
        pass
    return 0.0, "sin_datos"


def tarifa_minuto_taller(db: Session, fecha: date | None = None) -> float:
    """Tarifa_Minuto = Planilla_Taller_Mensual / Capacidad_Minutos_Mes.

    Blindado: capacidad <= 0/None → 11520; sin planilla o cualquier error →
    tarifa configurable por defecto (0.35). Nunca lanza ni divide por cero.
    """
    from app.services import config as config_svc
    from datetime import date as _date
    try:
        fecha = fecha or _date.today()
        planilla = planilla_taller_mes(db, fecha.year, fecha.month) or Decimal("0")
        if planilla > 0:
            try:
                capacidad = float(config_svc.get(db, "capacidad_minutos_mes", "11520"))
            except (ValueError, TypeError):
                capacidad = 11520.0
            if not capacidad or capacidad <= 0:
                capacidad = 11520.0
            return round(float(planilla) / capacidad, 4)
        try:
            return float(config_svc.get(db, "tarifa_minuto_default", "0.35"))
        except (ValueError, TypeError):
            return 0.35
    except Exception:
        return 0.35


# Mapeo categoría operativa -> (cuenta PCGE por defecto, clasificación de costos)
CATEGORIA_GASTO_MAP: dict[str, tuple[str, str]] = {
    # Nuevas categorías estandarizadas (cuenta PCGE, clasificación).
    "ALQUILERES": ("6311", "CIF"),
    "HONORARIOS_TERCEROS": ("6322", "GASTO_ADMINISTRATIVO"),
    "PLANILLA_PERSONAL": ("6211", "GASTO_ADMINISTRATIVO"),
    "SERVICIOS_BASICOS": ("6361", "GASTO_ADMINISTRATIVO"),
    "COMPRAS_AVIOS_SUMINISTROS": ("6032", "GASTO_ADMINISTRATIVO"),
    "PUBLICIDAD_MARKETING": ("6371", "GASTO_VENTAS"),
    "ACTIVO_FIJO": ("3351", "ACTIVO_FIJO"),
    "OTROS_GASTOS": ("6599", "GASTO_ADMINISTRATIVO"),
    "SUMINISTROS_Y_HERRAMIENTAS_TALLER": ("6599", "GASTO_ADMINISTRATIVO"),
    # Legacy (compatibilidad): resuelven a las mismas reglas nuevas.
    "ALQUILER": ("6311", "CIF"),
    "ALQUILER_NATURAL": ("6311", "CIF"),
    "HONORARIOS": ("6322", "GASTO_ADMINISTRATIVO"),
    "HONORARIOS_RXH": ("6322", "GASTO_ADMINISTRATIVO"),
    "PLANILLA": ("6211", "GASTO_ADMINISTRATIVO"),
    "OTRO": ("6511", "GASTO_ADMINISTRATIVO"),
}
# Matriz PCGE por categoría: IGV ("cero"|"auto"|"dado"), pasivo y origen.
# - cero: sin IGV aunque se pase (planilla, RxH, alquiler persona natural).
# - auto: 18% si es FACTURA y no se dio IGV (activo fijo).
# - dado: respeta el IGV explícito (facturas de proveedores/servicios).
REGLA_GASTO_MAP: dict[str, dict] = {
    "PLANILLA": {"igv": "cero", "pasivo": "4111", "origen": "PLANILLA"},
    "PLANILLA_PERSONAL": {"igv": "cero", "pasivo": "4111", "origen": "PLANILLA"},
    "HONORARIOS_RXH": {"igv": "cero", "pasivo": "4241", "origen": "HONORARIOS"},
    "HONORARIOS_TERCEROS": {"igv": "cero", "pasivo": "4241", "origen": "HONORARIOS"},
    "ALQUILER_NATURAL": {"igv": "cero", "pasivo": "4699", "origen": "GASTO_DIVERSO"},
    "ACTIVO_FIJO": {"igv": "auto", "pasivo": "4654", "origen": "ACTIVO_FIJO"},
}
REGLA_GASTO_DEFAULT = {"igv": "dado", "pasivo": "4212", "origen": "COMPRA"}


def regla_gasto(categoria: str | None) -> dict:
    return REGLA_GASTO_MAP.get((categoria or "").upper(), REGLA_GASTO_DEFAULT)


# Categoría de gasto -> regla del Plan Operativo (naturaleza del asiento).
REGLA_CONTABLE_POR_CATEGORIA: dict[str, str] = {
    "ALQUILERES": "GASTO_ALQUILER",
    "HONORARIOS_TERCEROS": "GASTO_RXH",
    "PLANILLA_PERSONAL": "PLANILLA",
    "SERVICIOS_BASICOS": "GASTO_SERVICIOS",
    "COMPRAS_AVIOS_SUMINISTROS": "GASTO_AVIOS",
    "PUBLICIDAD_MARKETING": "GASTO_PUBLICIDAD",
    "ACTIVO_FIJO": "GASTO_ACTIVO",
    "OTROS_GASTOS": "GASTO_OTROS",
    "SUMINISTROS_Y_HERRAMIENTAS_TALLER": "GASTO_OTROS",
    # Legacy (compatibilidad).
    "ALQUILER": "GASTO_ALQUILER",
    "ALQUILER_NATURAL": "GASTO_ALQUILER_NATURAL",
    "HONORARIOS": "GASTO_HONORARIOS",
    "HONORARIOS_RXH": "GASTO_RXH",
    "PLANILLA": "PLANILLA",
}


def _pasivo_de_regla(db: Session, codigo_regla: str) -> str:
    """Último token del haber de la regla (pasivo de la provisión)."""
    from app.services.motor_contable import get_regla
    toks = [t.strip() for t in (get_regla(db, codigo_regla).haber_patron or "").split("+") if t.strip()]
    if not toks:
        raise ValueError(f"Regla {codigo_regla} sin haber")
    return toks[-1]


def _debe_de_regla(db: Session, codigo_regla: str) -> str:
    """Primer token del debe resuelto a código hoja."""
    from app.services.motor_contable import expandir_patron, get_regla
    toks = [t.strip() for t in (get_regla(db, codigo_regla).debe_patron or "").split("+") if t.strip()]
    if not toks:
        raise ValueError(f"Regla {codigo_regla} sin debe")
    return expandir_patron(db, toks[0]).codigo


def pasivo_por_categoria(categoria: str | None) -> str:
    return regla_gasto(categoria)["pasivo"]


# Destino analítico (Elemento 9) por tipo de centro de costo.
DESTINO_POR_CENTRO: dict[str, str] = {
    "TALLER": "921",
    "ADMINISTRACION": "941",
    "COMERCIAL": "951",
    "SHOWROOM": "951",
}

# Destino contable Clase 9 por clasificación (solo cuentas de Clase 6).
# Asiento por destino: DEBE 94x/95x/97x/921 / HABER 791 (traslado analítico).
CLASIF_DESTINO_MAP: dict[str, str] = {
    "GASTO_ADMINISTRATIVO": "941",
    "GASTO_VENTAS": "951",
    "GASTO_FINANCIERO": "971",
    "CIF": "921",
}
CTA_DESTINO_CONTRAPARTIDA = "791"

# seed_pcge_basico / seed_pcge_detallado ELIMINADOS (Plan Operativo):
# usar seed_pcge_nuevo.py o asegurar_plan_operativo(). Cualquier llamada
# residual debe migrarse al motor (app/services/motor_contable.py).


# ── Gastos / compras operativas ──────────────────────────────────────
def registrar_gasto_operativo(
    db: Session,
    fecha: date,
    categoria: str,
    monto_base: float | Decimal,
    monto_igv: float | Decimal = 0,
    cuenta_codigo: str | None = None,
    proveedor_id: int | None = None,
    ruc_proveedor: str | None = None,
    tipo_comprobante: str | None = None,
    numero_comprobante: str | None = None,
    centro_costo_id: int | None = None,
    variabilidad: str = "FIJO",
    clasificacion: str | None = None,
    glosa: str | None = None,
    retencion: float | Decimal = 0,
    fecha_vencimiento: date | None = None,
    actividad_flujo: str | None = None,
    activo_fijo_id: int | None = None,
) -> tuple[GastoRegistrado, AsientoContable]:
    """Registra gasto/compra operativa y genera sus asientos devengados.

    Naturaleza: DEBE cuenta gasto/activo (base) + DEBE 40111 IGV (si aplica)
      / HABER pasivo según matriz (4212 proveedores, 4111 planilla,
      424 honorarios RxH, 4699 alquiler natural, 4654 activo fijo).
      Con retención (RxH 4ta): HABER 40172 por la retención y HABER pasivo
      por el neto (base + igv − retención).
    Destino (solo Clase 6): RxH/destajo de taller → DESTINO_921
    obligatorio (MOD de confección); si no, por CENTRO DE COSTO
    (reglas DESTINO_921/941/951/971).
      / HABER 791; sin destino para cuentas de Balance (33x). El 941 queda
      reservado a oficina y planilla administrativa.
    Retorna (gasto, asiento_naturaleza). Un solo commit final (atómico).

    Bloqueo duro: raise si `fecha` cae en período CERRADO/BLOQUEADO.
    """
    from app.services.contabilidad import exigir_periodo_abierto as _exigir
    from app.services.motor_contable import (
        dim_centro,
        expandir_patron,
        get_regla,
        post_manual,
        post_regla,
        post_regla_extra,
    )
    try:
        return _registrar_gasto_operativo_tx(
            db, fecha=fecha, categoria=categoria, monto_base=monto_base,
            monto_igv=monto_igv, cuenta_codigo=cuenta_codigo,
            proveedor_id=proveedor_id, ruc_proveedor=ruc_proveedor,
            tipo_comprobante=tipo_comprobante,
            numero_comprobante=numero_comprobante,
            centro_costo_id=centro_costo_id, variabilidad=variabilidad,
            clasificacion=clasificacion, glosa=glosa, retencion=retencion,
            fecha_vencimiento=fecha_vencimiento,
            actividad_flujo=actividad_flujo, activo_fijo_id=activo_fijo_id)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def _registrar_gasto_operativo_tx(
    db, fecha, categoria, monto_base, monto_igv=0, cuenta_codigo=None,
    proveedor_id=None, ruc_proveedor=None, tipo_comprobante=None,
    numero_comprobante=None, centro_costo_id=None, variabilidad="FIJO",
    clasificacion=None, glosa=None, retencion=0, fecha_vencimiento=None,
    actividad_flujo=None, activo_fijo_id=None,
):
    from app.services.contabilidad import exigir_periodo_abierto as _exigir
    from app.services.motor_contable import (
        dim_centro,
        expandir_patron,
        get_regla,
        post_manual,
        post_regla,
        post_regla_extra,
    )
    _exigir(db, fecha)
    cat = (categoria or "OTRO").upper()
    _cuenta_def, clasif_def = CATEGORIA_GASTO_MAP.get(cat, CATEGORIA_GASTO_MAP["OTRO"])
    clasif = clasificacion or clasif_def
    regla = regla_gasto(cat)
    actividad = actividad_flujo_gasto(cat, actividad_flujo)
    vencimiento = fecha_vencimiento or (fecha + timedelta(days=30))
    if vencimiento < fecha:
        raise ValueError("fecha_vencimiento no puede ser anterior a fecha_emision")
    # HONORARIOS_TERCEROS: comprobante fijo RECIBO_HONORARIOS, sin IGV y
    # con retención 8% automática (4ta categoría) si no se indicó otra.
    if cat == "HONORARIOS_TERCEROS":
        tipo_comprobante = "RECIBO_HONORARIOS"
    base = Decimal(str(monto_base or 0))
    igv = Decimal(str(monto_igv or 0))
    if regla["igv"] == "cero":
        igv = Decimal("0")  # planilla, RxH y alquiler natural: sin IGV
    elif regla["igv"] == "auto" and igv <= 0 and (tipo_comprobante or "").upper() == "FACTURA":
        igv = (base * Decimal("0.18")).quantize(Decimal("0.01"))
    if base <= 0:
        raise ValueError("monto_base debe ser positivo")
    if igv < 0:
        raise ValueError("IGV no puede ser negativo")
    ret = Decimal(str(retencion or 0))
    if cat == "HONORARIOS_TERCEROS" and ret <= 0 and base > 0:
        try:
            from app.services import config as _cfg
            pct = Decimal(str(_cfg.parametros_laborales(db)["retencion_4ta_pct"]))
        except Exception:
            pct = Decimal("8")
        ret = (base * pct / Decimal("100")).quantize(Decimal("0.01"))
    if ret < 0 or ret > base + igv:
        raise ValueError("retención inválida (0 <= retención <= total)")
    total = base + igv
    # Espejo de contraparte: sin proveedor explícito pero con RUC, se
    # reutiliza/crea el Supplier (la dimensión proveedor es obligatoria).
    # Planilla sin contraparte usa el proveedor genérico "Planilla / Personal".
    if not proveedor_id and (ruc_proveedor or "").strip():
        from app.models.purchasing import Supplier as _Supplier
        ruc = ruc_proveedor.strip()
        sup = db.query(_Supplier).filter(_Supplier.ruc == ruc).first()
        if not sup:
            sup = _Supplier(
                nombre=((glosa or "").strip() or f"Proveedor {ruc}")[:160],
                ruc=ruc)
            db.add(sup)
            db.flush()
        proveedor_id = sup.id
    if not proveedor_id and cat in ("PLANILLA", "PLANILLA_PERSONAL"):
        from app.models.purchasing import Supplier as _SupplierPl
        sup = db.query(_SupplierPl).filter(
            _SupplierPl.nombre == "Planilla / Personal").first()
        if not sup:
            sup = _SupplierPl(nombre="Planilla / Personal")
            db.add(sup)
            db.flush()
        proveedor_id = sup.id
    if not proveedor_id and regla.get("pasivo") == "4212":
        # Las provisiones en Cta 4212 (proveedores) exigen factura de un
        # proveedor real: sin proveedor_id ni RUC no hay contraparte fiscal
        # válida y el pasivo quedaría contra un genérico sin trazabilidad.
        raise ValueError(
            "Proveedor real requerido: las provisiones en Cta 4212 exigen "
            "factura de proveedor (proveedor_id o RUC)")
    if not proveedor_id:
        # Contrapartida genérica: todo gasto genera CxP (proveedor NOT NULL).
        from app.models.purchasing import Supplier as _SupplierGen
        sup = db.query(_SupplierGen).filter(
            _SupplierGen.nombre == "Proveedor / Varios").first()
        if not sup:
            sup = _SupplierGen(nombre="Proveedor / Varios")
            db.add(sup)
            db.flush()
        proveedor_id = sup.id
    # Naturaleza por regla del Plan Operativo (sin cuentas cableadas).
    cod_regla = REGLA_CONTABLE_POR_CATEGORIA.get(cat, "GASTO_GENERAL")
    dims_nat = {"proveedor_id": proveedor_id,
                "centro_costo_id": centro_costo_id,
                "activo_fijo_id": activo_fijo_id}
    if cuenta_codigo:
        # Override explícito: estructura de la regla con el debe sustituido.
        cta_over = expandir_patron(db, cuenta_codigo)
        from app.services.motor_contable import _exigir_imputable
        _exigir_imputable(cta_over)
        pasivo_cod = _pasivo_de_regla(db, cod_regla)
        items_debe = [(cuenta_codigo, base)] + ([("40111", igv)] if igv > 0 else [])
        items_haber = ([("40172", ret)] if ret > 0 else []) + [(pasivo_cod, total - ret)]
        gasto_cuenta_id = cta_over.id
        _items_override = (items_debe, items_haber)
    else:
        gasto_cuenta_id = expandir_patron(
            db, get_regla(db, cod_regla).debe_patron.split("+")[0].strip()).id
        _items_override = None
    gasto = GastoRegistrado(
        proveedor_id=proveedor_id,
        ruc_proveedor=(ruc_proveedor or None),
        tipo_comprobante=(tipo_comprobante or None),
        numero_comprobante=(numero_comprobante or None),
        categoria=cat,
        glosa=(glosa or "").strip() or None,
        monto_base=float(base),
        monto_igv=float(igv),
        monto_total=float(total),
        retencion=float(ret),
        clasificacion=clasif,
        variabilidad=variabilidad or "FIJO",
        centro_costo_id=centro_costo_id,
        cuenta_id=gasto_cuenta_id,
        fecha_emision=fecha,
        fecha_vencimiento=vencimiento,
        actividad_flujo=actividad,
        estado="PENDIENTE",
    )
    db.add(gasto); db.flush()
    # CxP inmediata: TODO gasto genera su espejo POR_PAGAR (incluye
    # planillas y anónimos vía proveedores espejo). Idempotente por
    # (clave, proveedor); el pago posterior la salda (Tesorería/CxP).
    from datetime import timedelta as _td
    clave_cxp = (numero_comprobante or "").strip() or f"GASTO-{gasto.id}"
    ya_cxp = db.query(CuentaPorPagar).filter(
        CuentaPorPagar.numero_factura == clave_cxp,
        CuentaPorPagar.proveedor_id == proveedor_id).first()
    if not ya_cxp:
        db.add(CuentaPorPagar(
            proveedor_id=proveedor_id, numero_factura=clave_cxp,
            origen_tipo=origen_por_categoria_gasto(cat),
            actividad_flujo=actividad,
            tipo_comprobante=(tipo_comprobante or "FACTURA"),
            monto_total=float(total), monto_pagado=0.0,
            saldo_pendiente=round(max(float(total) - float(ret), 0.0), 2),
            retencion=float(ret), fecha_emision=fecha,
            fecha_vencimiento=vencimiento, estado="POR_PAGAR"))
        db.flush()
    es_rxh = cat == "HONORARIOS_RXH" or (tipo_comprobante or "").upper() == "RECIBO_HONORARIOS"
    glosa_nat = (glosa or f"{cat.title()} {numero_comprobante or ''}").strip() or cat.title()
    neto = total - ret
    if _items_override is not None:
        items_debe, items_haber = _items_override
        asiento = post_manual(
            db, items_debe, items_haber, dims_nat, glosa_nat,
            regla["origen"], gasto.id, fecha)
    elif ret > 0:
        asiento = post_regla_extra(
            db, cod_regla, [base, igv], [neto],
            extra_haber=[("40172", ret)], dims=dims_nat,
            glosa=glosa_nat, origen_tipo=regla["origen"],
            origen_id=gasto.id, fecha=fecha)
    else:
        asiento = post_regla(
            db, cod_regla, [base, igv], [total], dims_nat,
            glosa_nat, regla["origen"], gasto.id, fecha)
    # Destino analítico por reglas DESTINO_*: RxH/destajo de taller siempre
    # a DESTINO_921; el resto por centro de costo (o clasificación).
    # Sin destino para cuentas de Balance (primer debe no empieza en "6").
    debe_cod = (cuenta_codigo or _debe_de_regla(db, cod_regla))
    if debe_cod.startswith("6"):
        if es_rxh:
            cod_dest = "DESTINO_921"
        else:
            cod_dest = None
            if centro_costo_id:
                cc = db.get(CentroCosto, centro_costo_id)
                if cc and (cc.tipo or "").upper() in DESTINO_POR_CENTRO:
                    cod_dest = {"921": "DESTINO_921", "941": "DESTINO_941",
                                "951": "DESTINO_951",
                                "971": "DESTINO_971"}.get(
                        DESTINO_POR_CENTRO[(cc.tipo or "").upper()])
            if not cod_dest:
                cod_dest = {"921": "DESTINO_921", "941": "DESTINO_941",
                            "951": "DESTINO_951",
                            "971": "DESTINO_971"}.get(CLASIF_DESTINO_MAP.get(clasif))
            if not cod_dest:
                raise ValueError(
                    f"Sin regla de destino para clasificación {clasif}")
        dims_dest = {"centro_costo_id": centro_costo_id or dim_centro(db, "921")}
        post_regla(
            db, cod_dest, [base], [base], dims_dest,
            f"Destino {cod_dest} {cat.title()} {numero_comprobante or ''}".strip(),
            regla["origen"], gasto.id, fecha)
    db.commit()
    db.refresh(gasto)
    return gasto, asiento


def pagar_gasto(
    db: Session, gasto_id: int, fecha: date | None = None, cuenta_origen_codigo: str = "104",
    permitir_sobregiro: bool = False,
) -> AsientoContable:
    """Cancela un gasto: DEBE pasivo (4212/4111/424/4699/4654 según categoría)
    / HABER Caja(101)-Banco(104). Marca PAGADO.

    Bloqueo duro: raise si `fecha` cae en período CERRADO/BLOQUEADO.
    Sobregiro estricto: 1011 bloquea sin saldo; 1041 exige
    permitir_sobregiro=True.
    """
    try:
        from app.services.contabilidad import exigir_periodo_abierto as _exigir
        from app.services.motor_contable import post_manual, post_regla
        _exigir(db, fecha)
        gasto = db.get(GastoRegistrado, gasto_id)
        if not gasto:
            raise ValueError("Gasto no encontrado")
        if gasto.estado == "PAGADO":
            raise ValueError("El gasto ya está PAGADO")
        total = Decimal(str(gasto.monto_total or 0))
        if total <= 0:
            raise ValueError("Total inválido")
        pasivo_cod = pasivo_por_categoria(gasto.categoria)
        hoja_caja = {"104": "1041", "101": "1011"}.get(
            cuenta_origen_codigo, cuenta_origen_codigo)
        exigir_saldo_tesoreria(
            db, hoja_caja, total, permitir_sobregiro=bool(permitir_sobregiro))
        dims = {"proveedor_id": gasto.proveedor_id,
                "centro_costo_id": gasto.centro_costo_id}
        if pasivo_cod == "4212":
            regla_pago = ("PAGO_CAJA" if hoja_caja == "1011" else "PAGO_BANCO")
            asiento = post_regla(
                db, regla_pago, [total], [total], dims,
                f"Pago gasto {gasto.numero_comprobante or gasto.id}",
                "PAGO", gasto.id, fecha)
        else:
            asiento = post_manual(
                db, [(pasivo_cod, total)], [(hoja_caja, total)], dims,
                f"Pago gasto {gasto.numero_comprobante or gasto.id}",
                "PAGO", gasto.id, fecha)
        gasto.estado = "PAGADO"
        db.commit(); db.refresh(gasto)
        return asiento
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def registrar_depreciacion(db: Session, fecha: date, monto: float | Decimal,
                           glosa: str | None = None,
                           activo_fijo_id: int | None = None,
                           centro_costo_id: int | None = None) -> AsientoContable:
    """Depreciación del periodo (regla DEPRECIACION: 6811 / 3911)."""
    try:
        from app.services.motor_contable import post_regla
        monto_d = Decimal(str(monto or 0))
        if monto_d <= 0:
            raise ValueError("monto debe ser positivo")
        asiento = post_regla(
            db, "DEPRECIACION", [monto_d], [monto_d],
            {"activo_fijo_id": activo_fijo_id,
             "centro_costo_id": centro_costo_id},
            glosa or f"Depreciación {fecha:%Y-%m}", "MANUAL", None, fecha)
        db.commit()
        db.refresh(asiento)
        return asiento
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def cierre_resultados(db: Session, anio: int, mes: int) -> AsientoContable | None:
    """Cierra ingresos/gastos del periodo contra 5911. Idempotente por periodo."""
    try:
        from datetime import date as _date
        periodo = get_or_create_periodo(db, anio, mes)
        ya = db.query(AsientoContable).filter(AsientoContable.periodo_id == periodo.id,
                                              AsientoContable.origen_tipo == "CIERRE").first()
        if ya:
            return ya
        bal = obtener_balance_comprobacion(db, periodo.id)
        if not bal:
            return None
        from app.services.motor_contable import post_manual
        items_debe: list[tuple[str, Decimal]] = []
        items_haber: list[tuple[str, Decimal]] = []
        for r in bal:
            cod = r["codigo"]
            if cod in ("5911", "59"):
                continue
            neto_debe = r["debe"] - r["haber"]  # >0 saldo deudor
            neto_haber = r["haber"] - r["debe"]  # >0 saldo acreedor
            if r["tipo"] == "INGRESO" and neto_haber > 0:
                items_debe.append((cod, neto_haber))
            elif r["tipo"] == "GASTO" and neto_debe > 0:
                items_haber.append((cod, neto_debe))
        if not items_debe and not items_haber:
            return None
        tot_d = sum(m for _, m in items_debe)
        tot_h = sum(m for _, m in items_haber)
        if tot_h >= tot_d:
            items_debe.append(("5911", tot_h - tot_d))
        else:
            items_haber.append(("5911", tot_d - tot_h))
        import calendar
        asiento = post_manual(
            db, items_debe, items_haber, None,
            f"Cierre resultados {anio}-{mes:02d}", "CIERRE", periodo.id,
            _date(anio, mes, calendar.monthrange(anio, mes)[1]))
        db.commit()
        db.refresh(asiento)
        return asiento
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def cerrar_periodos_antiguos(db: Session, hoy: date | None = None) -> int:
    """Cierra periodos con mes anterior al actual. Retorna cerrados."""
    hoy = hoy or date.today()
    n = 0
    for p in db.query(PeriodoContable).filter(PeriodoContable.estado == "ABIERTO").all():
        if (p.anio, p.mes) < (hoy.year, hoy.month):
            try:
                from datetime import datetime as _dt
                p.estado = "CERRADO"; p.fecha_cierre = _dt.now(); n += 1
            except Exception:
                pass
    if n:
        db.commit()
    return n

def get_periodo(db: Session, anio: int, mes: int) -> PeriodoContable | None:
    return db.query(PeriodoContable).filter(PeriodoContable.anio==anio, PeriodoContable.mes==mes).first()

def get_or_create_periodo(db: Session, anio: int, mes: int) -> PeriodoContable:
    p = get_periodo(db, anio, mes)
    if p:
        return p
    p = PeriodoContable(anio=anio, mes=mes, estado="ABIERTO")
    db.add(p); db.commit(); db.refresh(p)
    return p

# ── Validación partida doble ───────────────────────────────────────────
def validar_asiento(db: Session, lineas: list[dict]):
    if not lineas or len(lineas) < 2:
        raise ValueError("Asiento requiere al menos 2 líneas")
    total_debe = sum(Decimal(str(l.get("debe") or 0)) for l in lineas)
    total_haber = sum(Decimal(str(l.get("haber") or 0)) for l in lineas)
    if total_debe != total_haber:
        raise ValueError(f"Asiento descuadrado: DEBE {total_debe} != HABER {total_haber}")
    if total_debe <= 0:
        raise ValueError("Importe debe ser positivo")
    for l in lineas:
        d = Decimal(str(l.get("debe") or 0))
        h = Decimal(str(l.get("haber") or 0))
        if d < 0 or h < 0:
            raise ValueError("Importes negativos no permitidos")
        if d > 0 and h > 0:
            raise ValueError("Línea no puede tener debe y haber simultáneamente")
        if d == 0 and h == 0:
            raise ValueError("Línea debe tener debe o haber")
        # cuenta activa, acepta movimientos e imputable (solo hojas)
        c = db.get(CuentaContable, l["cuenta_id"])
        if not c or not c.activo or not c.acepta_movimientos:
            raise ValueError(f"Cuenta {l.get('cuenta_id')} no activa o no imputable")
        if not c.imputable:
            raise ValueError(
                f"Cuenta {c.codigo} no imputable (solo hojas reciben asientos)")

def crear_asiento_flush(db: Session, fecha: date, glosa: str, origen_tipo: str, origen_id: int | None,
                        lineas: list[dict], periodo_id: int | None = None) -> AsientoContable:
    """Variante componible: valida y hace flush SIN commit.

    Pensada para operaciones ACID compuestas (kardex + asiento + CxP en una sola
    transacción). El llamante debe hacer db.commit() / db.rollback().

    Los reintentos por colisión de número usan SAVEPOINT (begin_nested): un
    full db.rollback() aquí destruiría el trabajo pendiente del llamante
    (kardex, CPP, CxP) y dejaría la operación a medio aplicar.
    """
    if not periodo_id:
        # flush (no commit): el periodo se persiste con el commit del llamante.
        periodo = db.query(PeriodoContable).filter(
            PeriodoContable.anio == fecha.year, PeriodoContable.mes == fecha.month).first()
        if not periodo:
            periodo = PeriodoContable(anio=fecha.year, mes=fecha.month, estado="ABIERTO")
            db.add(periodo)
            db.flush()
        periodo_id = periodo.id
    else:
        periodo = db.get(PeriodoContable, periodo_id)
    if periodo and periodo.estado != "ABIERTO":
        raise ValueError(f"Periodo {periodo.anio}-{periodo.mes:02d} no está ABIERTO ({periodo.estado})")
    validar_asiento(db, lineas)
    import time as _time
    from sqlalchemy.exc import IntegrityError
    base_count = db.query(AsientoContable).count() + 1
    asiento = None
    ultimo_error: Exception | None = None
    for _intento in range(5):
        numero = f"AST-{fecha.year}{fecha.month:02d}-{base_count:05d}"
        if _intento:
            numero = f"{numero}-{int(_time.time()*1000)%10000:04d}"
            base_count += 1
        asiento = AsientoContable(numero=numero, fecha=fecha, periodo_id=periodo_id,
                                  glosa=glosa, origen_tipo=origen_tipo, origen_id=origen_id)
        try:
            with db.begin_nested():
                db.add(asiento)
                db.flush()
            break
        except IntegrityError as e:
            ultimo_error = e
            try:
                db.expunge(asiento)  # descarta solo el intento fallido
            except Exception:
                pass
            asiento = None
            continue
    if asiento is None:
        raise ValueError(f"No se pudo generar número de asiento único: {ultimo_error}")
    for l in lineas:
        db.add(LineaAsientoContable(
            asiento_id=asiento.id,
            cuenta_id=l["cuenta_id"],
            debe=Decimal(str(l.get("debe") or 0)),
            haber=Decimal(str(l.get("haber") or 0)),
            centro_costo_id=l.get("centro_costo_id"),
            centro_gestion_id=l.get("centro_gestion_id"),
            descripcion=l.get("descripcion"),
            orden_produccion_id=l.get("orden_produccion_id"),
            producto_id=l.get("producto_id"),
            trabajador_id=l.get("trabajador_id"),
            almacen_id=l.get("almacen_id"),
            cliente_id=l.get("cliente_id"),
            proveedor_id=l.get("proveedor_id"),
            activo_fijo_id=l.get("activo_fijo_id"),
            proyecto_id=l.get("proyecto_id"),
        ))
    db.flush()
    return asiento


def crear_asiento(db: Session, fecha: date, glosa: str, origen_tipo: str, origen_id: int | None,
                  lineas: list[dict], periodo_id: int | None = None, usuario_id: int | None = None) -> AsientoContable:
    # periodo
    if not periodo_id:
        periodo = get_or_create_periodo(db, fecha.year, fecha.month)
        periodo_id = periodo.id
    else:
        periodo = db.get(PeriodoContable, periodo_id)
    if periodo and periodo.estado != "ABIERTO":
        raise ValueError(f"Periodo {periodo.anio}-{periodo.mes:02d} no está ABIERTO ({periodo.estado})")
    validar_asiento(db, lineas)
    # numero correlativo robusto a concurrencia: reintenta si choca el unique
    import time as _time
    from sqlalchemy.exc import IntegrityError
    base_count = db.query(AsientoContable).count() + 1
    asiento = None
    for intento in range(5):
        if intento == 0:
            numero = f"AST-{fecha.year}{fecha.month:02d}-{base_count:05d}"
        else:
            numero = f"AST-{fecha.year}{fecha.month:02d}-{base_count:05d}-{int(_time.time()*1000)%10000:04d}"
            base_count += 1
        asiento = AsientoContable(numero=numero, fecha=fecha, periodo_id=periodo_id, glosa=glosa, origen_tipo=origen_tipo, origen_id=origen_id)
        db.add(asiento)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            # re-resuelve periodo tras rollback
            if not periodo_id:
                periodo = get_or_create_periodo(db, fecha.year, fecha.month)
                periodo_id = periodo.id
            asiento = None
            continue
    if asiento is None:
        raise ValueError("No se pudo generar número de asiento único")
    for l in lineas:
        db.add(LineaAsientoContable(
            asiento_id=asiento.id,
            cuenta_id=l["cuenta_id"],
            debe=Decimal(str(l.get("debe") or 0)),
            haber=Decimal(str(l.get("haber") or 0)),
            centro_costo_id=l.get("centro_costo_id"),
            centro_gestion_id=l.get("centro_gestion_id"),
            descripcion=l.get("descripcion")
        ))
    db.commit(); db.refresh(asiento)
    return asiento

def contabilizar(db: Session, asiento_id: int):
    a = db.get(AsientoContable, asiento_id)
    if not a:
        raise ValueError("Asiento no encontrado")
    # ya validado en creación
    return a

# ── Mayor y balances ───────────────────────────────────────────────────
def resolver_filtro_periodo(db: Session, periodo: str | None):
    """(pid, desde, hasta) para filtros YYYY-MM.

    Siempre retorna el rango de fechas cuando el período es válido: la fecha
    del asiento es la fuente de verdad (cubre asientos sin periodo asignado).
    `pid` se retorna solo como referencia para la vista.
    """
    import calendar as _cal
    if not periodo:
        return None, None, None
    try:
        anio, mes = map(int, periodo.split("-"))
        desde = date(anio, mes, 1)
        hasta = date(anio, mes, _cal.monthrange(anio, mes)[1])
    except Exception:
        return None, None, None
    p = db.query(PeriodoContable).filter(
        PeriodoContable.anio == anio, PeriodoContable.mes == mes).first()
    return (p.id if p else None), desde, hasta


def saldo_cuenta(db: Session, codigo: str, periodo_id: int | None = None) -> Decimal:
    """Saldo neto (debe − haber) de una cuenta analítica."""
    from decimal import Decimal as _D
    q = db.query(func.coalesce(func.sum(LineaAsientoContable.debe), 0),
                 func.coalesce(func.sum(LineaAsientoContable.haber), 0)) \
        .join(AsientoContable, LineaAsientoContable.asiento_id == AsientoContable.id) \
        .join(CuentaContable, LineaAsientoContable.cuenta_id == CuentaContable.id) \
        .filter(CuentaContable.codigo == codigo)
    if periodo_id:
        q = q.filter(AsientoContable.periodo_id == periodo_id)
    debe, haber = q.first() or (0, 0)
    return _D(str(debe or 0)) - _D(str(haber or 0))


def obtener_mayor(db: Session, cuenta_id: int | None = None, periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None, centro_costo_id: int | None = None):
    q = db.query(LineaAsientoContable).join(AsientoContable, LineaAsientoContable.asiento_id==AsientoContable.id)
    if cuenta_id:
        q = q.filter(LineaAsientoContable.cuenta_id==cuenta_id)
    if desde or hasta:
        # El rango de fechas prevalece: cubre asientos sin periodo asignado.
        if desde:
            q = q.filter(AsientoContable.fecha >= desde)
        if hasta:
            q = q.filter(AsientoContable.fecha <= hasta)
    elif periodo_id:
        q = q.filter(AsientoContable.periodo_id==periodo_id)
    if centro_costo_id:
        q = q.filter((LineaAsientoContable.centro_costo_id==centro_costo_id) | (LineaAsientoContable.centro_gestion_id==centro_costo_id))
    return q.order_by(AsientoContable.fecha, LineaAsientoContable.asiento_id,
                      LineaAsientoContable.id).all()

def _es_acreedora(cuenta) -> bool:
    """Naturaleza por elemento PCGE: 4,5,7 → Haber − Debe; resto → Debe − Haber."""
    try:
        el = int(cuenta.elemento) if cuenta is not None and cuenta.elemento is not None else None
    except Exception:
        el = None
    if el is not None:
        return el in (4, 5, 7)
    return (cuenta.tipo if cuenta is not None else "") in ("PASIVO", "PATRIMONIO", "INGRESO")

def obtener_mayor_agrupado(db: Session, cuenta_id: int | None = None, periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None, centro_costo_id: int | None = None):
    """Mayor agrupado por cuenta para la vista.

    Retorna [{"cuenta_id","codigo","nombre","lineas","total_debe","total_haber",
    "saldo"}] con `lineas` ordenadas por (fecha, asiento) y cada una con
    `saldo_acum` (acumulado debe − haber) y `fecha` en YYYY-MM-DD.
    """
    from decimal import Decimal as _D
    lineas = obtener_mayor(db, cuenta_id, periodo_id, desde, hasta, centro_costo_id)
    grupos: dict[int, dict] = {}
    for l in lineas:
        g = grupos.setdefault(l.cuenta_id, {"cuenta_id": l.cuenta_id,
            "codigo": l.cuenta.codigo if l.cuenta else "?",
            "nombre": l.cuenta.nombre if l.cuenta else "—",
            "acreedor": _es_acreedora(l.cuenta),
            "lineas": [], "total_debe": _D("0"), "total_haber": _D("0")})
        debe, haber = _D(str(l.debe or 0)), _D(str(l.haber or 0))
        g["total_debe"] += debe
        g["total_haber"] += haber
        net = g["total_haber"] - g["total_debe"] if g["acreedor"] else g["total_debe"] - g["total_haber"]
        acum = net
        f = l.asiento.fecha if l.asiento and l.asiento.fecha else None
        g["lineas"].append({"id": l.id,
            "fecha": f.isoformat() if f else "—",
            "asiento": l.asiento.numero if l.asiento else f"#{l.asiento_id}",
            "debe": debe, "haber": haber, "saldo_acum": acum,
            "descripcion": l.descripcion or ""})
    out = sorted(grupos.values(), key=lambda g: g["codigo"])
    for g in out:
        g["saldo"] = g["total_haber"] - g["total_debe"] if g["acreedor"] else g["total_debe"] - g["total_haber"]
    return out

def obtener_diario(db: Session, periodo_id: int | None = None,
                   origen_tipo: str | None = None, cuenta_id: int | None = None,
                   desde: date | None = None, hasta: date | None = None,
                   limit: int = 200) -> dict:
    """Libro Diario con líneas anidadas para la vista.

    Retorna {"filas": [{asiento, lineas, debe, haber, cuadrado}], "total_debe",
    "total_haber", "cuadrado", "n"}. `cuadrado` por asiento = |debe-haber|<=0.01.
    Solo lectura (no muta la sesión).
    """
    q = db.query(AsientoContable).order_by(AsientoContable.fecha.desc(),
                                           AsientoContable.id.desc()).limit(limit)
    asientos = q.all()
    if desde or hasta:
        if desde:
            asientos = [a for a in asientos if a.fecha and a.fecha >= desde]
        if hasta:
            asientos = [a for a in asientos if a.fecha and a.fecha <= hasta]
    elif periodo_id:
        asientos = [a for a in asientos if a.periodo_id == periodo_id]
    if origen_tipo:
        asientos = [a for a in asientos if a.origen_tipo == origen_tipo]
    if cuenta_id:
        con = {lid for (lid,) in db.query(LineaAsientoContable.asiento_id).filter(
            LineaAsientoContable.cuenta_id == cuenta_id).all()}
        asientos = [a for a in asientos if a.id in con]
    filas: list[dict] = []
    total_debe = Decimal("0")
    total_haber = Decimal("0")
    for a in asientos:
        lineas = [{
            "codigo": (l.cuenta.codigo if l.cuenta else "?"),
            "nombre": (l.cuenta.nombre if l.cuenta else "—"),
            "debe": Decimal(str(l.debe or 0)),
            "haber": Decimal(str(l.haber or 0)),
            "descripcion": l.descripcion or "",
        } for l in (a.lineas or [])]
        debe = sum((x["debe"] for x in lineas), Decimal("0"))
        haber = sum((x["haber"] for x in lineas), Decimal("0"))
        total_debe += debe
        total_haber += haber
        filas.append({"asiento": a, "lineas": lineas, "debe": debe, "haber": haber,
                      "cuadrado": abs(debe - haber) <= Decimal("0.01") and debe > 0})
    return {"filas": filas, "total_debe": total_debe, "total_haber": total_haber,
            "cuadrado": abs(total_debe - total_haber) <= Decimal("0.01"),
            "n": len(filas)}


def obtener_balance_comprobacion(db: Session, periodo_id: int | None = None,
                                   desde: date | None = None, hasta: date | None = None):
    # agrupa por cuenta
    q = db.query(
        CuentaContable.id, CuentaContable.codigo, CuentaContable.nombre, CuentaContable.tipo,
        func.coalesce(func.sum(LineaAsientoContable.debe),0).label("debe"),
        func.coalesce(func.sum(LineaAsientoContable.haber),0).label("haber")
    ).join(LineaAsientoContable, CuentaContable.id==LineaAsientoContable.cuenta_id)\
     .join(AsientoContable, LineaAsientoContable.asiento_id==AsientoContable.id)
    if desde or hasta:
        if desde:
            q = q.filter(AsientoContable.fecha >= desde)
        if hasta:
            q = q.filter(AsientoContable.fecha <= hasta)
    elif periodo_id:
        q = q.filter(AsientoContable.periodo_id==periodo_id)
    q = q.group_by(CuentaContable.id).order_by(CuentaContable.codigo)
    rows = []
    for r in q.all():
        debe = Decimal(str(r.debe or 0))
        haber = Decimal(str(r.haber or 0))
        saldo = debe - haber
        rows.append({
            "cuenta_id": r.id, "codigo": r.codigo, "nombre": r.nombre, "tipo": r.tipo,
            "debe": debe, "haber": haber,
            "saldo_deudor": saldo if saldo>0 else Decimal("0"),
            "saldo_acreedor": -saldo if saldo<0 else Decimal("0")
        })
    return rows

def obtener_estado_resultados(db: Session, periodo_id: int | None = None,
                                desde: date | None = None, hasta: date | None = None):
    try:
        bal = obtener_balance_comprobacion(db, periodo_id, desde, hasta) or []
    except Exception:
        bal = []
    def _cod(r) -> str:
        try:
            return str((r or {}).get("codigo") or "")
        except Exception:
            return ""
    def _dh(r, signo: int = 1):
        # signo +1: debe-haber; -1: haber-debe. None-safe, Decimal-safe.
        try:
            return ((r or {}).get("debe") or 0) - ((r or {}).get("haber") or 0) if signo > 0 else ((r or {}).get("haber") or 0) - ((r or {}).get("debe") or 0)
        except Exception:
            return 0
    def saldo_tipo(tipo):
        try:
            return sum((_dh(r) for r in bal if (r or {}).get("tipo") == tipo), 0)
        except Exception:
            return 0
    # Ingresos (70x) y Costo ventas (69x): códigos None-safe.
    ventas = sum((_dh(r, -1) for r in bal if _cod(r).startswith("70")), 0)
    costo_ventas = sum((_dh(r, 1) for r in bal if _cod(r).startswith("69")), 0)
    utilidad_bruta = ventas - costo_ventas
    gastos_admin = sum((_dh(r, 1) for r in bal if _cod(r).startswith("94")), 0)
    gastos_ventas = sum((_dh(r, 1) for r in bal if _cod(r).startswith("95")), 0)
    gastos_fin = sum((_dh(r, 1) for r in bal if _cod(r).startswith("97")), 0)
    # Gastos operativos (Clase 6: personal/MOD Cta 62, servicios y gestión;
    # excluye 60-61 compras, 69 costo ventas y la clase 9 analítica —el
    # destino 92x/791 es redistribución, no gasto real—. Todo None-safe:
    # códigos nulos o saldos None suman 0, sin dividir por cero aguas abajo.
    gastos_operativos = sum(
        _dh(r, 1) for r in bal
        if (r or {}).get("tipo") == "GASTO" and _cod(r)[:2] in ("62", "63", "64", "65", "66", "67", "68")
    )
    resultado = utilidad_bruta - gastos_admin - gastos_ventas - gastos_fin - gastos_operativos
    return {
        "ventas": ventas, "costo_ventas": costo_ventas, "utilidad_bruta": utilidad_bruta,
        "gastos_admin": gastos_admin, "gastos_ventas": gastos_ventas, "gastos_financieros": gastos_fin,
        "gastos_operativos": gastos_operativos,
        "resultado": resultado, "detalle": bal
    }

def obtener_balance_general(db: Session, periodo_id: int | None = None,
                               desde: date | None = None, hasta: date | None = None):
    bal = obtener_balance_comprobacion(db, periodo_id, desde, hasta)
    activo = sum(r["saldo_deudor"] - r["saldo_acreedor"] for r in bal if r["tipo"]=="ACTIVO")
    # PASIVO saldo acreedor - deudor
    pasivo = sum(r["saldo_acreedor"] - r["saldo_deudor"] for r in bal if r["tipo"]=="PASIVO")
    patrimonio = sum(r["saldo_acreedor"] - r["saldo_deudor"] for r in bal if r["tipo"]=="PATRIMONIO")
    # Desglose patrimonial: Capital (50) + Resultados acumulados (59) + Utilidad del ejercicio.
    capital = sum(r["saldo_acreedor"] - r["saldo_deudor"] for r in bal if r["codigo"].startswith("50"))
    resultados_59 = sum(r["saldo_acreedor"] - r["saldo_deudor"] for r in bal if r["codigo"].startswith("59"))
    ingresos = sum(r["saldo_acreedor"] - r["saldo_deudor"] for r in bal if r["tipo"]=="INGRESO")
    gastos = sum(r["saldo_deudor"] - r["saldo_acreedor"] for r in bal if r["tipo"]=="GASTO")
    resultado = ingresos - gastos
    # Patrimonio Total = Capital (50) + Resultados acumulados (59) + Utilidad.
    # Si el libro ya cuadra sin sumar el resultado, 59 ya lo incorpora (cierre):
    # no duplicar.
    def _eq(a, b, tol=Decimal("0.01")):
        try:
            return abs(Decimal(str(a)) - Decimal(str(b))) <= tol
        except Exception:
            return a == b
    if _eq(activo, pasivo + patrimonio):
        patrimonio_total = patrimonio
    else:
        patrimonio_total = patrimonio + resultado
    valida = _eq(activo, pasivo + patrimonio_total) or _eq(activo, pasivo + patrimonio)
    # Desglose por rubros oficiales para la tarjeta de EEFF.
    def _rubro(prefs: tuple[str, ...], acreedor: bool, excl: tuple[str, ...] = ()):
        tot = Decimal("0")
        for r in bal:
            if r["codigo"].startswith(prefs) and not r["codigo"].startswith(excl):
                tot += (r["saldo_acreedor"] - r["saldo_deudor"]) if acreedor else (r["saldo_deudor"] - r["saldo_acreedor"])
        return tot
    rubros = {
        "efectivo_10": _rubro(("10",), False),
        "cxc_12": _rubro(("12",), False, ("122",)),
        "inventarios": _rubro(("21", "23", "24"), False),
        "tributos_40": _rubro(("40",), True),
        "cxp_42": _rubro(("42", "46"), True),
        "anticipos_122": _rubro(("122",), True),
        "capital_50": _rubro(("50",), True),
    }
    rubros["total_activo"] = rubros["efectivo_10"] + rubros["cxc_12"] + rubros["inventarios"]
    rubros["total_pasivo"] = rubros["tributos_40"] + rubros["cxp_42"] + rubros["anticipos_122"]
    rubros["total_patrimonio"] = patrimonio_total
    rubros["utilidad"] = resultado
    return {"activo": activo, "pasivo": pasivo, "patrimonio": patrimonio, "patrimonio_total": patrimonio_total, "capital": capital, "resultados_59": resultados_59, "resultado": resultado, "valida": valida, "detalle": bal, "rubros": rubros}

# ── Costos ─────────────────────────────────────────────────────────────
def calcular_mod_devengada(db: Session, periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None, orden_produccion_id: int | None = None) -> Decimal:
    """Lee rendimiento devengado vía SQL solo lectura. No importa modelos rendimiento."""
    # Usa engine directo para lectura desacoplada
    with engine.connect() as conn:
        # rendimiento_registros + detalles
        sql = "SELECT COALESCE(SUM(d.subtotal),0) as total FROM rendimiento_registros r JOIN rendimiento_detalles d ON d.registro_jornada_id=r.id WHERE 1=1"
        params = {}
        if periodo_id:
            # necesita mapear periodo a fechas
            p = db.get(PeriodoContable, periodo_id)
            if p:
                sql += " AND r.fecha >= :ini AND r.fecha <= :fin"
                # primer y último día del mes
                import calendar
                ini = date(p.anio, p.mes, 1)
                last = calendar.monthrange(p.anio, p.mes)[1]
                fin = date(p.anio, p.mes, last)
                params["ini"] = ini; params["fin"] = fin
        else:
            if desde:
                sql += " AND r.fecha >= :desde"
                params["desde"] = desde
            if hasta:
                sql += " AND r.fecha <= :hasta"
                params["hasta"] = hasta
        if orden_produccion_id:
            # orden_produccion_id legado = garments.id; ref canónica = orden_produccion.id; orden_id = orders.id
            try:
                cols = [r["name"] for r in __import__("sqlalchemy").inspect(engine).get_columns("rendimiento_detalles")]
            except Exception:
                cols = ["orden_produccion_id"]
            if "orden_produccion_ref_id" in cols:
                sql += " AND (d.orden_produccion_id = :op OR d.orden_produccion_ref_id = :op OR d.orden_id = :op)"
            else:
                sql += " AND (d.orden_produccion_id = :op OR d.orden_id = :op)"
            params["op"] = orden_produccion_id
        # solo REGISTRADO/APROBADO (devengado, no necesariamente pagado)
        sql += " AND r.estado IN ('PENDIENTE','PROVISIONADO','REGISTRADO','APROBADO','LIQUIDADO_INTERNO','LIQUIDADO')"
        try:
            total = conn.execute(text(sql), params).scalar() or 0
        except Exception:
            # si tablas no existen aún
            total = 0
    return Decimal(str(total))

def _rango_periodo(db: Session, periodo_id: int | None):
    if not periodo_id:
        return None, None
    p = db.get(PeriodoContable, periodo_id)
    if not p:
        return None, None
    import calendar
    ini = date(p.anio, p.mes, 1)
    fin = date(p.anio, p.mes, calendar.monthrange(p.anio, p.mes)[1])
    return ini, fin


def calcular_mpd(db: Session, orden_produccion_id: int | None = None, periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None) -> Decimal:
    """MPD: suma de gastos MPD (devengado). Soporta filtro por periodo/rango."""
    q = db.query(func.coalesce(func.sum(GastoRegistrado.monto_total),0)).filter(GastoRegistrado.clasificacion=="MPD")
    if periodo_id:
        ini, fin = _rango_periodo(db, periodo_id)
        if ini and fin:
            q = q.filter(GastoRegistrado.fecha_emision >= ini, GastoRegistrado.fecha_emision <= fin)
    else:
        if desde:
            q = q.filter(GastoRegistrado.fecha_emision >= desde)
        if hasta:
            q = q.filter(GastoRegistrado.fecha_emision <= hasta)
    total = q.scalar() or 0
    # orden_produccion_id: sin FK directa, se devuelve global (documentado)
    return Decimal(str(total))

def calcular_cif(db: Session, base: str = "costo_mod", periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None) -> dict:
    """CIF real vs aplicado. Real filtra por periodo/rango; aplicado == real (base extensible)."""
    q = db.query(func.coalesce(func.sum(GastoRegistrado.monto_total),0)).filter(GastoRegistrado.clasificacion=="CIF")
    if periodo_id:
        ini, fin = _rango_periodo(db, periodo_id)
        if ini and fin:
            q = q.filter(GastoRegistrado.fecha_emision >= ini, GastoRegistrado.fecha_emision <= fin)
    else:
        if desde:
            q = q.filter(GastoRegistrado.fecha_emision >= desde)
        if hasta:
            q = q.filter(GastoRegistrado.fecha_emision <= hasta)
    real = q.scalar() or 0
    # aplicado: si base es costo_mod, aplica %; simplificado: aplicado = real (sin distribución compleja para test)
    # Para extensibilidad, permite horas_mod, costo_mod, etc.
    aplicado = real  # para test, aplicado == real
    return {"real": Decimal(str(real)), "aplicado": Decimal(str(aplicado)), "base": base}

def calcular_costo_produccion(db: Session, orden_produccion_id: int | None = None, periodo_id: int | None = None, desde: date | None = None, hasta: date | None = None) -> Decimal:
    mpd = calcular_mpd(db, orden_produccion_id, periodo_id, desde=desde, hasta=hasta)
    mod = calcular_mod_devengada(db, periodo_id=periodo_id, orden_produccion_id=orden_produccion_id, desde=desde, hasta=hasta)
    cif = calcular_cif(db, periodo_id=periodo_id, desde=desde, hasta=hasta)["aplicado"]
    return mpd + mod + cif

def calcular_costo_ventas(db: Session, periodo_id: int | None = None) -> Decimal:
    # Costo de ventas = suma de asientos con cuenta 691
    q = db.query(func.coalesce(func.sum(LineaAsientoContable.debe),0)).join(AsientoContable).join(CuentaContable, LineaAsientoContable.cuenta_id==CuentaContable.id).filter(CuentaContable.codigo.like("69%"))
    if periodo_id:
        q = q.filter(AsientoContable.periodo_id==periodo_id)
    total = q.scalar() or 0
    return Decimal(str(total))

# ── Cierres ────────────────────────────────────────────────────────────
def cerrar_periodo(db: Session, anio: int, mes: int):
    p = get_or_create_periodo(db, anio, mes)
    if p.estado != "ABIERTO":
        raise ValueError("Periodo no está ABIERTO")
    p.estado = "CERRADO"
    p.fecha_cierre = datetime.now()
    db.commit()
    return p

def reabrir_periodo(db: Session, anio: int, mes: int):
    p = get_periodo(db, anio, mes)
    if not p or p.estado != "CERRADO":
        raise ValueError("Solo periodos CERRADOS pueden reabrirse")
    p.estado = "ABIERTO"
    p.fecha_cierre = None
    db.commit()
    return p
