# Prueba integral ERP — Suit Elans (opera desde enero 2021)

Fecha: 2026-09-03. Seed: `seed_demo_integral.py` (todo prefijo `DEMO-`, idempotente).

## Estado: INCOHERENCIAS RESUELTAS (2026-09-03, 2da pasada)
Suite: **79 passed** (`tests/test_erp_coherencia.py` 7 nuevos + 72 previos).

1. **Dualidad compras** → `sincronizar_espejo_compras()` (`services/purchasing.py`) crea el espejo faltante por folio en ambos sentidos; se invoca en `_sync_cxp` y seed. Backfill vivo: 2 OC espejo creadas.
2. **Dualidad ventas** → `sincronizar_espejo_ventas()` (`services/ventas.py`, mapa cotizado→COTIZACION etc.) espeja Order→OrdenVenta, Payment→PagoOrden, Invoice→ComprobanteVenta; invocado en `_sync_cxc` y seed. Backfill vivo: 4 OV + 1 pago + 1 comprobante.
3. **CxC VENCIDO prematuro** → nuevo estado `VENCIDO_EN_TALLER` (fecha pasada + prenda fuera de CALIDAD_OK/entregado); `VENCIDO` solo con taller culminado o sin prendas.
4. **Turno dispar** → el abono CxC ahora registra `CashMovement` con `turno_id` abierto y advierte en log si no hay turno (no bloquea el cobro).
5. **IGV asimétrico** → `purchase_orders` y `ordenes_compra` tienen `subtotal/igv` (migración `6b7c8d9e0f1a`); `recalc_po` los desagrega (÷1.18); CSV compras e IGV usan el valor registrado con fallback presunto. Backfill: 2 OC.
6. **FK MOD engañosa** → nueva `orden_produccion_ref_id` (FK a `orden_produccion.id`); el registro de jornada la rellena; `calcular_mod_devengada` filtra por legado/canónica/`orden_id`. Backfill: 1 detalle.
7. **P&L vs patrimonio** → `cierre_resultados(año, mes)` (asiento CIERRE idempotente contra 5911) + `POST /finanzas/periodos/{pid}/cierre-resultados`.
8. **Activos sin depreciación** → cuentas 68/681/39/391 + `registrar_depreciacion` (Debe 681/Haber 391) + `POST /finanzas/depreciacion`.
9. **Periodos 2021 abiertos** → `cerrar_periodos_antiguos()`; backfill vivo cerró 5, queda abierto solo el mes en curso.
10. **Nota**: `DEMO-F-2021-01` quedó PAGADO por la verificación (correcto: pago simple bloquea el doble).

## Registros generados (suitelans.db)
- Usuarios: `demo.ventas`, `demo.taller`, `demo.contador` / demo123 (3).
- Clientes: Juan Pérez (2021), María López (2023), Carlos Ruiz (2025) + `DEMO- Corp Textil` + contacto.
- CRM: 3 leads (GANADO/PROSPECTO/PERDIDO) + `DEMO-COT-2021-01` convertida (línea traje 2500).
- Pedidos: `DEMO-2021-001` 2500 pagado/entregado (2021-04), `DEMO-2023-001` 3000 anticipo 1500/en_confeccion, `DEMO-2025-001` 4200 cotizado (empresa), `DEMO-2026-001` 1890 entregado; cada uno con prenda + `orden_produccion` (QR-DEMO-…).
- Compras: `DEMO-OC-2023-01` recibida 1200, `DEMO-OC-2025-01` borrador 800 (+líneas tela).
- Facturación/caja: F001-DEMO-1001 emitida 2500, Payment 2500, CashMovement ingreso 2500, CajaTurno ABIERTA 500.
- Rendimiento: `DEMO-OP-01` + jornada 2026-02-12 REGISTRADO (4×25=100) ligada a prenda DEMO-2026-001.
- Gastos (asiento automático): `DEMO-F-2021-01` ALQUILER 2000+360, `DEMO-RH-2023-01` HONORARIOS 1500, `DEMO-PL-2025-12` PLANILLA 3000, `DEMO-F-2024-07` ACTIVO_FIJO 5000+900.
- Periodos: 2021-01, 2023-08, 2025-12, 2026-02 (todos ABIERTO).

## Verificación de funcionamiento (todos GET 200 en :8001)
dashboard, comercial (clientes/crm), kanban, pos/órdenes/caja, catálogo/almacén/compras,
CxC/CxP/gastos/flujo/rentabilidad/reportes/diario/mayor/balance/EEFF/periodos/plan, rendimiento registro, admin usuarios.
RBAC: `demo.contador` 200 en gastos/CxC; `demo.ventas` 403 (correcto).
Flujos: gasto→asiento 6311/40111/4212 OK; pago doble bloqueado OK; P&L refleja operativos.

## Incoherencias anotadas (para solucionar luego, NO tocadas)
1. **Dualidad compras**: UI usa `purchase_orders` (2 filas DEMO) mientras `ordenes_compra` está vacía (0). `_sync_cxp` solo mira la primera. Riesgo de partir datos en dos tablas.
2. **Dualidad ventas**: `orders` usa estados minúscula (`cotizado/entregado/en_confeccion`); `ordenes_venta` spec (`COTIZACION/COMPLETADA`) vacía; `comprobantes_venta` vs `invoices` paralelo. Sincronía solo parcial vía `_sync_pago_orden`.
3. **CxC VENCIDO prematuro**: `DEMO-2023-001` figura VENCIDO solo por `fecha_entrega` 2023 pasada, aunque sigue `en_confeccion`. La mora debería considerar estado taller, no solo fecha.
4. **Turno de caja dispar**: `/ventas` exige turno abierto (`?error=turno`) pero `/finanzas` abona CxC sin turno. Mismo ingreso con distinto control.
5. **IGV asimétrico**: `invoices` desagrega IGV real; `purchase_orders.total` no, e IGV compras se presume (`total*18/118`). Fiscalmente débil.
6. **FK engañosa MOD**: `rendimiento_detalles.orden_produccion_id` apunta a `garments.id` (no a `orden_produccion.id`). Prorrateo MOD por orden propenso a error.
7. **P&L vs patrimonio**: en vivo P&L da operativos 6500 / resultado −6500 con balance `valida=True` porque el resultado no cerrado a `59` se mezcla en `patrimonio_total`. Aritmética OK, lectura confusa sin cierre.
8. **Activos sin depreciación**: `ACTIVO_FIJO 3341` se activa bien pero no hay cuentas 68/39 ni proceso de depreciación; el activo queda eterno.
9. **Periodos 2021 abiertos**: 2021-01 y demás históricos quedan ABIERTO, permiten asientos retroactivos sin bloqueo.
10. **Nota de prueba**: `DEMO-F-2021-01` quedó PAGADO (asiento pago id 5) por la verificación; el resto PENDIENTE. Si se requiere estado original, revertir ese pago.

## Limpieza (cuando se quiera)
Borrar filas `DEMO-%` en users/clients/companies/leads/quotations/orders/garments/orden_produccion/purchase_orders/invoices/payments/gastos/asientos + `DEMO-OP-01`/jornada. Script pendiente.
