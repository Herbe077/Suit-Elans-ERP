"""Plan Contable Operativo + matriz de reglas (Clean Slate).

Fuente única del seed `seed_pcge_nuevo.py`. Desvíos documentados vs el
pedido original:
- Sin tabla `plan_cuentas` separada: se usa `cuentas_contables` (id PK +
  codigo UNIQUE) con columnas `padre_codigo`, `imputable`, `analitica`.
- Sin `entidad_id` único: `cliente_id` + `proveedor_id` con FK reales.
- `producto_id` es INTEGER polimórfico (insumo o variante según regla).
- Cuentas 7011/7032/6911/6011/7911 del plan anterior NO existen: las
  reemplazan 7021/6921x/6021/791 (hojas). 2111 se conserva como PT.
- Patrones "x" resuelven a la ÚNICA hoja imputable bajo el prefijo.
"""
# (codigo, nombre, tipo, nivel, padre, imputable, analitica, elemento)
PLAN_CUENTAS: list[tuple] = [
    # ── 10 Efectivo ──
    ("10", "Efectivo y equivalentes", "ACTIVO", 1, None, False, False, 1),
    ("101", "Caja", "ACTIVO", 2, "10", False, False, 1),
    ("1011", "Caja Operativa", "ACTIVO", 3, "101", True, True, 1),
    ("1012", "Caja Chica", "ACTIVO", 3, "101", True, False, 1),
    ("104", "Cuentas corrientes", "ACTIVO", 2, "10", False, False, 1),
    ("1041", "CC Operativa Principal", "ACTIVO", 3, "104", True, True, 1),
    ("1042", "CC Secundaria", "ACTIVO", 3, "104", True, False, 1),
    # ── 12 CxC ──
    ("12", "Cuentas por cobrar comerciales", "ACTIVO", 1, None, False, False, 1),
    ("121", "Clientes", "ACTIVO", 2, "12", False, False, 1),
    ("1212", "Emitidas en Cartera", "ACTIVO", 3, "121", True, True, 1),
    ("122", "Anticipos de clientes", "PASIVO", 2, "12", False, False, 4),
    ("1221", "Anticipos de Clientes", "PASIVO", 3, "122", True, True, 4),
    # ── 20 Mercaderías / 24 MP ──
    ("24", "Materias primas", "ACTIVO", 1, None, False, False, 2),
    ("241", "Materias primas", "ACTIVO", 2, "24", False, False, 2),
    ("2411", "Materia Prima - Telas y Avíos", "ACTIVO", 3, "241", True, True, 2),
    ("25", "Materiales auxiliares", "ACTIVO", 1, None, False, False, 2),
    ("252", "Materiales auxiliares", "ACTIVO", 2, "25", False, False, 2),
    ("2521", "Materiales auxiliares", "ACTIVO", 3, "252", True, True, 2),
    # ── 23 WIP / 21 PT ──
    ("23", "Productos en proceso", "ACTIVO", 1, None, False, False, 2),
    ("231", "WIP Manufactura", "ACTIVO", 2, "23", False, False, 2),
    ("2311", "WIP Sastrería", "ACTIVO", 3, "231", True, True, 2),
    ("21", "Productos terminados", "ACTIVO", 1, None, False, False, 2),
    ("211", "PT Manufactura", "ACTIVO", 2, "21", False, False, 2),
    ("2111", "Productos Terminados Sastrería", "ACTIVO", 3, "211", True, True, 2),
    # ── 33 IME / 39 depreciación ──
    ("33", "Inmuebles, maquinaria y equipo", "ACTIVO", 1, None, False, False, 3),
    ("334", "Maquinaria y equipos", "ACTIVO", 2, "33", False, False, 3),
    ("3341", "Maquinaria y Equipos - Costo", "ACTIVO", 3, "334", True, True, 3),
    ("335", "Equipos y unidades diversas", "ACTIVO", 2, "33", False, False, 3),
    ("3351", "Activo Fijo Adquirido", "ACTIVO", 3, "335", True, True, 3),
    ("39", "Depreciación acumulada", "ACTIVO", 1, None, False, False, 3),
    ("391", "Depreciación acumulada IME", "ACTIVO", 2, "39", False, False, 3),
    ("3911", "Depreciación acumulada IME", "ACTIVO", 3, "391", True, False, 3),
    # ── 40 Tributos ──
    ("40", "Tributos por pagar", "PASIVO", 1, None, False, False, 4),
    ("401", "IGV por pagar", "PASIVO", 2, "40", False, False, 4),
    ("403", "Instituciones públicas", "PASIVO", 2, "40", False, False, 4),
    ("4031", "EsSalud por pagar", "PASIVO", 3, "403", True, True, 4),
    ("4032", "ONP/AFP por pagar", "PASIVO", 3, "403", True, True, 4),
    ("4011", "IGV ventas", "PASIVO", 3, "401", False, False, 4),
    ("40111", "IGV - Cuenta Propia", "PASIVO", 4, "4011", True, False, 4),
    ("4017", "Retenciones por pagar", "PASIVO", 2, "40", False, False, 4),
    ("40172", "Retención IR 4ta categoría - RxH", "PASIVO", 3, "4017", True, False, 4),
    # ── 42 Proveedores / 41 personal / 46 diversos ──
    ("42", "Cuentas por pagar comerciales", "PASIVO", 1, None, False, False, 4),
    ("421", "Proveedores", "PASIVO", 2, "42", False, False, 4),
    ("4212", "Emitidas - Proveedores", "PASIVO", 3, "421", True, True, 4),
    ("424", "Honorarios por pagar", "PASIVO", 2, "42", False, False, 4),
    ("4241", "Honorarios por pagar - RxH destajo", "PASIVO", 3, "424", True, True, 4),
    ("41", "Remuneraciones por pagar", "PASIVO", 1, None, False, False, 4),
    ("411", "Remuneraciones", "PASIVO", 2, "41", False, False, 4),
    ("4111", "Remuneraciones por pagar", "PASIVO", 3, "411", True, True, 4),
    ("46", "Cuentas por pagar diversas", "PASIVO", 1, None, False, False, 4),
    ("469", "Otras cuentas por pagar", "PASIVO", 2, "46", False, False, 4),
    ("4699", "Otras cuentas por pagar diversas", "PASIVO", 3, "469", True, True, 4),
    ("465", "Pasivo por compra de activo fijo", "PASIVO", 2, "46", False, False, 4),
    ("4654", "Pasivo por compra de activo fijo", "PASIVO", 3, "465", True, True, 4),
    # ── 50/59 Patrimonio ──
    ("50", "Capital", "PATRIMONIO", 1, None, False, False, 5),
    ("501", "Capital social", "PATRIMONIO", 2, "50", False, False, 5),
    ("5011", "Capital Social", "PATRIMONIO", 3, "501", True, False, 5),
    ("59", "Resultados", "PATRIMONIO", 1, None, False, False, 5),
    ("5911", "Utilidades Acumuladas", "PATRIMONIO", 2, "59", True, False, 5),
    # ── 60 Compras / 61 variación ──
    ("60", "Compras", "GASTO", 1, None, False, False, 6),
    ("602", "Compras materia prima", "GASTO", 2, "60", False, False, 6),
    ("6021", "Compras Materia Prima", "GASTO", 3, "602", True, True, 6),
    ("603", "Compras avíos y suministros", "GASTO", 2, "60", False, False, 6),
    ("6032", "Compras Avíos y Suministros", "GASTO", 3, "603", True, True, 6),
    ("61", "Variación de existencias", "GASTO", 1, None, False, False, 6),
    ("611", "Variación MP", "GASTO", 2, "61", False, False, 6),
    ("6111", "Variación de existencias - Materias primas", "GASTO", 3, "611", True, True, 6),
    # ── 62/63/65 gastos ──
    ("62", "Gastos de personal", "GASTO", 1, None, False, False, 6),
    ("621", "MOD", "GASTO", 2, "62", False, False, 6),
    ("6211", "Sueldos y Salarios (MOD)", "GASTO", 3, "621", True, True, 6),
    ("627", "Seguridad y previsión social", "GASTO", 2, "62", False, False, 6),
    ("6271", "EsSalud Empleador", "GASTO", 3, "627", True, True, 6),
    ("63", "Gastos servicios", "GASTO", 1, None, False, False, 6),
    ("631", "CIF Servicios taller", "GASTO", 2, "63", False, False, 6),
    ("6311", "Servicios Básicos y Alquileres (CIF)", "GASTO", 3, "631", True, True, 6),
    ("632", "Honorarios", "GASTO", 2, "63", False, False, 6),
    ("6322", "Honorarios Profesionales", "GASTO", 3, "632", True, True, 6),
    ("636", "Servicios básicos", "GASTO", 2, "63", False, False, 6),
    ("6361", "Servicios básicos y tercerizados", "GASTO", 3, "636", True, True, 6),
    ("637", "Publicidad y marketing", "GASTO", 2, "63", False, False, 6),
    ("6371", "Publicidad y Marketing", "GASTO", 3, "637", True, True, 6),
    ("65", "Otros gastos de gestión", "GASTO", 1, None, False, False, 6),
    ("651", "Gastos de gestión", "GASTO", 2, "65", False, False, 6),
    ("6511", "Gastos de Gestión (OPEX)", "GASTO", 3, "651", True, True, 6),
    ("659", "Otros gastos de gestión", "GASTO", 2, "65", False, False, 6),
    ("6591", "Mermas y desmedros", "GASTO", 3, "659", True, True, 6),
    ("6599", "Otros Gastos de Gestión", "GASTO", 3, "659", True, True, 6),
    ("68", "Valuación y deterioro", "GASTO", 1, None, False, False, 6),
    ("681", "Depreciación de activos", "GASTO", 2, "68", False, False, 6),
    ("6811", "Depreciación IME", "GASTO", 3, "681", True, True, 6),
    # ── 69/70/71 ──
    ("69", "Costo de ventas", "GASTO", 1, None, False, False, 6),
    ("692", "Costo de ventas PT", "GASTO", 2, "69", False, False, 6),
    ("6921", "Costo de Ventas - PT Sastrería", "GASTO", 3, "692", True, True, 6),
    ("70", "Ventas", "INGRESO", 1, None, False, False, 7),
    ("702", "Ventas PT", "INGRESO", 2, "70", False, False, 7),
    ("7021", "Ventas PT Sastrería", "INGRESO", 3, "702", True, True, 7),
    ("71", "Producción del ejercicio", "INGRESO", 1, None, False, False, 7),
    ("711", "Variación PT", "INGRESO", 2, "71", False, False, 7),
    ("7111", "Variación - Productos Terminados", "INGRESO", 3, "711", True, False, 7),
    # ── 79 cargas imputables / 9 destino ──
    ("79", "Cargas imputables", "INGRESO", 1, None, False, False, 9),
    ("791", "Cargas imputables MOD", "INGRESO", 2, "79", True, True, 9),
    ("92", "Costos de producción - destino", "GASTO", 1, None, False, False, 9),
    ("921", "Costos indirectos de fabricación", "GASTO", 2, "92", False, False, 9),
    ("9211", "Costo de Producción - Mano de Obra Directa", "GASTO", 3, "921", True, True, 9),
    ("94", "Gastos administrativos - destino", "GASTO", 1, None, False, False, 9),
    ("941", "Gastos administrativos", "GASTO", 2, "94", True, True, 9),
    ("95", "Gastos de ventas - destino", "GASTO", 2, None, False, False, 9),
    ("951", "Gastos de ventas", "GASTO", 2, "95", True, True, 9),
    ("97", "Gastos financieros - destino", "GASTO", 2, None, False, False, 9),
    ("971", "Gastos financieros", "GASTO", 2, "97", True, True, 9),
]

