"""Finanzas & Contabilidad — subdominio central, partida doble, PCGE, devengado.
Mantiene compatibilidad con 5 páginas legacy + expone nuevo plan contable / asientos / mayor / balances.
No importa rendimiento directamente, solo lectura SQL.
"""
from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, Form, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
import csv, io
from urllib.parse import quote as _quote

from app.core.config import BASE_DIR
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.billing import CajaTurno, CashMovement, Invoice
from app.models.finanzas import CuentaContable, CuentaPorCobrar, CuentaPorPagar, MovimientoFinanciero, PeriodoContable, CentroCosto, AsientoContable, LineaAsientoContable, GastoRegistrado
from app.models.order import Garment, Order, Payment
from app.models.purchasing import PurchaseOrder, Supplier
from app.models.client import Client
from app.models.company import Company
import app.services.finanzas as svc

router = APIRouter(prefix="/finanzas", tags=["finanzas"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
FinanzasAuth = Depends(require_roles("ADMIN", "FINANZAS"))

TALLER_FINALES = ("CALIDAD_OK", "entregado", "ENTREGADO")


def _taller_incompleto(db, order_id: int) -> bool:
    try:
        gs = db.query(Garment).filter(Garment.order_id == order_id).all()
        if not gs:
            return False
        return any((g.estado_taller or "") not in TALLER_FINALES for g in gs)
    except Exception:
        return False


def _sync_cxc(db):
    # espejo ventas primero para que OrdenVenta no quede vacía
    try:
        from app.services.ventas import sincronizar_espejo_ventas
        sincronizar_espejo_ventas(db)
    except Exception as e:
        _logger.warning("espejo ventas omitido: %s", e)
    orders = db.query(Order).filter(Order.estado.notin_(["cancelado", "CANCELADO"])).all()
    for o in orders:
        try:
            saldo = float(o.saldo or 0)
        except Exception:
            continue
        if saldo <= 0.01: continue
        vencida = bool(o.fecha_entrega and o.fecha_entrega < date.today())
        if vencida and _taller_incompleto(db, o.id):
            estado = "VENCIDO_EN_TALLER"
        elif vencida:
            estado = "VENCIDO"
        else:
            estado = "COBRADO_PARCIAL" if (o.anticipo or 0) > 0 else "PENDIENTE"
        c = db.query(CuentaPorCobrar).filter(CuentaPorCobrar.order_id == o.id).first()
        if c:
            c.monto_total=o.total or 0; c.monto_pagado=o.anticipo or 0; c.saldo_pendiente=saldo; c.cliente_id=o.client_id
            c.estado=estado
        else:
            db.add(CuentaPorCobrar(order_id=o.id, orden_venta_id=o.id, cliente_id=o.client_id, company_id=o.company_id, monto_total=o.total or 0, monto_pagado=o.anticipo or 0, saldo_pendiente=saldo, fecha_vencimiento=o.fecha_entrega, estado=estado))
    db.commit()

def _sync_cxp(db):
    try:
        from app.services.purchasing import sincronizar_espejo_compras
        sincronizar_espejo_compras(db)
    except Exception as e:
        _logger.warning("espejo compras omitido: %s", e)
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.estado.notin_(["cancelada", "CANCELADA", "recibida", "RECIBIDA", "COMPLETADA"])).all():
        if not db.query(CuentaPorPagar).filter(CuentaPorPagar.purchase_order_id==po.id).first():
            # evita duplicar si ya existe CxP del espejo OrdenCompra mismo folio
            try:
                from app.models.inventario import OrdenCompra
                oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first()
                if oc and db.query(CuentaPorPagar).filter(CuentaPorPagar.orden_compra_id==oc.id).first():
                    continue
            except Exception:
                pass
            total = float(po.total or 0)
            if total <= 0:
                continue
            db.add(CuentaPorPagar(proveedor_id=po.supplier_id, purchase_order_id=po.id, monto_total=total, monto_pagado=0, saldo_pendiente=total, retencion=0, estado="POR_PAGAR", fecha_emision=date.today(), fecha_vencimiento=date.today()+timedelta(days=30)))
    db.commit()

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db), user=FinanzasAuth):
    return RedirectResponse("/finanzas/cuentas-por-cobrar", status_code=302)

