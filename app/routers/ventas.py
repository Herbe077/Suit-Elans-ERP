"""Ámbito Ventas & Atención (Front-Office): POS 3 pasos, órdenes, cobros
con automatizaciones (confirmación + reserva + turno), caja por turnos y
facturación. Mapeo: OrdenVenta=Order, PagoOrden=Payment+CashMovement,
ComprobanteVenta=Invoice, OrdenTrabajo=Garment."""
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.constants import GARMENT_LABELS, GARMENT_SETS, GARMENT_TYPES
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.billing import CajaTurno, CashMovement, Invoice
from app.models.catalog import Product, ProductVariant
from app.models.client import Client
from app.models.company import Company
from app.models.inventory import Fabric
from app.models.order import Garment, Order, Payment
from app.models.purchasing import PurchaseOrder
from app.services import billing as billing_svc
from app.services import cash as cash_svc
from app.services import config as config_svc
from app.services import ventas as ventas_svc
from app.services.reports import comprobante_pdf

router = APIRouter(prefix="/ventas", tags=["ventas"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Auth = Depends(require_roles("VENTA", "SASTRE-MAESTRO"))  # ADMIN pasa siempre

ESTADO_VENTA = {"cotizado": "COTIZACION", "confirmado": "VENTA_CONFIRMADA",
                 "en_produccion": "EN_PRODUCCION",
                 "entregado": "COMPLETADA", "cancelado": "CANCELADA"}
INV_ESTADO_MAP = {"COTIZACION": "cotizado", "VENTA_CONFIRMADA": "confirmado", "EN_PRODUCCION": "en_produccion", "COMPLETADA": "entregado", "CANCELADA": "cancelado"}


def _sync_orden_venta(db: Session, order: Order):
    """Sincroniza Order legacy -> OrdenVenta spec."""
    try:
        from app.models.ventas import OrdenVenta
        ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == order.id).first()
        if not ov:
            # busca por folio
            ov = db.query(OrdenVenta).filter(OrdenVenta.folio == order.folio).first()
        estado_spec = ESTADO_VENTA.get(order.estado, "COTIZACION")
        if not ov:
            ov = OrdenVenta(folio=order.folio, cliente_id=order.client_id, company_id=order.company_id,
                            total=order.total, monto_adelantado=order.anticipo,
                            saldo_pendiente=order.saldo, estado=estado_spec,
                            legacy_order_id=order.id, sastre_id=order.sastre_id)
            db.add(ov)
        else:
            ov.total = order.total
            ov.monto_adelantado = order.anticipo
            ov.saldo_pendiente = order.saldo
            ov.estado = estado_spec
            ov.cliente_id = order.client_id
        db.commit()
        return ov
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None

def _sync_pago_orden(db: Session, payment: Payment, order: Order, turno_id: int | None):
    try:
        from app.models.ventas import PagoOrden, OrdenVenta
        ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == order.id).first()
        if not ov:
            ov = _sync_orden_venta(db, order)
        # evita duplicado
        if db.query(PagoOrden).filter(PagoOrden.legacy_payment_id == payment.id).first():
            return
        ov_id = ov.id if ov else None
        tipo = "ADELANTO" if (order.anticipo - payment.monto) <= 0.01 else "SALDO_FINAL"
        # determina si es primer pago vs saldo
        if ov and ov.monto_adelantado and ov.monto_adelantado > payment.monto:
            tipo = "SALDO_FINAL"
        else:
            # si total - anticipo previo == pago, es saldo
            pass
        db.add(PagoOrden(orden_venta_id=ov_id or order.id, legacy_payment_id=payment.id,
                         caja_turno_id=turno_id, legacy_order_id=order.id,
                         monto=payment.monto, metodo_pago=payment.metodo.upper(), tipo_pago=tipo))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

def _sync_comprobante(db: Session, inv: Invoice):
    try:
        from app.models.ventas import ComprobanteVenta, OrdenVenta
        if db.query(ComprobanteVenta).filter(ComprobanteVenta.legacy_invoice_id == inv.id).first():
            return
        ov = None
        if inv.order_id:
            ov = db.query(OrdenVenta).filter(OrdenVenta.legacy_order_id == inv.order_id).first()
        tipo = "FACTURA" if inv.serie == "F001" else "BOLETA"
        db.add(ComprobanteVenta(orden_venta_id=ov.id if ov else None,
                                legacy_invoice_id=inv.id, legacy_order_id=inv.order_id,
                                tipo=tipo, serie=inv.serie, numero=inv.numero, estado=inv.estado))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _igv(db: Session) -> float:
    try:
        return float(config_svc.get(db, "igv_default", "18"))
    except ValueError:
        return 18.0


