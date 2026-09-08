"""Router Rendimiento & Destajo Operario (/rendimiento).

Independiente: usa tablas rendimiento_* (no taller_*), pero reutiliza TALLER_TAREAS para seed.
Decimal para cálculos, filtros semana/mes/rango, export a Finanzas.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, Form, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.constants import TALLER_TAREAS
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.order import Garment, Order
from app.modules.rendimiento.models import CatalogoOperacion, DetalleJornada, RegistroJornada
import functools
import logging
import traceback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rendimiento", tags=["rendimiento"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Auth = Depends(require_roles("SASTRE-MAESTRO", "SASTRE-ASISTENTE"))  # ADMIN pasa siempre
FinanzasSM = Depends(require_roles("ADMIN", "FINANZAS", "SASTRE-MAESTRO"))


def _con_diagnostico(origen: str):
    """Envuelve el endpoint en try/except: imprime el traceback completo en
    logs y devuelve un JSON 500 estructurado (traceback solo fuera de prod)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                traceback.print_exc()
                logger.error("Error en %s: %s: %s", origen, type(e).__name__, e)
                body: dict = {"error": f"{type(e).__name__}: {e}"}
                if not settings.is_production:
                    body["traceback"] = traceback.format_exc()
                return JSONResponse(body, status_code=500)
        return wrapper
    return deco

def _grupos_tareo(catalogo: list) -> list[tuple[str, list]]:
    """Agrupa la hoja de ruta por etapas de producción (posición 1-based).

    a) Corte y Habilitación (01-06) · b) Fusionado y Planchado Base (10-13) ·
    c) Confección y Ensamble (07-09, 14-30) · d) Acabados y Ojales (31+).
    """
    grupos = [("✂️ Corte y Habilitación", []),
              ("🧵 Confección y Ensamble", []),
              ("🔥 Fusionado y Planchado Base", []),
              ("✨ Acabados y Ojales", [])]
    for idx, op in enumerate(catalogo, start=1):
        if 1 <= idx <= 6:
            grupos[0][1].append(op)
        elif 10 <= idx <= 13:
            grupos[2][1].append(op)
        elif idx >= 31:
            grupos[3][1].append(op)
        else:
            grupos[1][1].append(op)
    return [(t, ops) for t, ops in grupos if ops]


def _ensure_catalogo(db: Session):
    """Seed idempotente 44 operaciones con código CAT-01.."""
    existing = db.query(CatalogoOperacion).count()
    if existing >= len(TALLER_TAREAS):
        return
    for idx, (desc, tarifa) in enumerate(TALLER_TAREAS, start=1):
        codigo = f"OP-{idx:02d}"
        if not db.query(CatalogoOperacion).filter(CatalogoOperacion.codigo == codigo).first():
            # evita duplicado por nombre si ya existe con otro código
            if not db.query(CatalogoOperacion).filter(CatalogoOperacion.nombre_operacion == desc).first():
                db.add(CatalogoOperacion(codigo=codigo, nombre_operacion=desc, tarifa_base=Decimal(str(tarifa)), activa=True))
    db.commit()

def _rango(preset: str, desde: str = "", hasta: str = "") -> tuple[date, date]:
    hoy = date.today()
    if preset == "hoy" or preset == "diario":
        return hoy, hoy
    if preset == "semana" or preset == "semana_actual" or preset == "semanal":
        inicio = hoy - timedelta(days=hoy.weekday())  # lunes
        fin = inicio + timedelta(days=6)
    elif preset == "mes" or preset == "mes_actual" or preset == "mensual":
        inicio = hoy.replace(day=1)
        # último día del mes
        if hoy.month == 12:
            fin = date(hoy.year+1, 1, 1) - timedelta(days=1)
        else:
            fin = date(hoy.year, hoy.month+1, 1) - timedelta(days=1)
    elif preset == "personalizado" and desde and hasta:
        inicio = date.fromisoformat(desde)
        fin = date.fromisoformat(hasta)
        if inicio > fin:
            inicio, fin = fin, inicio
    else:
        # por defecto semana
        inicio = hoy - timedelta(days=hoy.weekday())
        fin = inicio + timedelta(days=6)
    return inicio, fin