# --- NUEVO: Plan Contable ---
@router.get("/plan-contable", response_class=HTMLResponse)
def plan_contable(request: Request, db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    cuentas = db.query(CuentaContable).order_by(CuentaContable.codigo).all()
    return templates.TemplateResponse(request, "finanzas/plan_contable.html", {"user":user,"tab":"plan","cuentas":cuentas})

@router.post("/plan-contable", response_class=HTMLResponse)
def plan_crear(codigo: str=Form(...), nombre: str=Form(...), tipo: str=Form(...), nivel: int=Form(1), padre: str=Form(""), db: Session = Depends(get_db), user=FinanzasAuth):
    try:
        svc.get_or_create_cuenta(db, codigo, nombre, tipo, nivel, padre or None)
    except Exception as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/plan-contable", status_code=303)

# --- NUEVO: Periodos ---
@router.get("/periodos", response_class=HTMLResponse)
def periodos(request: Request, db: Session = Depends(get_db), user=FinanzasAuth):
    periodos = db.query(PeriodoContable).order_by(PeriodoContable.anio.desc(), PeriodoContable.mes.desc()).limit(24).all()
    return templates.TemplateResponse(request, "finanzas/periodos.html", {"user":user,"tab":"periodos","periodos":periodos})

# --- Asiento de Apertura / saldos iniciales ---
@router.get("/apertura", response_class=HTMLResponse)
def apertura(request: Request, error: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    diario = svc.obtener_diario(db, origen_tipo="APERTURA")
    return templates.TemplateResponse(request, "finanzas/apertura.html", {
        "user": user, "tab": "apertura", "error": error, "filas": diario["filas"]})


@router.post("/apertura", response_class=HTMLResponse)
def apertura_crear(fecha: str = Form(""), caja: float = Form(0), banco: float = Form(0),
                   inventario: float = Form(0), activofijo: float = Form(0),
                   cxp: float = Form(0), capital: float = Form(...),
                   db: Session = Depends(get_db), user=FinanzasAuth):
    # Asiento inicial: DEBE 1011+1041+2411+3341 / HABER 4212+5011.
    # Capital = Activo − Pasivo (se recalcula en servidor; el form lo prellena).
    svc.seed_pcge_basico(db)
    try:
        fe = date.fromisoformat(fecha) if fecha else date.today()
    except Exception:
        return RedirectResponse("/finanzas/apertura?error=fecha+inválida", status_code=303)
    debitos = {"1011": caja or 0, "1041": banco or 0, "2411": inventario or 0,
               "3341": activofijo or 0}
    pasivo = round(cxp or 0, 2)
    if any(v < 0 for v in list(debitos.values()) + [pasivo]) or capital <= 0:
        return RedirectResponse("/finanzas/apertura?error=montos+inválidos", status_code=303)
    debe = round(sum(debitos.values()), 2)
    esperado = round(debe - pasivo, 2)
    if debe <= 0 or abs(round(capital, 2) - esperado) > 0.01:
        return RedirectResponse(
            "/finanzas/apertura?error=descuadrado:+capital+debe+ser+activo+menos+pasivo", status_code=303)
    try:
        cuentas = {cod: svc.get_cuenta_by_codigo(db, cod)
                   for cod in ("1011", "1041", "2411", "3341", "4212", "5011")}
        if not all(cuentas.values()):
            raise ValueError("Faltan cuentas 1011/1041/2411/3341/4212/5011 (seed PCGE)")
        lineas = [{"cuenta_id": cuentas[c].id, "debe": Decimal(str(v)), "haber": Decimal("0")}
                  for c, v in debitos.items() if v > 0]
        if pasivo > 0:
            lineas.append({"cuenta_id": cuentas["4212"].id, "debe": Decimal("0"),
                           "haber": Decimal(str(pasivo))})
        lineas.append({"cuenta_id": cuentas["5011"].id, "debe": Decimal("0"),
                       "haber": Decimal(str(round(capital, 2)))})
        svc.crear_asiento(db, fe, "Asiento de apertura — saldos iniciales",
                          "APERTURA", None, lineas)
    except ValueError as e:
        return RedirectResponse(f"/finanzas/apertura?error={_quote(str(e))}", status_code=303)
    return RedirectResponse("/finanzas/apertura", status_code=303)


@router.post("/periodos", response_class=HTMLResponse)
def periodo_crear(anio: int=Form(...), mes: int=Form(...), db: Session = Depends(get_db), user=FinanzasAuth):
    try:
        svc.get_or_create_periodo(db, anio, mes)
    except Exception as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/periodos", status_code=303)

@router.post("/periodos/{pid}/cerrar", response_class=HTMLResponse)
def periodo_cerrar(pid: int, db: Session = Depends(get_db), user=FinanzasAuth):
    p=db.get(PeriodoContable,pid)
    if p: 
        try: svc.cerrar_periodo(db,p.anio,p.mes)
        except Exception as e: return HTMLResponse(str(e),status_code=400)
    return RedirectResponse("/finanzas/periodos", status_code=303)

@router.post("/periodos/{pid}/reabrir", response_class=HTMLResponse)
def periodo_reabrir(pid: int, db: Session = Depends(get_db), user=FinanzasAuth):
    p=db.get(PeriodoContable,pid)
    if p:
        try: svc.reabrir_periodo(db,p.anio,p.mes)
        except Exception as e: return HTMLResponse(str(e),status_code=400)
    return RedirectResponse("/finanzas/periodos", status_code=303)

# --- NUEVO: Libro Diario / Mayor / Balance ---
@router.get("/diario", response_class=HTMLResponse)
def diario(request: Request, periodo: str=Query(""), origen: str=Query(""), cuenta: str=Query(""),
           db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    pid=None
    periodo_obj = None
    if periodo:
        try:
            anio,mes=map(int,periodo.split("-"))
            p=db.query(PeriodoContable).filter(PeriodoContable.anio==anio,PeriodoContable.mes==mes).first()
            pid=p.id if p else None
            periodo_obj = p
        except Exception:
            pass
    # Filtro por cuenta PCGE (restringe a asientos que la tocan)
    cuenta_id = None
    if cuenta:
        cuenta_obj=db.query(CuentaContable).filter(CuentaContable.codigo==cuenta).first()
        cuenta_id=cuenta_obj.id if cuenta_obj else -1
    diario=svc.obtener_diario(db, periodo_id=pid, origen_tipo=origen or None,
                              cuenta_id=cuenta_id)
    origenes=sorted({a.origen_tipo for a in db.query(AsientoContable.origen_tipo).distinct().all() if a.origen_tipo})
    cuentas=db.query(CuentaContable).filter(CuentaContable.es_analitica==True).order_by(CuentaContable.codigo).all()
    return templates.TemplateResponse(request, "finanzas/diario.html", {"user":user,"tab":"diario",
        "filas":diario["filas"],"total_debe":diario["total_debe"],"total_haber":diario["total_haber"],
        "diario_cuadrado":diario["cuadrado"],"n":diario["n"],
        "periodo":periodo,"pid":pid,"origen":origen,"origenes":origenes,
        "cuenta":cuenta,"cuentas":cuentas})

@router.get("/mayor", response_class=HTMLResponse)
def mayor(request: Request, cuenta: str=Query(""), periodo: str=Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    cuenta_id=None
    if cuenta:
        c=svc.get_cuenta_by_codigo(db, cuenta)
        cuenta_id=c.id if c else None
    pid=None
    if periodo:
        try:
            anio,mes=map(int,periodo.split("-")); p=db.query(PeriodoContable).filter(PeriodoContable.anio==anio,PeriodoContable.mes==mes).first(); pid=p.id if p else None
        except Exception:
            pass
    lineas=svc.obtener_mayor(db, cuenta_id=cuenta_id, periodo_id=pid)
    cuentas=db.query(CuentaContable).order_by(CuentaContable.codigo).all()
    return templates.TemplateResponse(request, "finanzas/mayor.html", {"user":user,"tab":"mayor","lineas":lineas,"cuentas":cuentas,"cuenta":cuenta,"periodo":periodo})

@router.get("/balance", response_class=HTMLResponse)
def balance(request: Request, periodo: str=Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    pid=None
    if periodo:
        try:
            anio,mes=map(int,periodo.split("-")); p=db.query(PeriodoContable).filter(PeriodoContable.anio==anio,PeriodoContable.mes==mes).first(); pid=p.id if p else None
        except: pass
    bal=svc.obtener_balance_comprobacion(db, periodo_id=pid)
    er=svc.obtener_estado_resultados(db, periodo_id=pid)
    bg=svc.obtener_balance_general(db, periodo_id=pid)
    return templates.TemplateResponse(request, "finanzas/balance.html", {"user":user,"tab":"balance","bal":bal,"er":er,"bg":bg,"periodo":periodo,"pid":pid})


@router.get("/estados-financieros", response_class=HTMLResponse)
def estados_financieros(request: Request, periodo: str=Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    """Alias canónico de /balance exigido por tests e integración (periodo=YYYY-MM)."""
    svc.seed_pcge_basico(db)
    pid=None
    if periodo:
        try:
            anio, mes = map(int, periodo.split("-"))
            p = db.query(PeriodoContable).filter(PeriodoContable.anio==anio, PeriodoContable.mes==mes).first()
            pid = p.id if p else None
        except Exception:
            pass
    bal=svc.obtener_balance_comprobacion(db, periodo_id=pid)
    er=svc.obtener_estado_resultados(db, periodo_id=pid)
    bg=svc.obtener_balance_general(db, periodo_id=pid)
    return templates.TemplateResponse(request, "finanzas/balance.html", {"user":user,"tab":"balance","bal":bal,"er":er,"bg":bg,"periodo":periodo,"pid":pid})

# --- Existentes mejorados: CxC/CxP/Flujo/Rentabilidad/Reportes ---
@router.get("/cuentas-por-cobrar", response_class=HTMLResponse)
def cxc(request: Request, estado: str = Query(""), cliente: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    _sync_cxc(db)
    q=db.query(CuentaPorCobrar)
    if estado: q=q.filter(CuentaPorCobrar.estado==estado.upper())
    if cliente:
        like=f"%{cliente}%"
        ids=[o.id for o in db.query(Order).join(Client, Order.client_id==Client.id).filter((Client.nombre.like(like)) | (Client.apellidos.like(like))).all()] if cliente else []
        ids += [o.id for o in db.query(Order).join(Company, Order.company_id==Company.id).filter(Company.nombre_comercial.like(like)).all()]
        if ids: q=q.filter(CuentaPorCobrar.order_id.in_(ids))
    cuentas=q.order_by(CuentaPorCobrar.id.desc()).limit(200).all()
    orders_map={o.id:o for o in db.query(Order).filter(Order.id.in_([c.order_id for c in cuentas if c.order_id])).all()}
    clientes={}
    for c in cuentas:
        o=orders_map.get(c.order_id)
        if o and o.client_id:
            cl=db.get(Client, o.client_id); clientes[c.id]=f"{cl.nombre} {cl.apellidos}" if cl else "—"
        elif o and o.company_id:
            comp=db.get(Company, o.company_id); clientes[c.id]=comp.nombre_comercial if comp else "—"
        else: clientes[c.id]="—"
    # buscador vinculado a la base de clientes (personas + empresas)
    deudores=[{"label": f"{p.nombre} {p.apellidos}", "value": p.nombre}
              for p in db.query(Client).order_by(Client.apellidos).limit(300).all()]
    deudores += [{"label": f"🏢 {e.nombre_comercial}", "value": e.nombre_comercial}
                 for e in db.query(Company).order_by(Company.nombre_comercial).limit(300).all()]
    # también expone asientos recientes para trazabilidad
    asientos=db.query(AsientoContable).filter(AsientoContable.origen_tipo=="COBRO").order_by(AsientoContable.id.desc()).limit(5).all()
    return templates.TemplateResponse(request, "finanzas/cuentas_por_cobrar.html", {"user":user,"tab":"cxc","cuentas":cuentas,"orders_map":orders_map,"clientes":clientes,"estado":estado,"cliente":cliente,"asientos":asientos,"deudores":deudores})

import logging as _log
_logger = _log.getLogger("finanzas")

def _aplicar_abono_cxc(db: Session, user, cuenta_id: int, monto: float, metodo: str):
    """Abono CxC vía motor atómico (Payment + caja/flujo + CxC + asiento COBRO).

    Sin turno abierto se registra igual (solo warning) para no bloquear el cobro.
    """
    from app.services import contabilidad as contab

    c = db.get(CuentaPorCobrar, cuenta_id)
    if not c or monto <= 0 or monto > (c.saldo_pendiente or 0) + 1e-9:
        return None
    if not c.order_id:
        return None
    _logger.info("abono CxC %s (usuario %s): cobro sin turno exigido", cuenta_id, user.id)
    try:
        from app.services.ventas import turno_abierto as _turno_abierto
        _t = _turno_abierto(db, user.id)
        _tid = _t.id if _t else None
    except Exception:
        _tid = None
    contab.cobrar_venta(db, c.order_id, float(monto), metodo or "efectivo",
                        cuenta_codigo="1011", usuario_id=user.id,
                        exigir_turno=False, turno_id=_tid)
    # Compat gestión: al saldar, la orden queda lista para entrega.
    try:
        o = db.get(Order, c.order_id)
        if o is not None and o.saldo <= 0.01 and o.estado != "entregado":
            o.estado = "entregado"
            db.commit()
    except Exception:
        pass
    return True

@router.post("/cuentas-por-cobrar/abono", response_class=HTMLResponse)
def cxc_abono(cuenta_id: int = Form(...), monto: float = Form(...), metodo: str = Form("efectivo"), db: Session = Depends(get_db), user=FinanzasAuth):
    _aplicar_abono_cxc(db, user, cuenta_id, monto, metodo)
    return RedirectResponse("/finanzas/cuentas-por-cobrar", status_code=303)

@router.post("/cuentas-por-cobrar/conciliar", response_class=HTMLResponse)
def cxc_conciliar(cuenta_id: int = Form(...), monto: float = Form(...), db: Session = Depends(get_db), user=FinanzasAuth):
    _aplicar_abono_cxc(db, user, cuenta_id, monto, "transferencia")
    return RedirectResponse("/finanzas/cuentas-por-cobrar", status_code=303)

@router.get("/cuentas-por-pagar", response_class=HTMLResponse)
def cxp(request: Request, estado: str = Query(""), proveedor: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    _sync_cxp(db)
    q=db.query(CuentaPorPagar)
    if estado: q=q.filter(CuentaPorPagar.estado==estado.upper())
    if proveedor:
        try:
            q=q.filter(CuentaPorPagar.proveedor_id==int(proveedor))
        except ValueError:
            like=f"%{proveedor}%"
            ids=[s.id for s in db.query(Supplier).filter(Supplier.nombre.like(like)).all()]
            q=q.filter(CuentaPorPagar.proveedor_id.in_(ids or [0]))
    cuentas=q.order_by(CuentaPorPagar.id.desc()).limit(200).all()
    proveedores={s.id:s for s in db.query(Supplier).all()}
    return templates.TemplateResponse(request, "finanzas/cuentas_por_pagar.html", {"user":user,"tab":"cxp","cuentas":cuentas,"proveedores":proveedores,"estado":estado,"proveedor":proveedor,"suppliers":db.query(Supplier).order_by(Supplier.nombre).all()})

@router.post("/cuentas-por-pagar", response_class=HTMLResponse)
def cxp_crear(proveedor_id: int = Form(...), numero_factura: str = Form(""), monto_total: float = Form(...), retencion: float = Form(0), fecha_emision: str = Form(""), fecha_vencimiento: str = Form(""), db: Session = Depends(get_db), user=FinanzasAuth):
    from datetime import timedelta
    if monto_total <= 0:
        return HTMLResponse("monto_total debe ser positivo", status_code=400)
    if retencion < 0 or retencion > monto_total:
        return HTMLResponse("retencion inválida (0 <= retencion <= total)", status_code=400)
    try:
        fe=date.fromisoformat(fecha_emision) if fecha_emision else date.today()
        fv=date.fromisoformat(fecha_vencimiento) if fecha_vencimiento else date.today()+timedelta(days=30)
    except Exception:
        fe=date.today(); fv=date.today()+timedelta(days=30)
    # Provisión atómica: el monto ingresado es TOTAL FINAL con IGV incluido.
    # Base = total/1.18, IGV = total - base, pasivo 4212 = total.
    from app.services import contabilidad as contab
    try:
        base = round(monto_total / 1.18, 2)
        contab.provisionar_compra(db, proveedor_id=proveedor_id, base=base,
                                  igv=round(monto_total - base, 2),
                                  numero_factura=numero_factura or None, fecha=fe,
                                  retencion=retencion, fecha_vencimiento=fv)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/cuentas-por-pagar", status_code=303)

@router.post("/cuentas-por-pagar/{cid}/pagar", response_class=HTMLResponse)
def cxp_pagar(cid: int, monto: float = Form(...), cuenta_origen: str = Form("Banco"), db: Session = Depends(get_db), user=FinanzasAuth):
    # Pago atómico: CxP + EGRESO flujo + caja + asiento 4212/1011-1041 (bloquea período cerrado)
    from app.services import contabilidad as contab
    try:
        contab.pagar_proveedor(db, cid, float(monto),
                               cuenta_codigo="1041" if cuenta_origen == "Banco" else "1011",
                               usuario_id=user.id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/cuentas-por-pagar", status_code=303)

@router.get("/gastos", response_class=HTMLResponse)
def gastos_list(request: Request, estado: str = Query(""), categoria: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    svc.seed_pcge_basico(db)
    q = db.query(GastoRegistrado).order_by(GastoRegistrado.id.desc())
    if estado:
        q = q.filter(GastoRegistrado.estado == estado.upper())
    if categoria:
        q = q.filter(GastoRegistrado.categoria == categoria.upper())
    gastos = q.limit(200).all()
    cuentas = db.query(CuentaContable).filter(CuentaContable.activo == True).order_by(CuentaContable.codigo).all()  # noqa: E712
    centros = db.query(CentroCosto).filter(CentroCosto.activo == True).order_by(CentroCosto.codigo).all()  # noqa: E712
    proveedores = db.query(Supplier).order_by(Supplier.nombre).all()
    cuentas_map = {c.id: c.codigo for c in db.query(CuentaContable).all()}
    prov_map = {s.id: s.nombre for s in proveedores}
    return templates.TemplateResponse(request, "finanzas/gastos.html", {
        "user": user, "tab": "gastos", "gastos": gastos, "cuentas": cuentas,
        "centros": centros, "proveedores": proveedores,
        "cuentas_map": cuentas_map, "prov_map": prov_map,
        "estado": estado, "categoria": categoria,
        "categorias": ["ALQUILER", "HONORARIOS", "PLANILLA", "ACTIVO_FIJO", "OTRO"],
        "map": svc.CATEGORIA_GASTO_MAP,
    })


@router.post("/gastos", response_class=HTMLResponse)
def gastos_crear(
    fecha: str = Form(""), proveedor_id: str = Form(""), ruc: str = Form(""),
    tipo_comprobante: str = Form("FACTURA"), numero_comprobante: str = Form(""),
    categoria: str = Form("OTRO"), cuenta_codigo: str = Form(""),
    monto_total: float = Form(0), monto_base: float = Form(0),
    igv: float = Form(0),
    centro_costo_id: str = Form(""), db: Session = Depends(get_db), user=FinanzasAuth,
):
    try:
        fe = date.fromisoformat(fecha) if fecha else date.today()
    except Exception:
        fe = date.today()
    try:
        pid = int(proveedor_id) if proveedor_id else None
    except Exception:
        pid = None
    try:
        ccid = int(centro_costo_id) if centro_costo_id else None
    except Exception:
        ccid = None
    cat = (categoria or "OTRO").upper()
    cta = cuenta_codigo or (svc.CATEGORIA_GASTO_MAP.get(cat, svc.CATEGORIA_GASTO_MAP["OTRO"])[0])
    # El monto ingresado es TOTAL FINAL con IGV incluido (FACTURA: se desglosa
    # base = total/1.18; otros comprobantes: todo va a base). Compat: si no
    # viene monto_total se respetan monto_base + igv explícitos.
    if monto_total and monto_total > 0:
        if (tipo_comprobante or "FACTURA").upper() == "FACTURA":
            monto_base = round(monto_total / 1.18, 2)
            igv = round(monto_total - monto_base, 2)
        else:
            monto_base, igv = monto_total, 0.0
    # Provisión atómica con bloqueo de período cerrado
    from app.services import contabilidad as contab
    try:
        contab.registrar_gasto_atomico(
            db, fecha=fe, categoria=cat, monto_base=monto_base, monto_igv=igv,
            cuenta_codigo=cta, proveedor_id=pid, ruc_proveedor=ruc or None,
            tipo_comprobante=tipo_comprobante or None, numero_comprobante=numero_comprobante or None,
            centro_costo_id=ccid,
        )
    except Exception as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/gastos", status_code=303)


@router.post("/gastos/{gid}/pagar", response_class=HTMLResponse)
def gastos_pagar(gid: int, cuenta_origen: str = Form("104"), db: Session = Depends(get_db), user=FinanzasAuth):
    # Pago atómico: 4212/caja-banco + EGRESO flujo + caja (bloquea período cerrado)
    from app.services import contabilidad as contab
    try:
        contab.pagar_gasto_atomico(db, gid,
                                   cuenta_origen_codigo=cuenta_origen if cuenta_origen in ("101", "104") else "104",
                                   usuario_id=user.id)
    except Exception as e:
        status = 404 if "no encontrado" in str(e).lower() else 400
        return HTMLResponse(str(e), status_code=status)
    return RedirectResponse("/finanzas/gastos", status_code=303)

@router.get("/flujo-caja", response_class=HTMLResponse)
def flujo_caja(request: Request, periodo: str = Query("mes"), db: Session = Depends(get_db), user=FinanzasAuth):
    import calendar as _cal
    ingresos=db.query(func.coalesce(func.sum(MovimientoFinanciero.monto),0)).filter(MovimientoFinanciero.tipo=="INGRESO").scalar() or 0
    egresos=db.query(func.coalesce(func.sum(MovimientoFinanciero.monto),0)).filter(MovimientoFinanciero.tipo=="EGRESO").scalar() or 0
    ingresos_cash=db.query(func.coalesce(func.sum(CashMovement.monto),0)).filter(CashMovement.tipo=="ingreso").scalar() or 0
    egresos_cash=db.query(func.coalesce(func.sum(CashMovement.monto),0)).filter(CashMovement.tipo=="egreso").scalar() or 0
    total_ing=round(ingresos+ingresos_cash,2); total_egr=round(egresos+egresos_cash,2); liquidez=round(total_ing-total_egr,2)
    apertura=db.query(func.coalesce(func.sum(CajaTurno.saldo_apertura),0)).scalar() or 0
    cats=db.query(MovimientoFinanciero.categoria, func.sum(MovimientoFinanciero.monto)).group_by(MovimientoFinanciero.categoria).all()
    monthly=[]
    hoy = date.today()
    for i in range(5, -1, -1):
        # aritmética real de meses (no 30 días)
        m = hoy.month - i
        y = hoy.year
        while m <= 0:
            m += 12; y -= 1
        start = date(y, m, 1)
        last = _cal.monthrange(y, m)[1]
        end = date(y, m, last) if i == 0 else date(
            (y + (1 if m == 12 else 0)), (1 if m == 12 else m + 1), 1,
        )
        if i == 0:
            # mes en curso: hasta hoy inclusive -> end exclusivo mañana
            end = hoy + timedelta(days=1)
        ing=db.query(func.coalesce(func.sum(MovimientoFinanciero.monto),0)).filter(MovimientoFinanciero.tipo=="INGRESO", MovimientoFinanciero.fecha>=start, MovimientoFinanciero.fecha<end).scalar() or 0
        egr=db.query(func.coalesce(func.sum(MovimientoFinanciero.monto),0)).filter(MovimientoFinanciero.tipo=="EGRESO", MovimientoFinanciero.fecha>=start, MovimientoFinanciero.fecha<end).scalar() or 0
        monthly.append({"mes":start.strftime("%Y-%m"),"ingresos":round(ing,2),"egresos":round(egr,2)})
    movs=db.query(MovimientoFinanciero).order_by(MovimientoFinanciero.fecha.desc()).limit(50).all()
    # Flujo real por actividad (operativa/inversión/financiamiento) y por origen caja/bancos
    from app.services import contabilidad as contab
    flujo_act = contab.flujo_por_actividad(db)
    return templates.TemplateResponse(request, "finanzas/flujo_caja.html", {"user":user,"tab":"flujo","total_ing":total_ing,"total_egr":total_egr,"liquidez":liquidez,"apertura":apertura,"cats":cats,"monthly":monthly,"movs":movs,"flujo_act":flujo_act})

@router.get("/rentabilidad", response_class=HTMLResponse)
def rentabilidad(request: Request, umbral: float = Query(40.0), db: Session = Depends(get_db), user=FinanzasAuth):
    from app.core.constants import CONSUMO_TELA_M
    from app.models.inventory import Fabric
    from app.models.inventario import ProductoInsumo
    COSTO_MINUTO_DESTAJO_DEFAULT = 0.5  # fallback si no hay MOD devengada registrada
    # Tarifa implícita: MOD devengada total / minutos totales (desacoplado, solo lectura)
    try:
        mod_total = float(svc.calcular_mod_devengada(db))
    except Exception:
        mod_total = 0.0
    from app.models.order import WorkLog as _WL
    minutos_totales = db.query(func.coalesce(func.sum(_WL.minutos_reales), 0)).scalar() or 0
    tarifa_media = round(mod_total / minutos_totales, 4) if minutos_totales and mod_total else COSTO_MINUTO_DESTAJO_DEFAULT
    orders=db.query(Order).order_by(Order.id.desc()).limit(100).all()
    rows=[]
    for o in orders:
        garments=db.query(Garment).filter(Garment.order_id==o.id).all()
        if not garments: continue
        costo_telas=0.0
        for g in garments:
            consumo=CONSUMO_TELA_M.get(g.tipo,1.5)
            tela=db.get(Fabric,g.tela_id) if g.tela_id else None
            if tela: costo_telas+=consumo*(tela.precio_metro or 0)
            else:
                prod=db.query(ProductoInsumo).filter(ProductoInsumo.sku==str(g.tela_id)).first() if g.tela_id else None
                if prod: costo_telas+=consumo*(prod.costo_unitario or 0)
        from app.models.order import WorkLog
        minutos=db.query(func.coalesce(func.sum(WorkLog.minutos_reales),0)).filter(WorkLog.garment_id.in_([gg.id for gg in garments])).scalar() or 0
        costo_destajo=round(minutos*tarifa_media,2)
        precio=o.total or sum(g.precio for g in garments) or 0
        costo_total=round(costo_telas+costo_destajo,2)
        margen=round(precio-costo_total,2) if precio else 0
        margen_pct=round((margen/precio*100) if precio else 0,1)
        alerta=margen_pct<umbral
        rows.append({"order":o,"precio":precio,"costo_telas":round(costo_telas,2),"costo_destajo":costo_destajo,"costo_total":costo_total,"margen":margen,"margen_pct":margen_pct,"alerta":alerta,"garments":garments})
    return templates.TemplateResponse(request, "finanzas/rentabilidad.html", {"user":user,"tab":"rentabilidad","rows":rows,"umbral":umbral,"tarifa_media":tarifa_media})

@router.get("/reportes-contables", response_class=HTMLResponse)
def reportes(request: Request, desde: str = Query(""), hasta: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    # expone también periodos y balances derivados del mayor
    periodos=db.query(PeriodoContable).order_by(PeriodoContable.anio.desc(), PeriodoContable.mes.desc()).limit(12).all()
    # balances
    try:
        bal=svc.obtener_balance_comprobacion(db)
        bg=svc.obtener_balance_general(db)
        er=svc.obtener_estado_resultados(db)
    except Exception as e:
        _logger.warning("reportes-contables sin balances: %s", e)
        bal=[]; bg={}; er={}
    return templates.TemplateResponse(request, "finanzas/reportes_contables.html", {"user":user,"tab":"reportes","desde":desde,"hasta":hasta,"bal":bal,"bg":bg,"er":er,"periodos":periodos})

@router.get("/reportes-contables/ventas.csv")
def export_ventas(desde: str = Query(""), hasta: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    q=db.query(Invoice)
    if desde:
        try: q=q.filter(Invoice.created_at >= date.fromisoformat(desde))
        except: pass
    if hasta:
        try: q=q.filter(Invoice.created_at <= date.fromisoformat(hasta))
        except: pass
    invs=q.order_by(Invoice.id).all()
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow(["serie","numero","order_id","subtotal","igv_pct","igv","total","estado","fecha"])
    for inv in invs: w.writerow([inv.serie,inv.numero,inv.order_id or "",inv.subtotal,inv.igv_pct,inv.igv,inv.total,inv.estado,inv.created_at])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=ventas.csv"})

@router.get("/reportes-contables/compras.csv")
def export_compras(desde: str = Query(""), hasta: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    q=db.query(PurchaseOrder)
    if desde:
        try: q=q.filter(PurchaseOrder.created_at >= date.fromisoformat(desde))
        except: pass
    if hasta:
        try: q=q.filter(PurchaseOrder.created_at <= date.fromisoformat(hasta))
        except: pass
    ocs=q.order_by(PurchaseOrder.id).all()
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow(["folio","supplier_id","subtotal","igv","total","estado","fecha"])
    for po in ocs: w.writerow([po.folio,po.supplier_id,po.subtotal,po.igv,po.total,po.estado,po.created_at])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=compras.csv"})

@router.get("/reportes-contables/igv", response_class=HTMLResponse)
def igv_resumen(request: Request, desde: str = Query(""), hasta: str = Query(""), db: Session = Depends(get_db), user=FinanzasAuth):
    q_igv = db.query(Invoice).filter(Invoice.estado == "emitida")
    q_oc = db.query(PurchaseOrder)
    if desde:
        try:
            d = date.fromisoformat(desde)
            q_igv = q_igv.filter(Invoice.created_at >= d)
            q_oc = q_oc.filter(PurchaseOrder.created_at >= d)
        except Exception:
            pass
    if hasta:
        try:
            h = date.fromisoformat(hasta)
            q_igv = q_igv.filter(Invoice.created_at <= h)
            q_oc = q_oc.filter(PurchaseOrder.created_at <= h)
        except Exception:
            pass
    igv_cobrado = q_igv.with_entities(func.coalesce(func.sum(Invoice.igv), 0)).scalar() or 0
    # IGV compras: usa columna desagregada cuando existe; si es 0 presume total con IGV incluido
    igv_registrado = q_oc.with_entities(func.coalesce(func.sum(PurchaseOrder.igv), 0)).scalar() or 0
    if float(igv_registrado or 0) > 0:
        igv_pagado = round(float(igv_registrado), 2)
    else:
        total_compras = q_oc.with_entities(func.coalesce(func.sum(PurchaseOrder.total), 0)).scalar() or 0
        igv_pagado = round(float(total_compras) * 0.18 / 1.18 if total_compras else 0.0, 2)
    return templates.TemplateResponse(request, "finanzas/reportes_contables.html", {"user":user,"tab":"reportes","igv_cobrado":round(float(igv_cobrado),2),"igv_pagado":igv_pagado,"desde":desde,"hasta":hasta,"reporte_igv":True})


@router.post("/depreciacion", response_class=HTMLResponse)
def depreciar(fecha: str = Form(""), monto: float = Form(...), db: Session = Depends(get_db), user=FinanzasAuth):
    try:
        fe = date.fromisoformat(fecha) if fecha else date.today()
    except Exception:
        fe = date.today()
    try:
        svc.registrar_depreciacion(db, fe, monto)
    except Exception as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/reportes-contables", status_code=303)


@router.post("/periodos/{pid}/cierre-resultados", response_class=HTMLResponse)
def periodo_cierre_resultados(pid: int, db: Session = Depends(get_db), user=FinanzasAuth):
    p = db.get(PeriodoContable, pid)
    if not p:
        return HTMLResponse("Periodo no encontrado", status_code=404)
    try:
        svc.cierre_resultados(db, p.anio, p.mes)
    except Exception as e:
        return HTMLResponse(str(e), status_code=400)
    return RedirectResponse("/finanzas/periodos", status_code=303)