# ---------- Punto de venta (3 pasos: cliente -> prenda/tela -> pago) ----------
@router.get("/pos", response_class=HTMLResponse)
def pos(request: Request, ok: str = "", error: str = "",
        db: Session = Depends(get_db), user=Auth):
    import logging as _logging
    try:
        empresas = db.query(Company).order_by(Company.nombre_comercial).all()
        colabs: dict[int, list] = {}
        for c in db.query(Client).filter(Client.company_id.is_not(None)).all():
            colabs.setdefault(c.company_id, []).append(c)
        variantes = db.query(ProductVariant).filter(ProductVariant.stock > 0).order_by(
            ProductVariant.sku).limit(200).all()
        prods = {p.id: p.nombre for p in db.query(Product).all()}
        return templates.TemplateResponse(request, "ventas/pos.html", {
            "user": user, "ok": ok, "error": error,
            "turno": ventas_svc.turno_abierto(db, user.id),
            "min_anticipo": ventas_svc.anticipo_min_pct(db),
            "clientes": db.query(Client).order_by(Client.apellidos).limit(200).all(),
            "empresas": empresas, "colabs": colabs,
            "variantes": variantes, "nombres_prod": prods,
            "telas": db.query(Fabric).filter(Fabric.stock_metros > 0).order_by(
                Fabric.codigo).limit(200).all(),
            "tipos": GARMENT_TYPES, "tipos_labels": GARMENT_LABELS,
            "conjuntos": GARMENT_SETS,
            "recientes": db.query(Order).order_by(Order.id.desc()).limit(10).all()})
    except Exception as e:
        # Vista HTML: jamás JSON crudo; banner amigable + log completo.
        _logging.getLogger(__name__).exception("500 GET /ventas/pos: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return templates.TemplateResponse(request, "ventas/pos.html", {
            "user": user, "ok": "", "error": "interno",
            "turno": None, "min_anticipo": 50,
            "clientes": [], "empresas": [], "colabs": {},
            "variantes": [], "nombres_prod": {},
            "telas": [], "tipos": GARMENT_TYPES,
            "tipos_labels": GARMENT_LABELS, "conjuntos": GARMENT_SETS,
            "recientes": []}, status_code=500)


@router.post("/pos/cliente-rapido")
async def cliente_rapido(request: Request, db: Session = Depends(get_db),
                         user=Auth):
    """Alta exprés desde el POS (JSON, sin salir de la venta).

    Solo datos mínimos de comprobante/contacto; SIN medidas antropométricas
    (esas viven en Comercial/CRM y el modo Servicio/Bespoke).
    RUC → empresa; otro doc → persona. Nunca bloquea por formato.
    """
    from app.services import peru as peru_svc
    data = await request.json()
    tipo_doc = ((data.get("tipo_doc") or "DNI").strip() or "DNI").upper()
    nro = (data.get("nro_doc") or "").strip() or None
    nombre = (data.get("nombre") or "").strip()
    apellidos = (data.get("apellidos") or "").strip()
    telefono = (data.get("telefono") or "").strip() or None
    email = (data.get("email") or "").strip() or None
    if not nombre:
        return JSONResponse({"error": "Nombre requerido"}, status_code=400)
    if tipo_doc == "RUC":
        razon = f"{nombre} {apellidos}".strip()
        e = Company(nombre_comercial=razon, ruc=peru_svc.normalizar_ruc(nro),
                    telefono=telefono, email=email, clasificacion="Nuevo")
        db.add(e)
        db.commit()
        db.refresh(e)
        return {"tipo": "empresa", "id": e.id,
                "display": f"{e.nombre_comercial} · {e.ruc or ''}".strip(" ·")}
    c = Client(nombre=nombre, apellidos=apellidos, telefono=telefono,
               email=email, tipo_doc=tipo_doc,
               nro_doc=(peru_svc.normalizar_ruc(nro) if tipo_doc == "RUC"
                        else nro),
               clasificacion="Nuevo")
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"tipo": "persona", "id": c.id,
            "display": f"{c.nombre} {c.apellidos} · {c.doc_label}".strip(" ·")}


