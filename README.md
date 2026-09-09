# Suit Elans ERP — sastrería, diseño y confección

ERP para sastrería a medida + línea comercial + clientes corporativos: CRM (leads→cotizaciones→pedidos), taller MES con flujo de 12 pasos y SAM, catálogo por tallas/SKU, almacén y compras, caja, facturación interna y API v1 lista para web/Odoo. PWA mobile-first con identidad Borgoña Imperial `#370003` / Marfil `#FAF8F5` / Dorado Latón `#C5A880`.

## Stack
FastAPI + SQLAlchemy 2.0 (SQLite dev / PostgreSQL prod) + Alembic + Jinja2/HTMX/Tailwind/Alpine + JWT (cookie HTTPOnly en web, Bearer en API) + RBAC + ReportLab.

## Arranque rápido (dev)
```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python seed.py   # define ADMIN_EMAIL/ADMIN_PASSWORD antes de ejecutar
.venv/bin/python -m uvicorn app.main:app --port 8000
# → http://127.0.0.1:8000  (redirige a /auth/login)
```
Las rutas de DB/plantillas son absolutas al proyecto: `seed.py` y `uvicorn` funcionan desde cualquier directorio.

## Producción
```bash
docker compose up --build   # web + PostgreSQL 16
# Migraciones: PYTHONPATH=. .venv/bin/alembic upgrade head
```

## Módulos web
## Módulos (6 ámbitos)
| Ámbito / Ruta | Rol | Función |
|---|---|---|
| 📊 `/dashboard` | todos | KPIs + accesos (leads abiertos, por cobrar, stock) |
| 🤝 `/comercial/clientes` | ventas | Personas + B2B unificados, clasificación, ficha 360° |
| 🤝 `/comercial/citas` | ventas, sastre | Calendario mes/semana/día, por prenda, cotiza desde la cita |
| 🤝 `/comercial/crm` | ventas | Kanban PROSPECTO→GANADO/PERDIDO, canales, interacciones |
| ✂️ `/taller/tablero` | taller, sastre | Kanban 7 columnas HTMX + urgencias + miniatura tela |
| ✂️ `/taller/fichas` | sastre, ventas | Clientes, medidas versionadas, pedidos, citas |
| ✂️ `/taller/orden/{id}/ficha` | taller, sastre | Ficha técnica: diseño, medidas, artesano, SAM, pruebas, calidad |
| ✂️ `/taller/pruebas` | taller, sastre | Fit tests 1/2 por zonas (auto→ajustes) + foto opcional |
| ✂️ `/taller/control-calidad` | taller, sastre | Checklist 6 puntos → Listo para Entrega |
| 🛒 `/ventas/pos` | ventas | POS 3 pasos (cliente→prenda/tela→pago), confirma y reserva |
| 🛒 `/ventas/ordenes` | ventas | COTIZACION→VENTA_CONFIRMADA→COMPLETADA, cobro, bloqueo por saldo |
| 🛒 `/ventas/caja` | ventas | Turnos (apertura/arqueo/cierre), cobros exigen turno abierto |
| 🛒 `/ventas/facturacion` | ventas | Comprobantes B001/F001 + PDF (internos) |
| 📦 `/inventario/catalogo` | ventas, almacen | Colecciones, productos, variantes/SKU, stock |
| 📦 `/inventario/almacen` | almacen | Telas, avíos, kardex, alertas |
| 📦 `/inventario/compras` | almacen | Proveedores, OC, recepción a kardex |
| ⚙️ `/admin/usuarios` | admin | Usuarios RBAC + catálogo SAM |
| ⚙️ `/admin/configuracion` | admin | Datos de sede e IGV |
| `/taller/cierre-jornada`, `/taller/reporte-pagos` | taller | Módulo aislado de pagos por jornada |
| Rutas antiguas (`/crm`, `/citas`, `/sastre/...`, etc.) | — | Redirect 302 a su ámbito |

*admin pasa todos los `require_roles`.*

## API v1 (Odoo / web futura)
`POST /api/v1/auth/token` (OAuth2 password → Bearer) · `GET/POST/PATCH /api/v1/leads` · `POST /api/v1/quotations` (+`/lines`, +`/convert`) · `GET/POST /api/v1/companies` (+`/contacts`) · `GET/POST /api/v1/products` (+`/variants`, `/stock/adjust`, `/low-stock`) · `GET /api/v1/orders[/{id}]` · `GET /api/v1/appointments` · `POST/GET /api/v1/invoices` · `GET /api/v1/webhooks` (catálogo de eventos). Docs interactivas en `/docs`.

## Operación y nivelación financiera

- `scripts/fix_cxp_compras.py` corrige espejos OC→CxP, vencimientos, actividad de flujo, orígenes legacy y saldos derivados de forma idempotente.
- `alembic upgrade head` incorpora los campos de vencimiento/flujo, índices de consulta y restricciones de integridad financiera.
- En producción `SECRET_KEY` y `ADMIN_PASSWORD` deben venir del entorno; no se distribuyen secretos ni una BD local.

## Tests
```bash
.venv/bin/python -m pytest tests/ -q   # DB aislada en /tmp, 37 tests
```

## Estructura
```
app/core/ (config, rbac, seguridad)  app/models/  app/schemas/  app/api/v1.py
app/routers/ (dashboard, comercial, taller, taller_cierre, ventas,
              inventario, admin, legacy, auth, reportes)
app/services/  app/main.py
app/templates/ (dashboard/, comercial/, taller/, ventas/, inventario/,
                admin/, components/sidebar.html)
seed.py  tests/  alembic/  Dockerfile  docker-compose.yml
```
