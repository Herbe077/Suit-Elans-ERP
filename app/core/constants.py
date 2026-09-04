"""Constantes de dominio: roles, estados, catálogos y SAM estándar."""

ROLES = ("ADMIN", "VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "FINANZAS",
         "admin", "sastre", "taller", "ventas", "almacen", "gerente", "contador", "vendedor", "recepcion", "almacenero", "consulta")
ROLES_CANONICOS = ("ADMIN", "VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "FINANZAS")
ROLES_FINANZAS_PERMITIDOS = ("ADMIN", "FINANZAS", "admin", "gerente", "contador", "GERENTE", "CONTADOR")
ROLES_FINANZAS_BLOQUEADOS = ("VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE", "ALMACEN", "vendedor", "recepcion", "sastre", "taller", "almacen")

ORDER_STATUS = (
    "cotizado",
    "confirmado",
    "corte",
    "confeccion",
    "prueba",
    "terminado",
    "entregado",
    "cancelado",
)

GARMENT_TYPES = ("saco", "pantalon", "chaleco", "camisa", "abrigo", "smoking")

# Flujo real de sastrería a medida (12 pasos). Valores legacy
# ("pendiente", "en_proceso", "pausado", "terminado") se conservan por compatibilidad.
WORKFLOW_STATES = (
    "pendiente",      # 0 asesoría / por iniciar
    "diseno",         # 1 definición + diseño
    "patronaje",      # 2 patronaje
    "corte",          # 3 corte
    "confeccion",     # 4 confección (en_proceso legacy mapea aquí)
    "fitting_1",      # 5 primera prueba
    "ajustes",        # 6 ajustes
    "fitting_2",      # 7 segunda prueba
    "terminados",     # 8 terminados
    "calidad",        # 9 control de calidad
    "entregado",      # 10 entrega final
    "pausado",
    "terminado",      # legacy: equivale a calidad superada
    "en_proceso",     # legacy: equivale a confeccion
)
GARMENT_STATUS = WORKFLOW_STATES

# --- Tablero Kanban de producción (7 fases canónicas) ---
TALLER_KANBAN: list[tuple[str, str]] = [
    ("POR_CORTAR", "Por Cortar"),
    ("EN_CORTE", "En Corte"),
    ("ARMADO_HILVAN", "Armado / Hilván"),
    ("EN_PRUEBA", "En Prueba"),
    ("EN_CONFECCION", "En Confección"),
    ("ACABADOS", "Acabados y Planchado"),
    ("CALIDAD_OK", "Listo para Entrega"),
]
KANBAN_STATES = [k for k, _ in TALLER_KANBAN]
# Etapas que ya superaron "En Prueba" (destajo posterior al fitting)
ETAPAS_POST_PRUEBA = ("EN_CONFECCION", "ACABADOS", "CALIDAD_OK")
# Valores legacy/detallados equivalentes (ver KANBAN_MAP)
ETAPAS_POST_PRUEBA_TODAS = ("EN_CONFECCION", "ACABADOS", "CALIDAD_OK",
                            "ajustes", "fitting_2", "terminados", "calidad",
                            "terminado", "listo")
# Mapeo de estados legacy/detallados a columna kanban
KANBAN_MAP = {
    "POR_CORTAR": "POR_CORTAR", "pendiente": "POR_CORTAR", "diseno": "POR_CORTAR",
    "patronaje": "POR_CORTAR", "pausado": "POR_CORTAR",
    "EN_CORTE": "EN_CORTE", "corte": "EN_CORTE",
    "ARMADO_HILVAN": "ARMADO_HILVAN", "confeccion": "ARMADO_HILVAN",
    "en_proceso": "ARMADO_HILVAN",
    "EN_PRUEBA": "EN_PRUEBA", "fitting_1": "EN_PRUEBA", "prueba_1": "EN_PRUEBA",
    "EN_CONFECCION": "EN_CONFECCION", "ajustes": "EN_CONFECCION",
    "fitting_2": "EN_CONFECCION",
    "ACABADOS": "ACABADOS", "terminados": "ACABADOS", "calidad": "ACABADOS",
    "terminado": "ACABADOS",
    "CALIDAD_OK": "CALIDAD_OK", "listo": "CALIDAD_OK",
}

# --- Ficha técnica: catálogos de diseño ---
SOLAPAS = ("muesca", "pico (peak lapel)", "esmoquin (chal)", "discontinua")
BOLSILLOS_OPTS = ("rectos con solapa", "inclinados", "de ojal/ribete", "parche", "ticket")
FORROS_OPTS = ("completo", "medio forro", "sin forro")
RESPIRADEROS_OPTS = ("sin abertura", "abertura simple", "abertura doble")

# --- Fit Tests: zonas de corrección por tipo de prenda ---
FIT_ZONES_SACO = (("hombros", "Hombros"), ("sisa", "Caja de sisa"),
                  ("manga", "Largo de manga"), ("talle", "Entalle de talle"),
                  ("solapa", "Solapa / cuello"))
FIT_ZONES_PANTALON = (("tiro", "Caja de tiro"), ("cintura", "Cintura"),
                      ("muslo", "Muslo"), ("bastas", "Bastas / ruedo"))
FIT_ZONES_OTROS = (("general", "General"),)

# --- Control de calidad pre-entrega (6 puntos) ---
QC_CHECKLIST = (
    "Medidas finales vs. ficha",
    "Planchado a vapor y asentado de costuras",
    "Limpieza de hilos e hilvanes",
    "Caída de forro y bolsillos funcionales",
    "Ojales y botones alineados",
    "Colgado en gancho y funda",
)

ORDER_CANALES = ("sastreria", "comercial", "corporativo")

# --- CRM / Comercial unificado ---
LEAD_ORIGINS = ("web", "referido", "redes", "visita_tienda", "corporativo", "otro",
                "instagram", "recomendacion", "organico", "empresa_b2b", "whatsapp")
# Pipeline de oportunidades (migra valores legacy: ver LEAD_STATUS_MAP)
LEAD_STATUS = ("PROSPECTO", "CITA_AGENDADA", "COTIZACION_ENVIADA",
               "GANADO_EN_TALLER", "PERDIDO")
LEAD_STATUS_MAP = {"nuevo": "PROSPECTO", "contactado": "CITA_AGENDADA",
                   "cotizado": "COTIZACION_ENVIADA", "ganado": "GANADO_EN_TALLER",
                   "perdido": "PERDIDO"}
LEAD_OPEN = ("PROSPECTO", "CITA_AGENDADA", "COTIZACION_ENVIADA")
INTERACCION_TIPOS = ("llamada", "whatsapp", "nota", "visita")
CLASIFICACION_CLIENTE = ("Nuevo", "Frecuente", "VIP")
QUOTATION_STATUS = ("borrador", "enviada", "aprobada", "rechazada", "vencida", "convertida")
LINE_CATEGORIES = ("prenda_medida", "prenda_comercial", "servicio")

# --- Catálogo comercial ---
PRODUCT_LINES = ("medida", "comercial")
SIZE_LIST = ("XS", "S", "M", "L", "XL", "XXL", "28", "30", "32", "34", "36", "38", "40", "42", "44", "U")

# --- Localización Perú ---
MONEDA = "S/"
MONEDA_NOMBRE = "Soles"
DOC_TYPES_CLIENTE = ("DNI", "RUC", "CE", "PASAPORTE")

# --- Compras / caja / facturación ---
PO_STATUS = ("borrador", "enviada", "recibida_parcial", "recibida", "cancelada")
CASH_TIPOS = ("ingreso", "egreso")
PAYMENT_METHODS = ("efectivo", "transferencia", "tarjeta", "yape", "plin", "credito")
INVOICE_SERIES = ("B001", "F001")  # boleta / factura internas (no electrónicas)
IGV_PCT = 18.0

# --- Cierre de jornada taller: catálogo maestro (descripción, tarifa S/) ---
TALLER_TAREAS: list[tuple[str, float]] = [
    ("Corte, marcado y habilitacion de espalda", 2.29),
    ("Corte, marcado y habilitacion de mangas", 1.60),
    ("Corte, marcado y habilitacion de contrapecho", 4.13),
    ("Corte, marcado y habilitacion de cuello y bolsillos", 3.44),
    ("Costura base espalda, manga, delantero y contrapecho", 2.29),
    ("Costura de bolsillos", 1.15),
    ("Confección de mangas", 2.75),
    ("Puntada de refuerso magas", 0.46),
    ("Costura de unión mangas", 0.69),
    ("Planchado y aplicacion de adhesivo delantero", 1.83),
    ("Planchado y aplicacion de adhesivo contrapecho", 2.75),
    ("Planchado y aplicacion de adhesivo espalda", 1.15),
    ("Planchado y aplicacion de adhesivo manga", 1.83),
    ("Confección de bolisllos y cartera de delantero", 4.13),
    ("Armado y puntada de cartera de delantero", 1.60),
    ("Marcado final de delantero", 1.15),
    ("Moldeado de estructura de refuerzo", 2.29),
    ("Armado de estructura de refuerzo", 1.60),
    ("Montaje externo de delantero, costadillo, espalda y cuello", 2.75),
    ("Ribeteado e hilvanado de contrapechos", 3.21),
    ("Montaje de contrapecho en forro interno", 1.38),
    ("Confección de bolisllos de contrapecho interno", 4.58),
    ("Montaje interno de contrapecho, costadillo, espalda y cuello", 2.29),
    ("Planchado de montaje externo", 2.75),
    ("Planchado de montaje interno", 1.60),
    ("Planchado de mangas", 2.75),
    ("Hilvanado de unión externo e interno", 2.29),
    ("Montaje de unión externo e interno", 3.44),
    ("Puntada de refuerso de bastas bajas", 1.15),
    ("Pespunte de refuerzo bordes de unión", 2.29),
    ("Hilvanado de bordes de unión", 3.44),
    ("Planchado intermedio de fijacion", 1.15),
    ("Hilvanado de refuerzo contrapecho.", 1.15),
    ("Puntada de refuerzo de contrapecho y cuello", 2.75),
    ("Hilvanado de abeturas traseras y otros", 1.83),
    ("Montaje de hilvanado de unión en mangas", 6.88),
    ("Borradado de plieges de sisa en manga", 1.83),
    ("Costura de unión mangas", 1.83),
    ("Montaje de ruerzo de hombros y fijacion", 3.90),
    ("Pespunte para forrado de hombros y sisa", 3.44),
    ("Marcado y puntada de ojales", 3.90),
    ("Limpieza de hilos de hilvanado y otras", 1.15),
    ("Planchado final a vapor", 5.73),
    ("Pegado de botones", 3.44),
]

APPOINTMENT_TYPES = ("TOMA_MEDIDAS", "PRIMERA_PRUEBA", "SEGUNDA_PRUEBA",
                     "ENTREGA_FINAL", "AJUSTE")
APPOINTMENT_TYPES_MAP = {"toma_medidas": "TOMA_MEDIDAS",
                         "primera_prueba": "PRIMERA_PRUEBA",
                         "segunda_prueba": "SEGUNDA_PRUEBA", "entrega": "ENTREGA_FINAL",
                         "ajuste": "AJUSTE"}
APPOINTMENT_STATUS = ("PROGRAMADA", "CONFIRMADA", "ASISTIO", "REPROGRAMADA",
                      "CANCELADA", "NO_ASISTIO")
APPOINTMENT_STATUS_MAP = {"programada": "PROGRAMADA", "confirmada": "CONFIRMADA",
                          "realizada": "ASISTIO", "cancelada": "CANCELADA",
                          "no_asistio": "NO_ASISTIO"}

MOVEMENT_TYPES = ("entrada", "salida", "ajuste", "merma", "reserva", "liberacion")

# Medidas anatómicas (cm) para el formulario de sastrería bespoke
MEASURE_FIELDS_SACO = [
    ("cuello", "Cuello"),
    ("hombro", "Hombro"),
    ("sisa", "Sisa"),
    ("pecho", "Pecho"),
    ("cintura_saco", "Cintura"),
    ("cadera", "Cadera"),
    ("largo_manga", "Largo manga"),
    ("ancho_manga", "Ancho manga"),
    ("largo_espalda", "Largo espalda"),
    ("largo_saco", "Largo saco"),
    ("caida_hombro", "Caída hombro"),
]
MEASURE_FIELDS_PANTALON = [
    ("cintura_pantalon", "Cintura pantalón"),
    ("cadera_pantalon", "Cadera pantalón"),
    ("tiro", "Tiro"),
    ("muslo", "Muslo"),
    ("rodilla", "Rodilla"),
    ("tobillo", "Tobillo / bajo"),
    ("largo_pantalon", "Largo pantalón"),
]
ALL_MEASURE_FIELDS = [k for k, _ in MEASURE_FIELDS_SACO + MEASURE_FIELDS_PANTALON]

# SAM estándar (minutos) por operación — base para tablero MES y liquidaciones
# Ajustar con cronometrajes reales del taller.
DEFAULT_OPERATIONS: list[dict] = [
    {"codigo": "COR-01", "nombre": "Tendido y corte saco", "tipo_prenda": "saco", "sam_minutos": 90.0},
    {"codigo": "COR-02", "nombre": "Tendido y corte pantalón", "tipo_prenda": "pantalon", "sam_minutos": 45.0},
    {"codigo": "ENT-01", "nombre": "Fusionado / entretelado", "tipo_prenda": "saco", "sam_minutos": 35.0},
    {"codigo": "CONF-01", "nombre": "Armado cuerpo saco", "tipo_prenda": "saco", "sam_minutos": 240.0},
    {"codigo": "CONF-02", "nombre": "Pegado mangas + hombros", "tipo_prenda": "saco", "sam_minutos": 90.0},
    {"codigo": "CONF-03", "nombre": "Confección pantalón", "tipo_prenda": "pantalon", "sam_minutos": 150.0},
    {"codigo": "OJAL-01", "nombre": "Ojales + botones", "tipo_prenda": "saco", "sam_minutos": 40.0},
    {"codigo": "PLAN-01", "nombre": "Planchado final + control calidad", "tipo_prenda": "saco", "sam_minutos": 45.0},
    {"codigo": "AJU-01", "nombre": "Ajustes de prueba", "tipo_prenda": "saco", "sam_minutos": 60.0},
]

# Consumo estimado de tela (m) por prenda — para reserva automática
CONSUMO_TELA_M = {"saco": 2.0, "pantalon": 1.4, "chaleco": 1.0, "camisa": 1.8, "abrigo": 2.8, "smoking": 2.2}