@router.post("/pos/vender")
def vender(client_id: str = Form(""), company_id: str = Form(""),
           colaborador_id: str = Form(""), colab_nombre: str = Form(""),
           colab_apellidos: str = Form(""), colab_telefono: str = Form(""),
           publico_general: str = Form(""),
           variant_id: str = Form(""), cantidad: float = Form(1),
           concepto: str = Form(""), precio: float = Form(0),
           garment_tipo: str = Form(""), tela_id: str = Form(""),
           monto_cobro: float = Form(0), metodo: str = Form("efectivo"),
           db: Session = Depends(get_db), user=Auth):
    from app.models.inventory import StockMovement
    from app.services.orders import next_folio
    es_pg = (publico_general or "").strip().lower() in ("1", "true", "si", "on")
    emp_id = int(company_id) if company_id and company_id.isdigit() else None
    # Colaborador / beneficiario: la prenda y ficha van a su nombre,
    # la facturación queda a nombre de la empresa (company_id).
    beneficiario_id: int | None = None
    if emp_id:
        if colaborador_id and colaborador_id.isdigit():
            col = db.get(Client, int(colaborador_id))
            if col:
                if col.company_id != emp_id:
                    col.company_id = emp_id
                beneficiario_id = col.id
        elif (colab_nombre or "").strip():
            col = Client(nombre=colab_nombre.strip(),
                         apellidos=(colab_apellidos or "").strip(),
                         telefono=(colab_telefono or "").strip() or None,
                         tipo_doc="DNI", nro_doc=None, clasificacion="Nuevo",
                         company_id=emp_id)
            db.add(col)
            db.flush()
            beneficiario_id = col.id
    cli_id = int(client_id) if client_id and client_id.isdigit() else beneficiario_id
    if not cli_id and not emp_id and not es_pg:
        return RedirectResponse("/ventas/pos", status_code=303)
    if not variant_id and not (concepto and precio > 0):
        return RedirectResponse("/ventas/pos", status_code=303)
    if monto_cobro > 0 and not ventas_svc.turno_abierto(db, user.id):
        return RedirectResponse("/ventas/pos?error=turno", status_code=303)
    total, variante = 0.0, None
    if variant_id:
        variante = db.get(ProductVariant, int(variant_id))
        if not variante or variante.stock < cantidad:
            return RedirectResponse("/ventas/pos", status_code=303)
        total = round(variante.precio * cantidad, 2)
    else:
        total = round(precio, 2)
    if not cli_id and not emp_id:
        # Boleta a Público General: tope SUNAT S/ 700.00.
        from app.services import peru as peru_svc
        try:
            peru_svc.exigir_cliente_sunat(total, False)
        except ValueError:
            return RedirectResponse("/ventas/pos?error=sunat", status_code=303)
    order = Order(folio=next_folio(db), client_id=cli_id, company_id=emp_id,
                  sastre_id=user.id, estado="cotizado", canal="comercial",
                  concepto=(concepto or "").strip() or None, total=total)
    db.add(order)
    db.flush()
    if variante:
        variante.stock = round(variante.stock - cantidad, 2)
        db.add(StockMovement(item_tipo="variant", item_id=variante.id, cantidad=-cantidad,
                             tipo="salida", motivo=f"POS {order.folio}", usuario_id=user.id))
        # Costo de ventas RTW automático (6911/2111 a CPP; no bloquea la venta)
        try:
            from app.services import contabilidad as contab
            cogs = round(float(variante.costo_unitario or 0) * cantidad, 2)
            if cogs > 0:
                from app.services.motor_contable import dim_almacen
                r_costo = contab.registrar_costo_ventas(
                    db, order.id, cogs, user.id, producto_id=variante.id,
                    almacen_id=dim_almacen(db))
                # Salida física Cta 23 vinculada a la venta POS.
                from app.models.inventario import MovimientoKardex
                db.refresh(variante)
                db.add(MovimientoKardex(
                    producto_id=None, tipo_movimiento="SALIDA_VENTA",
                    cantidad=float(cantidad),
                    costo_unitario=round(float(variante.costo_unitario or 0), 2),
                    costo_total=cogs,
                    saldo_fisico=float(variante.stock or 0),
                    saldo_valorizado=round(float(variante.stock or 0) * float(variante.costo_unitario or 0), 2),
                    orden_venta_id=order.id,
                    asiento_id=r_costo.get("asiento_id"),
                    doc_ref=variante.sku, usuario_id=user.id,
                    observacion=f"Salida venta {order.folio} {variante.sku} x{cantidad:g}"))
                db.commit()
        except Exception:
            pass
    else:
        # Orden de trabajo para taller + reserva inmediata de tela si se eligió.
        # Conjuntos multipieza generan una ficha técnica por pieza.
        piezas = list(GARMENT_SETS.get(garment_tipo, (None, [garment_tipo or "prenda"]))[1])
        n = len(piezas)
        parte = [round(total / n, 2)] * n
        parte[-1] = round(total - sum(parte[:-1]), 2)
        prendas = []
        for tp, pp in zip(piezas, parte):
            g = Garment(order_id=order.id, tipo=tp,
                        tela_id=int(tela_id) if tela_id else None, precio=pp)
            db.add(g)
            db.flush()
            prendas.append(g)
        from app.services.taller import codigo_qr as _qr
        for g in prendas:
            g.codigo_qr = _qr(order.folio, g.id)
        g = prendas[0]
        if g.tela_id:
            from app.core.constants import CONSUMO_TELA_M
            tela = db.get(Fabric, g.tela_id)
            consumo = round(sum(CONSUMO_TELA_M.get(tp, 1.5) for tp in piezas), 2)
            if tela and tela.stock_metros >= consumo:
                tela.stock_metros = round(tela.stock_metros - consumo, 2)
                for gg in prendas:
                    gg.tela_reservada = True
                # Spec inventario: reserva ProductoInsumo
                try:
                    from app.services.inventory import ensure_producto_for_fabric, reservar_insumo
                    prod = ensure_producto_for_fabric(db, tela)
                    # reservar sin descontar físico ya descontado legacy, solo reservado + disponible
                    if prod.stock_disponible >= consumo - 1e-9:
                        prod.stock_reservado = round((prod.stock_reservado or 0) + consumo, 2)
                        # compensa físico porque legacy ya descontó: restaura para que disponible = fisico_legacymirrored?
                        # Mantén fisico ProductoInsumo sin descontar (reserva no toca fisico)
                        # Para que físico spec quede 10, reservado 2, disponible 8 (test spec)
                        # Pero legacy fisico ya es 8, spec fisico 10 -> disponible 8 ok
                        # No sincronices fisico con legacy descontado
                        from app.models.inventario import MovimientoKardex
                        db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_CONSUMO_TALLER",
                                                cantidad=consumo, orden_venta_id=order.id, usuario_id=user.id, observacion="Reserva POS"))
                except Exception:
                    pass
    db.commit()
    # B2B: aprobación automática a producción (sin esperar adelanto).
    try:
        order = db.get(Order, order.id)
        if order is not None and ventas_svc.es_pedido_corporativo(db, order):
            ventas_svc.aprobar_produccion(db, order)
    except Exception:
        pass
    _sync_orden_venta(db, order)
    if monto_cobro > 0:
        try:
            o, pay = ventas_svc.registrar_cobro(db, order.id, monto_cobro, metodo, "ADELANTO",
                                       user.id)
            # sync pago spec
            turno = ventas_svc.turno_abierto(db, user.id)
            _sync_orden_venta(db, o)
            _sync_pago_orden(db, pay, o, turno.id if turno else None)
        except ValueError:
            pass
    return RedirectResponse("/ventas/pos?ok=1", status_code=303)