def _sede_filter_query(db: Session, user):
    """Prendas que ya superaron la etapa En Prueba (destajo posterior al fitting).

    Solo EN_CONFECCION / ACABADOS / CALIDAD_OK (+ equivalentes legacy).
    Entregadas/terminadas se excluyen (ya archivadas del kanban).
    Filtra por sede si aplica (proxy sastre_id).
    """
    from app.core.constants import ETAPAS_POST_PRUEBA_TODAS
    q = db.query(Garment).filter(Garment.estado_taller.in_(ETAPAS_POST_PRUEBA_TODAS))
    sede_id = getattr(user, "sede_id", None)
    if sede_id:
        # si Order tuviera sede_id, filtraría. Por ahora intenta filtrar por sastre_id como proxy
        q = q.filter(Garment.artesano_id == user.id)
    return q

@router.get("/registro/marcadas", response_class=JSONResponse)
def marcadas_por_prenda(garment_id: int = Query(...), db: Session = Depends(get_db), user=Auth):
    """Memoria de tareo: actividades ya marcadas para una prenda (quién/cuándo)."""
    from app.models.user import User
    marcadas = []
    for d in db.query(DetalleJornada).filter(DetalleJornada.orden_produccion_id == garment_id).all():
        reg = db.get(RegistroJornada, d.registro_jornada_id)
        op = db.get(CatalogoOperacion, d.operacion_id) if d.operacion_id else None
        emp = db.get(User, reg.operario_id) if reg else None
        marcadas.append({
            "operacion_id": d.operacion_id,
            "codigo": op.codigo if op else "",
            "nombre": op.nombre_operacion if op else "",
            "operario": emp.full_name if emp else "",
            "fecha": reg.fecha.isoformat() if reg and reg.fecha else "",
            "registro_id": d.registro_jornada_id,
        })
    return {"garment_id": garment_id, "marcadas": marcadas}

@router.get("", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db), user=Auth):
    return RedirectResponse("/rendimiento/registro", status_code=302)

@router.get("/registro", response_class=HTMLResponse)
def registro_get(request: Request, todas: str = Query(""), db: Session = Depends(get_db), user=Auth):
    _ensure_catalogo(db)
    catalogo = db.query(CatalogoOperacion).filter(CatalogoOperacion.activa.is_(True)).order_by(CatalogoOperacion.id).all()
    # prendas que superaron En Prueba (salvo ?todas=1 que muestra en proceso)
    if todas:
        prendas = db.query(Garment).filter(Garment.estado_taller.notin_(["terminado", "entregado"])).order_by(Garment.id.desc()).limit(80).all()
    else:
        prendas = _sede_filter_query(db, user).order_by(Garment.id.desc()).limit(80).all()
    # para mostrar folio
    orders = {o.id: o for o in db.query(Order).filter(Order.id.in_([g.order_id for g in prendas])).all()} if prendas else {}
    # dueño del pedido: nombre del cliente o empresa
    from app.models.client import Client
    from app.models.company import Company
    propietarios: dict[int, str] = {}
    empresas: dict[int, str] = {}
    if orders:
        cls = {c.id: c for c in db.query(Client).filter(Client.id.in_([o.client_id for o in orders.values() if o.client_id])).all()}
        comps = {c.id: c for c in db.query(Company).filter(Company.id.in_([o.company_id for o in orders.values() if o.company_id])).all()}
        for o in orders.values():
            if o.client_id and o.client_id in cls:
                propietarios[o.id] = f"{cls[o.client_id].nombre} {cls[o.client_id].apellidos or ''}".strip()
                # B2B: colaborador con empresa facturadora → paréntesis
                if o.company_id and o.company_id in comps:
                    empresas[o.id] = comps[o.company_id].nombre_comercial
            elif o.company_id and o.company_id in comps:
                propietarios[o.id] = comps[o.company_id].nombre_comercial
            else:
                propietarios[o.id] = "—"
    from app.core.constants import TALLER_KANBAN
    etapas = dict(TALLER_KANBAN)
    # histórico del operario hoy
    hoy = date.today()
    mias = db.query(RegistroJornada).filter(RegistroJornada.operario_id==user.id, RegistroJornada.fecha==hoy).order_by(RegistroJornada.id.desc()).limit(10).all()
    # resumen del día: operaciones marcadas + subtotal S/
    mias_ids = [r.id for r in mias]
    mias_dets = db.query(DetalleJornada).filter(DetalleJornada.registro_jornada_id.in_(mias_ids)).all() if mias_ids else []
    mias_ops = sum((d.cantidad or 0) for d in mias_dets)
    mias_subtotal = sum(((d.subtotal if d.subtotal is not None else Decimal("0")) for d in mias_dets), Decimal("0"))
    # agrupación visual por etapas (posición 1-based en la hoja de ruta)
    grupos = _grupos_tareo(catalogo)
    return templates.TemplateResponse(request, "rendimiento/registro_diario.html", {
        "user": user, "tab": "registro", "catalogo": catalogo, "grupos": grupos,
        "prendas": prendas, "orders": orders, "propietarios": propietarios, "empresas": empresas, "etapas": etapas,
        "mias": mias, "mias_ops": mias_ops, "mias_subtotal": mias_subtotal,
        "hoy": hoy, "todas": todas})