# (codigo_regla, descripcion, debe_patron, haber_patron, dims_obligatorias)
REGLAS: list[tuple] = [
    ("COMPRA_MP", "Compra MP + IGV vs proveedores",
     "602x+40111", "4212", ["proveedor_id"]),
    ("RECEPCION_MP", "Ingreso físico a almacén",
     "2411x", "6111x", ["producto_id", "almacen_id"]),
    ("CONSUMO_MPD", "Consumo MP a WIP",
     "2311x", "2411x", ["producto_id", "almacen_id", "centro_costo_id"]),
    ("CONSUMO_AUX", "Consumo auxiliar a WIP",
     "2311x", "2521x", ["producto_id", "almacen_id", "centro_costo_id"]),
    ("MERMA", "Merma y desmedros",
     "6591x", "2411x", ["producto_id", "almacen_id"]),
    ("IMPUTACION_MOD", "MOD destajo a WIP",
     "2311x", "791", ["orden_produccion_id", "trabajador_id", "centro_costo_id"]),
    ("IMPUTACION_CIF", "CIF a WIP",
     "2311x", "791", ["orden_produccion_id", "centro_costo_id"]),
    ("CIERRE_OP", "Cierre OP: alta PT vs WIP",
     "2111x", "2311x", ["orden_produccion_id", "producto_id"]),
    ("LIQUIDACION_9211", "Liquidación MOD taller 9211 vía 7111",
     "7111", "9211x", ["centro_costo_id"]),
    ("INGRESO_PT", "Alta PT manual (Cta 23 vs 7111)",
     "2311x", "7111", ["producto_id", "almacen_id"]),
    ("VENTA_PT", "Venta PT + IGV",
     "121x", "7021x+40111", ["cliente_id"]),
    ("FACTURA_ANTICIPO", "Factura de anticipo",
     "121x", "40111+1221", ["cliente_id"]),
    ("FACTURA_SALDO", "Factura de saldo (aplica anticipos)",
     "1221+121x", "40111+7021x", ["cliente_id"]),
    ("COSTO_VENTA_PT", "Costo venta colección",
     "6921x", "2111x", ["producto_id", "almacen_id"]),
    ("COSTO_VENTA_BESPOKE", "Costo venta bespoke directo de WIP",
     "6921x", "2311x", []),
    ("COBRO_CAJA", "Cobro en efectivo",
     "1011", "121x", []),
    ("COBRO_BANCO", "Cobro bancario/yape/tarjeta",
     "1041", "121x", []),
    ("PAGO_CAJA", "Pago a proveedor en efectivo",
     "4212", "1011", []),
    ("PAGO_BANCO", "Pago a proveedor por banco",
     "4212", "1041", []),
    ("PLANILLA", "Planilla de remuneraciones",
     "6211x+40111", "4111", ["centro_costo_id"]),
    ("GASTO_ALQUILER", "Alquiler / servicios taller",
     "6311x+40111", "4212", ["proveedor_id"]),
    ("GASTO_ALQUILER_NATURAL", "Alquiler persona natural",
     "6311x+40111", "4699", ["proveedor_id"]),
    ("GASTO_HONORARIOS", "Honorarios profesionales",
     "6322x+40111", "4212", ["proveedor_id"]),
    ("GASTO_RXH", "RxH destajo devengado",
     "6322x+40111", "4241", ["proveedor_id"]),
    ("GASTO_ACTIVO", "Compra de activo fijo",
     "3351x+40111", "4654", ["proveedor_id"]),
    ("GASTO_SERVICIOS", "Servicios básicos",
     "6361x+40111", "4212", ["proveedor_id"]),
    ("GASTO_AVIOS", "Compras avíos y suministros",
     "6032x+40111", "4212", ["proveedor_id"]),
    ("GASTO_PUBLICIDAD", "Publicidad y marketing",
     "6371x+40111", "4212", ["proveedor_id"]),
    ("GASTO_GENERAL", "Gasto operativo general",
     "6511x+40111", "4212", ["proveedor_id"]),
    ("GASTO_OTROS", "Otros gastos de gestión",
     "6599x+40111", "4212", ["proveedor_id"]),
    ("DESTINO_921", "Destino taller",
     "9211", "791", []),
    ("DESTINO_941", "Destino administración",
     "941", "791", []),
    ("DESTINO_951", "Destino comercial",
     "951", "791", []),
    ("DESTINO_971", "Destino financiero",
     "971", "791", []),
    ("DEPRECIACION", "Depreciación IME",
     "6811", "3911", []),
]