# ---------- Órdenes de venta ----------
@router.get("/ordenes", response_class=HTMLResponse)
def ordenes(request: Request, estado: str = "", error: str = "", msg: str = "",
            cliente_id: str = "", concepto: str = "",
            db: Session = Depends(get_db), user=Auth):
    import logging as _logging
    try:
        return _ordenes_ok(request, estado, error, msg, cliente_id, concepto,
                           db, user)
    except Exception as e:
        _logging.getLogger(__name__).exception("500 GET /ventas/ordenes: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        tpl = "ventas/ordenes_listado.html" if (BASE_DIR / "app" / "templates" / "ventas" / "ordenes_listado.html").exists() else "ventas/ordenes.html"
        return templates.TemplateResponse(request, tpl, {
            "user": user, "ordenes": [], "nombres": {}, "taller_ok": {},
            "estado": estado, "error": "interno", "msg": "Error interno al cargar las órdenes.",
            "mapa": ESTADO_VENTA, "min_anticipo": 50, "facturas_activas": {},
            "cliente_id": cliente_id, "concepto_sugerido": concepto,
            "clientes": []}, status_code=500)


def _ordenes_ok(request: Request, estado: str, error: str, msg: str,
                cliente_id: str, concepto: str,
                db: Session, user):
    q = db.query(Order)
    if estado:
        q = q.filter(Order.estado == estado)
    ordenes = q.order_by(Order.id.desc()).limit(120).all()
    nombres = {}
    taller_ok = {}
    for o in ordenes:
        if o.client_id and db.get(Client, o.client_id):
            c = db.get(Client, o.client_id)
            nombres[o.id] = f"{c.nombre} {c.apellidos}"
        elif o.company_id and db.get(Company, o.company_id):
            nombres[o.id] = db.get(Company, o.company_id).nombre_comercial
        else:
            nombres[o.id] = "—"
        # Taller listo: todas las prendas en CALIDAD_OK
        gs = db.query(Garment).filter(Garment.order_id == o.id).all()
        if not gs:
            taller_ok[o.id] = True
        else:
            taller_ok[o.id] = all(g.estado_taller in ("CALIDAD_OK", "entregado") for g in gs)
    tpl = "ventas/ordenes_listado.html" if (BASE_DIR / "app" / "templates" / "ventas" / "ordenes_listado.html").exists() else "ventas/ordenes.html"
    # Comprobante activo por pedido (para badge PDF u ocultar "Facturar →").
    facturas_activas = {}
    for inv in db.query(Invoice).filter(
            Invoice.order_id.in_([o.id for o in ordenes]),
            Invoice.estado != "anulada",
            Invoice.tipo.in_(["TOTAL", "FINAL"])).order_by(Invoice.id).all():
        facturas_activas[inv.order_id] = inv
    return templates.TemplateResponse(request, tpl, {
        "user": user, "ordenes": ordenes, "nombres": nombres, "taller_ok": taller_ok, "estado": estado,
        "error": error, "msg": msg, "mapa": ESTADO_VENTA,
        "min_anticipo": ventas_svc.anticipo_min_pct(db),
        "facturas_activas": facturas_activas,
        "cliente_id": cliente_id,
        "concepto_sugerido": concepto,
        "clientes": db.query(Client).order_by(Client.apellidos).limit(200).all()})


@router.post("/ordenes")
def crear_orden(client_id: str = Form(""), concepto: str = Form(...),
                total: float = Form(...), garment_tipo: str = Form(""),
                db: Session = Depends(get_db), user=Auth):
    from app.services.orders import next_folio
    if not client_id or total <= 0 or not (garment_tipo or "").strip():
        return RedirectResponse("/ventas/ordenes", status_code=303)
    o = Order(folio=next_folio(db), client_id=int(client_id), sastre_id=user.id,
              estado="cotizado", canal="sastreria", total=round(total, 2),
              concepto=(concepto or "").strip() or None)
    db.add(o)
    db.flush()
    # Prenda base: la venta confirmada deriva al taller con su ficha.
    g = Garment(order_id=o.id, tipo=garment_tipo.strip(), precio=round(total, 2))
    db.add(g)
    db.flush()
    from app.services.taller import codigo_qr as _qr
    g.codigo_qr = _qr(o.folio, g.id)
    db.commit()
    # B2B: aprobación automática a producción (sin esperar adelanto).
    try:
        if ventas_svc.es_pedido_corporativo(db, o):
            ventas_svc.aprobar_produccion(db, o)
    except Exception:
        pass
    _sync_orden_venta(db, o)
    return RedirectResponse("/ventas/ordenes", status_code=303)


@router.post("/ordenes/{oid}/entregar")
def entregar(oid: int, db: Session = Depends(get_db), user=Auth):
    try:
        order = ventas_svc.entregar(db, oid)
        _sync_orden_venta(db, order)
    except ValueError as e:
        msg = str(e)
        if "saldo pendiente" in msg:
            return RedirectResponse("/ventas/ordenes?error=saldo", status_code=303)
        if "no está lista" in msg or "CALIDAD_OK" in msg:
            return RedirectResponse(f"/ventas/ordenes?error=taller&msg={msg}", status_code=303)
        return RedirectResponse("/ventas/ordenes?error=saldo", status_code=303)
    return RedirectResponse("/ventas/ordenes", status_code=303)


# ---------- Cobro unificado ----------
@router.post("/cobro")
def cobro(order_id: int = Form(...), monto: float = Form(...),
          metodo: str = Form("efectivo"), tipo: str = Form(""),
          back: str = Form("/ventas/ordenes"),
          db: Session = Depends(get_db), user=Auth):
    if not back.startswith("/"):
        back = "/ventas/ordenes"
    try:
        order, pay = ventas_svc.registrar_cobro(db, order_id, monto, metodo, tipo or "", user.id)
        turno = ventas_svc.turno_abierto(db, user.id)
        _sync_orden_venta(db, order)
        _sync_pago_orden(db, pay, order, turno.id if turno else None)
    except ValueError as e:
        msg = "turno" if str(e) == "TURNO_CERRADO" else "sunat" if "SUNAT" in str(e) else "monto"
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}error={msg}", status_code=303)
    return RedirectResponse(back, status_code=303)