@router.post("/registro", response_class=HTMLResponse)
async def registro_post(request: Request, db: Session = Depends(get_db), user=Auth):
    _ensure_catalogo(db)
    form = await request.form()
    orden_id = form.get("orden_produccion_id") or form.get("orden_id") or form.get("garment_id")
    observaciones = form.get("observaciones") or ""
    try:
        gid = int(orden_id) if orden_id else None
    except:
        gid = None
    # valida prenda existe si se especificó
    garment = db.get(Garment, gid) if gid else None
    # la prenda es obligatoria: sin prenda no hay tareo
    if not garment:
        return RedirectResponse("/rendimiento/registro?error=sin_prenda", status_code=303)
    # el tareo con prenda exige etapa posterior a En Prueba
    if garment:
        from app.core.constants import ETAPAS_POST_PRUEBA, KANBAN_MAP
        etapa = KANBAN_MAP.get(garment.estado_taller or "", garment.estado_taller or "")
        if etapa not in ETAPAS_POST_PRUEBA:
            return RedirectResponse("/rendimiento/registro?error=etapa_prueba", status_code=303)
    # recolecta operaciones seleccionadas
    ops = []
    for key in form.keys():
        if key.startswith("op_"):
            try:
                op_id = int(key[3:])
                cantidad = int(form.get(f"cantidad_{op_id}") or "1")
                if cantidad < 1:
                    cantidad = 1
                # checkbox value
                if form.get(key) in ("on", "1", "true", str(op_id)):
                    ops.append((op_id, cantidad))
            except:
                continue
    # alternativa: lista `operaciones` multiple
    if not ops:
        try:
            for v in form.getlist("operaciones"):
                op_id = int(v)
                cantidad = int(form.get(f"cantidad_{op_id}") or "1")
                ops.append((op_id, cantidad))
        except:
            pass
    if not ops:
        return RedirectResponse("/rendimiento/registro?error=no_ops", status_code=303)
    # valida operaciones activas
    catalogo_map = {c.id: c for c in db.query(CatalogoOperacion).filter(CatalogoOperacion.id.in_([o[0] for o in ops])).all()}
    if len(catalogo_map) != len(ops):
        return RedirectResponse("/rendimiento/registro?error=invalid_op", status_code=303)
    # memoria: descarta actividades ya marcadas en esta prenda (por cualquiera)
    if gid:
        ya = {d.operacion_id for d in db.query(DetalleJornada).filter(DetalleJornada.orden_produccion_id == gid).all()}
        ops = [(op_id, cant) for op_id, cant in ops if op_id not in ya]
        if not ops:
            return RedirectResponse("/rendimiento/registro?error=ya_marcada", status_code=303)
    # crea registro jornada (ingresa PENDIENTE por defecto)
    reg = RegistroJornada(operario_id=user.id, fecha=date.today(), observaciones=observaciones, estado="PENDIENTE",
                          sede_id=getattr(user, "sede_id", None))
    db.add(reg)
    db.flush()
    total = Decimal("0")
    # referencia canónica a orden_produccion.id (además del legado garments.id)
    orden_prod_ref = None
    try:
        if garment:
            from app.models.produccion import OrdenProduccion as _OP
            _op = db.query(_OP).filter(_OP.orden_venta_id == garment.order_id).order_by(_OP.id.desc()).first()
            orden_prod_ref = _op.id if _op else None
    except Exception:
        orden_prod_ref = None
    for op_id, cant in ops:
        cat = catalogo_map[op_id]
        tarifa = cat.tarifa_base
        subtotal = (tarifa * Decimal(cant)).quantize(Decimal("0.01"))
        total += subtotal
        db.add(DetalleJornada(registro_jornada_id=reg.id, orden_produccion_id=gid, orden_id= garment.order_id if garment else None,
                              orden_produccion_ref_id=orden_prod_ref,
                              operacion_id=op_id, cantidad=cant, tarifa_aplicada=tarifa, subtotal=subtotal))
    try:
        db.commit()
    except Exception as e:
        # carrera entre dos empleados: el candado único decide quién marcó primero
        from sqlalchemy.exc import IntegrityError
        db.rollback()
        if isinstance(e, IntegrityError) or "uq_detalle_prenda_operacion" in str(e).lower() or "unique" in str(e).lower():
            return RedirectResponse("/rendimiento/registro?error=ya_marcada", status_code=303)
        raise
    # integración financiera opcional: exportar a Finanzas como MovimientoFinanciero pendiente
    # No auto-liquidado; queda REGISTRADO hasta aprobación
    return RedirectResponse(f"/rendimiento/registro?ok={reg.id}", status_code=303)