CENTROS_DEFAULT = [
    ("921", "Taller: Confección, Ensamblaje y Máquinas", "TALLER"),
    ("922", "Taller: Mesa de Corte y Patronaje", "TALLER"),
    ("923", "Taller: Calidad, Acabados y Planchado", "TALLER"),
    ("941", "Administración y Gestión General", "ADMINISTRACION"),
    ("951", "Comercial: Showroom, Tienda y Mostrador", "COMERCIAL"),
    ("952", "Comercial: Marketing, Catálogo y Publicidad", "COMERCIAL"),
]

ALMACENES_DEFAULT = [("ALM-01", "Almacén Principal")]
PROYECTOS_DEFAULT = [("PROY-GRAL", "Operación General")]


def cargar_plan_operativo(db, desde_cero: bool = False) -> dict:
    """Carga el plan + reglas + maestros mínimos. Idempotente.

    desde_cero=True: vacía cuentas/centros/reglas antes (Clean Slate).
    Valida que cada patrón de regla resuelva a una hoja imputable única.
    """
    from app.models.finanzas import (
        Almacen,
        CentroCosto,
        CuentaContable,
        Proyecto,
        ReglaContable,
    )

    if desde_cero:
        db.query(ReglaContable).delete()
        db.query(CentroCosto).delete()
        db.query(CuentaContable).delete()
        db.flush()
    por_codigo: dict[str, CuentaContable] = {}
    for codigo, nombre, tipo, nivel, padre, imp, ana, elemento in PLAN_CUENTAS:
        c = db.query(CuentaContable).filter(
            CuentaContable.codigo == codigo).first()
        if not c:
            c = CuentaContable(codigo=codigo)
            db.add(c)
        c.nombre, c.tipo, c.nivel = nombre, tipo, nivel
        c.padre_codigo, c.imputable, c.analitica = padre, bool(imp), bool(ana)
        c.elemento, c.es_analitica = elemento, bool(imp)
        c.acepta_movimientos, c.activo = True, True
        por_codigo[codigo] = c
    db.flush()
    for c in por_codigo.values():
        c.cuenta_padre_id = (por_codigo[c.padre_codigo].id
                             if c.padre_codigo in por_codigo else None)
    for codigo, nombre, tipo in CENTROS_DEFAULT:
        if not db.query(CentroCosto).filter(
                CentroCosto.codigo == codigo).first():
            db.add(CentroCosto(codigo=codigo, nombre=nombre, tipo=tipo,
                               activo=True))
    for codigo, nombre in ALMACENES_DEFAULT:
        if not db.query(Almacen).filter(Almacen.codigo == codigo).first():
            db.add(Almacen(codigo=codigo, nombre=nombre, activo=True))
    for codigo, nombre in PROYECTOS_DEFAULT:
        if not db.query(Proyecto).filter(
                Proyecto.codigo == codigo).first():
            db.add(Proyecto(codigo=codigo, nombre=nombre, activo=True))
    for cod, desc, debe, haber, dims in REGLAS:
        ex = db.query(ReglaContable).filter(
            ReglaContable.codigo_regla == cod).first()
        if ex:
            ex.descripcion, ex.debe_patron = desc, debe
            ex.haber_patron, ex.dimensiones_obligatorias = haber, dims
            ex.activa = True
        else:
            db.add(ReglaContable(codigo_regla=cod, descripcion=desc,
                                 debe_patron=debe, haber_patron=haber,
                                 dimensiones_obligatorias=dims, activa=True))
    db.flush()
    from app.services.motor_contable import expandir_patron
    for cod, _d, debe, haber, _m in REGLAS:
        for tok in (debe + "+" + haber).split("+"):
            expandir_patron(db, tok.strip())  # raise si ambiguo/inexistente
    return {"cuentas": len(por_codigo), "reglas": len(REGLAS)}


def asegurar_plan_operativo(db) -> dict | None:
    """Bootstrap de arranque: carga el plan solo si no hay cuentas."""
    from app.models.finanzas import CuentaContable
    if db.query(CuentaContable).count():
        return None
    return cargar_plan_operativo(db, desde_cero=False)