# ---------- Caja por turnos ----------
@router.get("/caja", response_class=HTMLResponse)
def caja(request: Request, error: str = "", db: Session = Depends(get_db), user=Auth):
    turno = ventas_svc.turno_abierto(db, user.id)
    con_saldo = [o for o in db.query(Order).filter(
        Order.estado.notin_(["cancelado"])).order_by(Order.id.desc()).limit(100).all()
        if o.saldo > 0]
    movs = db.query(CashMovement).order_by(CashMovement.id.desc()).limit(60).all()
    if turno:
        movs = [m for m in movs if m.turno_id == turno.id or m.turno_id is None][:60]
    turnos = db.query(CajaTurno).order_by(CajaTurno.id.desc()).limit(20).all()
    # Spec pide caja_arqueo.html, legacy es caja.html — ambos existen con mismo contenido
    tpl = "ventas/caja_arqueo.html" if (BASE_DIR / "app" / "templates" / "ventas" / "caja_arqueo.html").exists() else "ventas/caja.html"
    return templates.TemplateResponse(request, tpl, {
        "user": user, "bal": cash_svc.balance(db), "movs": movs,
        "con_saldo": con_saldo, "error": error, "turno": turno,
        "teorico": ventas_svc.teorico_turno(db, turno.id) if turno else 0,
        "turnos": turnos,
        "por_pagar": db.query(PurchaseOrder).filter(
            PurchaseOrder.estado.notin_(["recibida", "cancelada"])).all()})