@router.get("/calculadora", response_class=HTMLResponse)
@_con_diagnostico("/rendimiento/calculadora")
def calculadora(request: Request, preset: str = Query("semana"), desde: str = Query(""), hasta: str = Query(""), operario: str = Query(""), orden: str = Query(""), db: Session = Depends(get_db), user=Depends(require_roles("ADMIN", "FINANZAS", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"))):
    _ensure_catalogo(db)
    # si el usuario ingresó ambas fechas, mandan sobre el preset
    if desde and hasta and preset != "personalizado":
        try:
            date.fromisoformat(desde); date.fromisoformat(hasta)
            preset = "personalizado"
        except ValueError:
            pass
    try:
        inicio, fin = _rango(preset, desde, hasta)
    except:
        inicio, fin = _rango("semana")
        preset = "semana"
    q = db.query(RegistroJornada).filter(RegistroJornada.fecha >= inicio, RegistroJornada.fecha <= fin)
    if operario:
        try:
            q = q.filter(RegistroJornada.operario_id == int(operario))
        except:
            pass
    # operario no admin solo ve lo propio? Por now muestra todo si admin else propio
    if user.role not in ("admin", "gerente", "contador") and user.role not in ("taller",):
        # limit to own?
        pass
    # filtro por orden en detalles
    if orden:
        try:
            oid = int(orden)
            # busca registros que tengan al menos un detalle con ese orden
            sub = db.query(DetalleJornada.registro_jornada_id).filter(DetalleJornada.orden_produccion_id==oid).distinct()
            q = q.filter(RegistroJornada.id.in_(sub))
        except:
            pass
    registros = q.order_by(RegistroJornada.fecha.desc()).all()
    detalles = []
    if registros:
        detalles = db.query(DetalleJornada).filter(DetalleJornada.registro_jornada_id.in_([r.id for r in registros])).all()
    # Suma en tiempo real: solo PENDIENTE por operario (legacy no
    # provisionado también cuenta una vez para compatibilidad).
    _PEND = ("PENDIENTE", "REGISTRADO", "APROBADO", "LIQUIDADO_INTERNO")
    pendientes = [r for r in registros if (r.estado or "") in _PEND]
    det_pend = [d for d in detalles if d.registro_jornada_id in {r.id for r in pendientes}]
    # métricas Decimal
    total_acumulado = sum((d.subtotal for d in det_pend), Decimal("0"))
    # prendas intervenidas distintas
    prendas_set = set(d.orden_produccion_id for d in detalles if d.orden_produccion_id)
    n_prendas = len(prendas_set)
    # horas/puntos: si tarifa es S/, puntos = total. Horas aprox = subtotal / tarifa_promedio? Simplifica: total como puntos
    catalogo = {c.id: c for c in db.query(CatalogoOperacion).all()}
    # desglose por operario (solo PENDIENTE)
    from app.models.user import User
    users = {u.id: u for u in db.query(User).filter(User.id.in_([r.operario_id for r in registros])).all()} if registros else {}
    por_operario: dict[int, dict] = {}
    for r in pendientes:
        dets = [d for d in det_pend if d.registro_jornada_id==r.id]
        sub = sum((d.subtotal for d in dets), Decimal("0"))
        agg = por_operario.setdefault(r.operario_id, {"user": users.get(r.operario_id), "total": Decimal("0"), "prendas": set(), "detalles": 0})
        agg["total"] += sub
        agg["prendas"].update(d.orden_produccion_id for d in dets if d.orden_produccion_id)
        agg["detalles"] += len(dets)
    por_operario_list = []
    for oid, agg in por_operario.items():
        por_operario_list.append({"operario": agg["user"].full_name if agg["user"] else f"usuario#{oid}", "operario_id": oid, "total": agg["total"], "prendas": len(agg["prendas"]), "registros": len([r for r in pendientes if r.operario_id==oid])})
    por_operario_list.sort(key=lambda x: x["total"], reverse=True)
    # por orden
    por_orden: dict[int, dict] = {}
    for d in detalles:
        if not d.orden_produccion_id:
            continue
        agg = por_orden.setdefault(d.orden_produccion_id, {"total": Decimal("0"), "ops": set()})
        agg["total"] += d.subtotal
        agg["ops"].add(d.operacion_id)
    # por actividad
    por_actividad: dict[int, dict] = {}
    for d in detalles:
        agg = por_actividad.setdefault(d.operacion_id, {"cat": catalogo.get(d.operacion_id), "total": Decimal("0"), "cantidad": 0})
        agg["total"] += d.subtotal
        agg["cantidad"] += d.cantidad
    # lista usuarios para filtro
    usuarios = db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name).all()
    # Perfil Personal (DNI/RUC) por operario: vínculo user_id o nombre.
    empleados_docs: dict[int, str] = {}
    try:
        from app.models.personnel import Empleado, ensure_empleados_table
        ensure_empleados_table(db)
        emps = db.query(Empleado).filter(Empleado.activo.is_(True)).all()
        por_user = {e.user_id: e for e in emps if e.user_id}
        por_nombre = {(e.nombres or "").strip().lower() + " " + (e.apellidos or "").strip().lower(): e for e in emps}
        for oid in {r.operario_id for r in pendientes}:
            e = por_user.get(oid)
            if not e:
                u = users.get(oid)
                if u and u.full_name:
                    e = por_nombre.get(u.full_name.strip().lower())
            if e and e.documento:
                empleados_docs[oid] = e.documento
    except Exception:
        pass
    # lista órdenes para filtro
    prendas_opt = db.query(Garment).order_by(Garment.id.desc()).limit(50).all()
    orders_map = {o.id: o for o in db.query(Order).all()}
    return templates.TemplateResponse(request, "rendimiento/calculadora_destajo.html", {
        "user": user, "tab": "calculadora", "preset": preset, "desde": desde, "hasta": hasta,
        "inicio": inicio, "fin": fin, "registros": registros, "detalles": detalles,
        "total_acumulado": total_acumulado, "n_prendas": n_prendas, "n_registros": len(registros),
        "por_operario": por_operario_list, "por_orden": por_orden, "por_actividad": por_actividad,
        "empleados_docs": empleados_docs,
        "operario": operario, "orden": orden, "usuarios": usuarios, "prendas_opt": prendas_opt, "orders_map": orders_map, "catalogo": catalogo})

@router.get("/calculadora/export")
def export_liquidacion(preset: str = Query("semana"), desde: str = Query(""), hasta: str = Query(""), operario: str = Query(""), db: Session = Depends(get_db), user=Auth):
    """Exporta el pendiente para Finanzas/Caja (CSV, sin marcas)."""
    if desde and hasta and preset != "personalizado":
        try:
            date.fromisoformat(desde); date.fromisoformat(hasta)
            preset = "personalizado"
        except ValueError:
            pass
    try:
        inicio, fin = _rango(preset, desde, hasta)
    except:
        inicio, fin = _rango("semana")
    q = db.query(RegistroJornada).filter(RegistroJornada.fecha >= inicio, RegistroJornada.fecha <= fin, RegistroJornada.estado.in_(["PENDIENTE","REGISTRADO","APROBADO","LIQUIDADO_INTERNO"]))
    if operario:
        try:
            q = q.filter(RegistroJornada.operario_id == int(operario))
        except:
            pass
    registros = q.all()
    # Solo exporta, no marca estados ni toca finanzas.
    import csv, io
    from fastapi.responses import StreamingResponse
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["registro_id","fecha","operario_id","orden_produccion_id","operacion","cantidad","tarifa","subtotal","estado"])
    catalogo = {c.id: c for c in db.query(CatalogoOperacion).all()}
    for r in registros:
        for d in db.query(DetalleJornada).filter(DetalleJornada.registro_jornada_id==r.id).all():
            cat = catalogo.get(d.operacion_id)
            w.writerow([r.id, r.fecha, r.operario_id, d.orden_produccion_id, cat.nombre_operacion if cat else d.operacion_id, d.cantidad, d.tarifa_aplicada, d.subtotal, r.estado])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=liquidacion_{inicio}_{fin}.csv"})

@router.post("/generar-rxh-cxp", response_class=HTMLResponse)
@router.post("/automatizar-gasto-rxh", response_class=HTMLResponse)
@router.post("/generar-rxh", response_class=HTMLResponse)
@router.post("/generar-cxh", response_class=HTMLResponse)
async def generar_rxh(request: Request, db: Session = Depends(get_db), user=FinanzasSM):
    """Botón único: provisión RxH/CxP por el PENDIENTE del operario.

    Crea Gasto HONORARIOS_RXH (6322/4241) + CxP POR_PAGAR con
    RECIBO_HONORARIOS y marca los registros PROVISIONADO.
    Nunca genera MovimientoFinanciero/Caja (el egreso nace solo al PAGAR).
    """
    from datetime import date as _date
    form = await request.form()

    def _int(name):
        try:
            return int(form.get(name))  # type: ignore
        except Exception:
            return None

    def _fecha(name):
        try:
            v = (form.get(name) or "").strip()  # type: ignore
            return _date.fromisoformat(v) if v else None
        except Exception:
            return None

    operario_id = _int("operario_id")
    if not operario_id:
        return HTMLResponse("operario_id es obligatorio", status_code=400)
    numero = str(form.get("numero_comprobante") or "").strip()
    ruc_dni = str(form.get("ruc_dni") or "").strip() or None
    con_ret = str(form.get("con_retencion") or "").lower() in ("1", "on", "true", "si")
    try:
        from app.services import rendimiento_rxh as rxh_svc
        rxh_svc.generar_provision_rxh(
            db, operario_id, numero,
            desde=_fecha("desde"), hasta=_fecha("hasta"),
            ruc_dni=ruc_dni, con_retencion=con_ret,
            usuario_id=user.id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    if "application/json" in (request.headers.get("accept") or ""):
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": True, "numero": numero})
    return RedirectResponse("/rendimiento/calculadora", status_code=303)

@router.get("/tarifario", response_class=HTMLResponse)
def tarifario_get(request: Request, db: Session = Depends(get_db), user=FinanzasSM):
    _ensure_catalogo(db)
    catalogo = db.query(CatalogoOperacion).order_by(CatalogoOperacion.id).all()
    return templates.TemplateResponse(request, "rendimiento/tarifario.html", {
        "user": user, "tab": "tarifario", "catalogo": catalogo})

@router.post("/tarifario", response_class=HTMLResponse)
def tarifario_post(codigo: str = Form(...), nombre_operacion: str = Form(...), tarifa_base: str = Form(...), db: Session = Depends(get_db), user=FinanzasSM):
    try:
        tarifa = Decimal(tarifa_base)
    except:
        return RedirectResponse("/rendimiento/tarifario", status_code=303)
    if db.query(CatalogoOperacion).filter(CatalogoOperacion.codigo==codigo).first():
        return RedirectResponse("/rendimiento/tarifario", status_code=303)
    db.add(CatalogoOperacion(codigo=codigo, nombre_operacion=nombre_operacion.strip(), tarifa_base=tarifa, activa=True))
    db.commit()
    return RedirectResponse("/rendimiento/tarifario", status_code=303)

@router.post("/tarifario/{cid}", response_class=HTMLResponse)
@router.put("/tarifario/{cid}", response_class=HTMLResponse)
async def tarifario_update(cid: int, request: Request, db: Session = Depends(get_db), user=FinanzasSM):
    cat = db.get(CatalogoOperacion, cid)
    if not cat:
        return HTMLResponse("Operación no encontrada", status_code=404)
    form = await request.form()
    if form.get("tarifa_base"):
        try:
            cat.tarifa_base = Decimal(str(form.get("tarifa_base")))
        except:
            pass
    if form.get("nombre_operacion"):
        cat.nombre_operacion = str(form.get("nombre_operacion")).strip()
    if "activa" in form:
        cat.activa = form.get("activa") in ("on","true","1",True)
    db.commit()
    return RedirectResponse("/rendimiento/tarifario", status_code=303)

@router.get("/tarifario/{cid}/toggle", response_class=HTMLResponse)
def tarifario_toggle(cid: int, db: Session = Depends(get_db), user=FinanzasSM):
    cat = db.get(CatalogoOperacion, cid)
    if cat:
        cat.activa = not cat.activa
        db.commit()
    return RedirectResponse("/rendimiento/tarifario", status_code=303)
