# Suit Elans ERP — hardening y optimización enterprise

## Backend
- Configuración segura: `SECRET_KEY` obligatoria en producción; generación efímera solo en desarrollo; cookies Secure/SameSite en producción; pool PostgreSQL parametrizable; SQLite con timeout.
- Middleware de sesión optimizado para decodificar JWT una sola vez por request.
- Eliminada la exposición de credenciales de desarrollo en scripts, README y login.
- CxP deja de ejecutar sincronizaciones masivas durante un GET. La bandeja de tesorería es de solo lectura.
- Consulta CxP optimizada con `JOIN` de proveedores para evitar N+1.
- Dashboard optimizado con agregaciones SQL para ventas y cuentas por cobrar.
- Recepción de OC: CxP, kardex y asiento quedan dentro de la misma transacción; un fallo revierte el evento completo.
- `seed_pcge_basico` y helpers contables admiten ejecución componible sin commits internos, evitando romper transacciones de negocio.
- Gastos ahora persisten `fecha_vencimiento` y `actividad_flujo`.
- Gastos con centro de costo y cuenta de Clase 6 generan destino analítico contra 7911; RxH mantiene 9211/7911.
- Orígenes CxP normalizados a un catálogo cerrado de cuatro valores y con restricciones DB en PostgreSQL/fresh SQLite.

## Base de datos
- Nueva migración `c9d1e2f3a4b5_enterprise_integrity_indexes.py`.
- Índices compuestos para CxP, gastos, asientos, kardex y órdenes de compra.
- Reintegración segura de campos de localización Perú que estaban en una rama Alembic huérfana.
- Restricciones de actividad de flujo y origen CxP en PostgreSQL.

## Frontend / UX
- Nuevo sistema visual global en `app/static/app.css`.
- Nuevas microinteracciones en `app/static/app.js`: estados de procesamiento, Toasts, cierre de modales con Escape y feedback de errores HTMX.
- Login rediseñado y sin credenciales demo visibles.
- Dashboard y CxP refinados; importes numéricos con tipografía tabular; badges de origen diferenciados.
- Formulario de gastos incorpora vencimiento y actividad de flujo.
- Mejoras globales de foco, inputs, botones, tarjetas, tablas, responsive y reduced-motion.

## Testing
Se actualizaron fixtures para no distribuir contraseñas hardcodeadas y se agregaron pruebas enterprise para:
- vencimiento y actividad de flujo en gastos;
- destino Clase 9 / 7911;
- normalización cerrada de orígenes CxP.

Validaciones ejecutadas en el entorno disponible:
- `test_compras_cxp_origen.py`: 12 passed
- `test_compras_kardex_contable.py`: 7 passed
- `test_compras_oc.py` + `test_finanzas_gastos.py`: 10 passed
- `test_finanzas_contabilidad.py` + `test_finanzas_integracion.py`: 3 passed
- `test_contabilidad_transaccional.py`: 23 passed
- `test_tesoreria_pago.py`: 7 passed
- `test_enterprise_hardening.py`: 3 passed
- compilación/sintaxis de Python y Alembic: sin errores

La suite completa existente fue iniciada, pero el conjunto histórico supera la ventana de ejecución disponible en este entorno; los bloques críticos fueron ejecutados individualmente y pasaron.

## Seguridad del paquete entregado
No se incluyen `.env`, base SQLite, `.venv`, `__pycache__`, `.pyc` ni `.git`.