@router.post("/caja/abrir")
def abrir(saldo_apertura: float = Form(0), db: Session = Depends(get_db), user=Auth):
    try:
        ventas_svc.abrir_turno(db, user.id, saldo_apertura)
    except ValueError:
        return RedirectResponse("/ventas/caja?error=turno", status_code=303)
    except Exception:
        # Fallo de BD (p.ej. esquema degradado): rollback + aviso, jamás 500.
        try:
            db.rollback()
        except Exception:
            pass
        return RedirectResponse("/ventas/caja?error=turno", status_code=303)
    return RedirectResponse("/ventas/caja", status_code=303)


@router.post("/caja/cerrar")
def cerrar(saldo_real: float = Form(...), db: Session = Depends(get_db), user=Auth):
    try:
        turno = ventas_svc.turno_abierto(db, user.id)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return RedirectResponse("/ventas/caja?error=turno", status_code=303)
    if not turno:
        return RedirectResponse("/ventas/caja?error=turno", status_code=303)
    try:
        t = ventas_svc.cerrar_turno(db, turno.id, saldo_real, user.id)
    except ValueError:
        return RedirectResponse("/ventas/caja?error=monto", status_code=303)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return RedirectResponse("/ventas/caja?error=monto", status_code=303)
    return RedirectResponse(f"/ventas/caja?error=cierre_{t.diferencia}", status_code=303)


@router.post("/caja/movimiento")
def movimiento(tipo: str = Form(...), concepto: str = Form(...), monto: float = Form(...),
               metodo: str = Form("efectivo"), db: Session = Depends(get_db), user=Auth):
    turno = ventas_svc.turno_abierto(db, user.id)
    try:
        cash_svc.add_movement(db, tipo, concepto.strip(), monto, metodo, user.id,
                              turno_id=turno.id if turno else None)
    except ValueError:
        pass
    return RedirectResponse("/ventas/caja", status_code=303)


@router.post("/caja/cobrar")
def cobrar(order_id: int = Form(...), monto: float = Form(...),
           metodo: str = Form("efectivo"), db: Session = Depends(get_db), user=Auth):
    try:
        order, pay = ventas_svc.registrar_cobro(db, order_id, monto, metodo, "", user.id)
        turno = ventas_svc.turno_abierto(db, user.id)
        _sync_orden_venta(db, order)
        _sync_pago_orden(db, pay, order, turno.id if turno else None)
    except ValueError as e:
        msg = "turno" if str(e) == "TURNO_CERRADO" else "monto"
        return RedirectResponse(f"/ventas/caja?error={msg}", status_code=303)
    return RedirectResponse("/ventas/caja", status_code=303)


# ---------- Facturación / comprobantes ----------
def _nombre_orden(db: Session, o: Order) -> str:
    if o.company_id and db.get(Company, o.company_id):
        return db.get(Company, o.company_id).nombre_comercial
    if o.client_id and db.get(Client, o.client_id):
        c = db.get(Client, o.client_id)
        return f"{c.nombre} {c.apellidos}"
    return "—"


def _info_facturar(db: Session, pendientes: list) -> dict:
    """Desglose por pedido para el formulario: anticipo pendiente, saldo y modo."""
    from app.services import contabilidad as contab
    info = {}
    for o in pendientes:
        pend = contab.anticipo_pendiente_facturar(db, o)
        saldo = round(max((o.total or 0) - (o.anticipo or 0), 0.0), 2)
        info[o.id] = {"nombre": _nombre_orden(db, o),
                      "anticipo": pend, "saldo": saldo, "total": o.total or 0,
                      "saldo_fact": contab.saldo_pendiente_facturar(db, o),
                      "modo": "ANTICIPO" if pend > 0 else "TOTAL"}
    return info


def _nombre(db: Session, inv: Invoice) -> str:
    if inv.company_id and db.get(Company, inv.company_id):
        e = db.get(Company, inv.company_id)
        return f"{e.nombre_comercial} ({e.doc_label})" if e.ruc else e.nombre_comercial
    if inv.client_id and db.get(Client, inv.client_id):
        c = db.get(Client, inv.client_id)
        base = f"{c.nombre} {c.apellidos}"
        return f"{base} ({c.doc_label})" if c.nro_doc else base
    if inv.order_id and db.get(Order, inv.order_id):
        o = db.get(Order, inv.order_id)
        if o.client_id and db.get(Client, o.client_id):
            c = db.get(Client, o.client_id)
            return f"{c.nombre} {c.apellidos}"
    return "—"


@router.get("/facturacion", response_class=HTMLResponse)
def facturacion(request: Request, order_id: str = "",
                db: Session = Depends(get_db), user=Auth):
    from app.services import contabilidad as contab
    facturas = db.query(Invoice).order_by(Invoice.id.desc()).limit(80).all()
    pendientes = [o for o in db.query(Order).filter(
        Order.estado.notin_(["cancelado"])).order_by(
        Order.id.desc()).limit(120).all()
        if not contab.comprobante_activo_cubriente(db, o.id)]
    # Pre-selección desde Órdenes de Venta (?order_id=): solo si el pedido
    # sigue pendiente de facturación; si no, se ignora y usa el primero.
    order_sel = _order_id_seleccionado(order_id, pendientes)
    return templates.TemplateResponse(request, "ventas/facturacion.html", {
        "user": user, "facturas": facturas,
        "nombres": {f.id: _nombre(db, f) for f in facturas},
        "pendientes": pendientes,
        "order_id_sel": order_sel,
        # Pedidos de empresa (RUC) fuerzan Factura F001.
        "serie_forzada": {o.id: ("F001" if o.company_id else "B001")
                          for o in pendientes},
        "info_fact": _info_facturar(db, pendientes),
        "igv": _igv(db),
        "sede": config_svc.get(db, "sede_nombre", "Suit Elans")})


def _order_id_seleccionado(order_id: str, pendientes: list) -> int | None:
    """Valida ?order_id= contra los pedidos pendientes visibles.

    Retorna el id (int) si existe en `pendientes`, None en otro caso."""
    try:
        oid = int((order_id or "").strip())
    except (TypeError, ValueError):
        return None
    return oid if any(o.id == oid for o in pendientes) else None


@router.get("/comprobantes", response_class=HTMLResponse)
def comprobantes(request: Request, order_id: str = "",
                 db: Session = Depends(get_db), user=Auth):
    # Spec pide comprobantes.html; facturacion.html es legacy
    if (BASE_DIR / "app" / "templates" / "ventas" / "comprobantes.html").exists():
        from app.services import contabilidad as contab
        facturas = db.query(Invoice).order_by(Invoice.id.desc()).limit(80).all()
        pendientes = [o for o in db.query(Order).filter(
            Order.estado.notin_(["cancelado"])).order_by(
            Order.id.desc()).limit(120).all()
            if not contab.comprobante_activo_cubriente(db, o.id)]
        return templates.TemplateResponse(request, "ventas/comprobantes.html", {
            "user": user, "facturas": facturas,
            "nombres": {f.id: _nombre(db, f) for f in facturas},
            "pendientes": pendientes,
            "order_id_sel": _order_id_seleccionado(order_id, pendientes),
            "serie_forzada": {o.id: ("F001" if o.company_id else "B001")
                              for o in pendientes},
            "info_fact": _info_facturar(db, pendientes),
            "igv": _igv(db),
            "sede": config_svc.get(db, "sede_nombre", "Suit Elans")})
    return facturacion(request, order_id, db, user)


@router.post("/facturacion/emitir")
def emitir(serie: str = Form("B001"), order_id: str = Form(""),
           modo: str = Form("TOTAL"), monto: str = Form(""),
           db: Session = Depends(get_db), user=Auth):
    # Emisión atómica: comprobante + asiento (TOTAL/ANTICIPO/SALDO) + CxC.
    from app.services import contabilidad as contab
    order = None
    if (order_id or "").strip():
        try:
            order = db.get(Order, int(str(order_id).strip()))
        except (TypeError, ValueError):
            return HTMLResponse("Pedido inválido", status_code=400)
        if order is None:
            return HTMLResponse("Pedido no encontrado", status_code=400)
    if order and order.company_id:
        serie = "F001"  # Empresa con RUC: comprobante forzado a Factura
    try:
        if order:
            res = contab.emitir_factura_venta(
                db, order.id, serie, _igv(db), user.id, modo=modo,
                monto=float(monto) if monto.strip() else None)
            inv = db.get(Invoice, res["invoice_id"])
        else:
            inv = billing_svc.emit_invoice(db, serie, None, None, None, _igv(db), user.id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    _sync_comprobante(db, inv)
    return RedirectResponse("/ventas/facturacion", status_code=303)


@router.post("/comprobantes", response_class=HTMLResponse)
def crear_comprobante(serie: str = Form("B001"), order_id: str = Form(""), tipo: str = Form(""),
                      modo: str = Form("TOTAL"), monto: str = Form(""),
                      db: Session = Depends(get_db), user=Auth):
    # Spec: POST /ventas/comprobantes crea BOLETA/FACTURA (emisión atómica si hay pedido)
    from app.services import contabilidad as contab
    order = db.get(Order, int(order_id)) if order_id and order_id.isdigit() else None
    if order and order.company_id:
        tipo, serie = "FACTURA", "F001"  # Empresa con RUC: Factura forzada
    elif tipo and tipo.upper() in ("BOLETA", "FACTURA"):
        serie = "B001" if tipo.upper() == "BOLETA" else "F001"
    try:
        if order:
            res = contab.emitir_factura_venta(
                db, order.id, serie, _igv(db), user.id, modo=modo,
                monto=float(monto) if monto.strip() else None)
            inv = db.get(Invoice, res["invoice_id"])
        else:
            inv = billing_svc.emit_invoice(db, serie, None, None, None, _igv(db), user.id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    _sync_comprobante(db, inv)
    return RedirectResponse("/ventas/comprobantes", status_code=303)


@router.post("/comprobantes/emitir")
def emitir_alias(serie: str = Form("B001"), order_id: str = Form(""),
                 modo: str = Form("TOTAL"), monto: str = Form(""),
                 db: Session = Depends(get_db), user=Auth):
    return emitir(serie=serie, order_id=order_id, modo=modo, monto=monto,
                  db=db, user=user)


@router.post("/facturacion/{iid}/anular")
def anular(iid: int, db: Session = Depends(get_db), user=Auth):
    # Anulación atómica: comprobante → 'anulada' + asiento de extorno en el
    # Libro Diario (reversión exacta) + espejo ComprobanteVenta. Un solo commit.
    from app.services import contabilidad as contab
    try:
        contab.anular_factura_venta(db, iid)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/ventas/facturacion", status_code=303)


@router.get("/facturacion/{iid}.pdf")
def pdf(iid: int, db: Session = Depends(get_db), user=Auth):
    inv = db.get(Invoice, iid)
    lineas = ([{"concepto": f"Pedido {db.get(Order, inv.order_id).folio}",
                "importe": inv.subtotal}] if inv.order_id
              else [{"concepto": "Servicios de sastrería", "importe": inv.subtotal}])
    doc = comprobante_pdf(inv.folio, _nombre(db, inv), lineas,
                          inv.subtotal, inv.igv_pct, inv.igv, inv.total)
    return Response(content=doc, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename={inv.folio}.pdf"})
