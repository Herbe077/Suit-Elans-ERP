"""Ámbito Producción & Confección: kanban 7 fases, fichas técnicas y medidas,
fit tests (1-3) y control de calidad, con automatizaciones entre módulos.

Unidad de producción = prenda (Garment). Sin pagos (módulo aislado).
Acceso: admin, taller y sastre (fichas también ventas).
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.constants import (
    FIT_ZONES_OTROS, FIT_ZONES_PANTALON, FIT_ZONES_SACO, KANBAN_STATES,
    QC_CHECKLIST, TALLER_KANBAN,
)
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.order import Garment, Operation, Order, WorkLog
from app.services import taller as svc


def _duenos_por_orden(db: Session, orden_ids: list[int]) -> dict[int, str]:
    """Nombre del dueño (cliente o empresa) por order_id."""
    from app.models.client import Client
    from app.models.company import Company
    duenos: dict[int, str] = {}
    if not orden_ids:
        return duenos
    ordenes = {o.id: o for o in db.query(Order).filter(Order.id.in_(orden_ids)).all()}
    cls = {c.id: c for c in db.query(Client).filter(
        Client.id.in_([o.client_id for o in ordenes.values() if o.client_id])).all()}
    comps = {c.id: c for c in db.query(Company).filter(
        Company.id.in_([o.company_id for o in ordenes.values() if o.company_id])).all()}
    for oid, o in ordenes.items():
        if o.client_id and o.client_id in cls:
            duenos[oid] = f"{cls[o.client_id].nombre} {cls[o.client_id].apellidos or ''}".strip()
        elif o.company_id and o.company_id in comps:
            duenos[oid] = comps[o.company_id].nombre_comercial
        else:
            duenos[oid] = "—"
    return duenos

router = APIRouter(prefix="/produccion", tags=["produccion"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Auth = Depends(require_roles("SASTRE-MAESTRO", "SASTRE-ASISTENTE"))  # ADMIN pasa siempre
AuthFichas = Depends(require_roles("SASTRE-MAESTRO", "SASTRE-ASISTENTE", "VENTA"))

UPLOAD_DIR = BASE_DIR / "app" / "static" / "uploads" / "fit"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _card(db: Session, g: Garment) -> dict:
    from app.models.client import Client
    from app.models.company import Company
    from app.models.inventory import Fabric
    o = db.get(Order, g.order_id)
    c = db.get(Client, o.client_id) if o and o.client_id else None
    e = db.get(Company, o.company_id) if o and o.company_id else None
    nombre = c.nombre_completo if c else (e.nombre_comercial if e else "—")
    cliente_full = f"{nombre} ({e.nombre_comercial})" if c and e else nombre
    tela = db.get(Fabric, g.tela_id) if g.tela_id else None
    dias = svc.dias_restantes(g, o)
    etiqueta, color = svc.urgencia(dias)
    col = svc.columna(g.estado_taller)
    idx = KANBAN_STATES.index(col)
    return {"g": g, "folio": o.folio if o else "?",
            "cliente": c.nombre_completo if c else "—",
            "cliente_full": cliente_full,
            "tela": tela, "dias": dias, "etiqueta": etiqueta, "color": color,
            "col": col, "pausada": g.estado_taller == "pausado",
            "prev": KANBAN_STATES[idx - 1] if idx > 0 else None,
            "next": KANBAN_STATES[idx + 1] if idx < len(KANBAN_STATES) - 1 else None}


# Solo taller real: VENTA_CONFIRMADA (confirmado) o COMPLETADA (entregado).
# Cotizaciones (cotizado) y cancelados JAMÁS generan tarjetas en el Kanban.
ESTADOS_TALLER_ORDEN = ("confirmado", "entregado")


def _board(db: Session) -> dict:
    # Oculta cotizaciones, pedidos entregados y cancelados del flujo.
    prendas = db.query(Garment).join(Order, Garment.order_id == Order.id).filter(
        Order.estado.in_(ESTADOS_TALLER_ORDEN),
        Garment.estado_taller != "entregado").order_by(Garment.id.desc()).limit(200).all()
    cols: dict[str, list] = {k: [] for k, _ in TALLER_KANBAN}
    for g in prendas:
        card = _card(db, g)
        cols[card["col"]].append(card)
    return cols


def _board_resp(request: Request, db: Session, user):
    return templates.TemplateResponse(request, "produccion/kanban.html", {
        "user": user, "tab": "kanban", "cols": _board(db), "kanban": TALLER_KANBAN},
        headers={"Cache-Control": "no-store"})


def _sync_op_estado(db: Session, garment: Garment):
    """Sincroniza estado de OrdenProduccion spec con Garment."""
    try:
        from app.models.produccion import OrdenProduccion as _OP
        ops = db.query(_OP).filter(_OP.orden_venta_id == garment.order_id).all()
        for op in ops:
            # si hay varios, actualiza el que tenga codigo_qr coincidente o el primero
            if op.codigo_qr == garment.codigo_qr or len(ops) == 1:
                op.estado = garment.estado_taller
                op.fecha_limite_entrega = garment.fecha_limite_entrega
        if ops:
            db.commit()
    except Exception:
        pass


def _destajo_prenda(db: Session, g, order) -> float:
    """Destajo acumulado de la prenda (cierres de jornada por referencia)."""
    try:
        from app.models.taller import TallerCierreJornada
        from app.services.taller_cierre import ref_producto
        from sqlalchemy import func as _func
        ref = ref_producto(order.folio if order else "?", g.tipo, g.id)
        total = db.query(_func.coalesce(_func.sum(
            TallerCierreJornada.total_pago), 0)).filter(
            TallerCierreJornada.producto_referencia == ref).scalar() or 0
        return round(float(total), 2)
    except Exception:
        return 0.0


@router.get("", response_class=HTMLResponse)
def kanban_root(request: Request, db: Session = Depends(get_db), user=Auth):
    return _board_resp(request, db, user)


@router.get("/kanban", response_class=HTMLResponse)
def kanban(request: Request, db: Session = Depends(get_db), user=Auth):
    return _board_resp(request, db, user)


@router.patch("/orden/{gid}/estado", response_class=HTMLResponse)
def mover_htmx(gid: int, request: Request, estado: str = Form(...),
               db: Session = Depends(get_db), user=Auth):
    """Mueve la prenda (HTMX) con validaciones de flujo; devuelve las
    columnas origen+destino con swap fuera de banda."""
    from app.models.order import Garment as _G
    g0 = db.get(_G, gid)
    col_src = svc.columna(g0.estado_taller) if g0 else None
    try:
        g = svc.mover(db, gid, estado, user.id)
        _sync_op_estado(db, g)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    col_dst = svc.columna(g.estado_taller)
    return templates.TemplateResponse(request, "produccion/_movecols.html", {
        "user": user, "cols": _board(db), "kanban": TALLER_KANBAN,
        "col_src": col_src, "col_dst": col_dst},
        headers={"Cache-Control": "no-store"})


@router.post("/orden/{gid}/estado", response_class=HTMLResponse)
@router.put("/orden/{gid}/estado", response_class=HTMLResponse)
def mover_post(gid: int, request: Request, estado: str = Form(...),
               db: Session = Depends(get_db), user=Auth):
    """Spec: /produccion/orden/<id>/estado (POST/PUT) — alias REST para
    mover la prenda. Usa las mismas validaciones: EN_PRUEBA→EN_CONFECCION
    exige Fit Test, ACABADOS exige paso por EN_CONFECCION, CALIDAD_OK vía
    checklist."""
    from app.models.order import Garment as _G
    g0 = db.get(_G, gid)
    col_src = svc.columna(g0.estado_taller) if g0 else None
    try:
        g = svc.mover(db, gid, estado, user.id)
        _sync_op_estado(db, g)
    except ValueError as e:
        # Soporta clientes JSON y HTMX/form
        if "application/json" in (request.headers.get("accept") or ""):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": str(e)}, status_code=400)
        return HTMLResponse(str(e), status_code=400)
    # Si es JSON prefieren JSON
    if "application/json" in (request.headers.get("accept") or ""):
        from fastapi.responses import JSONResponse
        return JSONResponse({"id": g.id, "estado": g.estado_taller,
                             "col": svc.columna(g.estado_taller)})
    # HTMX/form: si trae hx-request devuelve swap parcial, si no redirect
    if request.headers.get("hx-request"):
        col_dst = svc.columna(g.estado_taller)
        return templates.TemplateResponse(request, "produccion/_movecols.html", {
            "user": user, "cols": _board(db), "kanban": TALLER_KANBAN,
            "col_src": col_src, "col_dst": col_dst},
            headers={"Cache-Control": "no-store"})
    return RedirectResponse(f"/produccion/ficha/{gid}", status_code=303)


@router.get("/ficha/{gid}", response_class=HTMLResponse)
def ficha(gid: int, request: Request, ok: str = "",
          db: Session = Depends(get_db), user=Auth):
    from app.core.constants import (
        BOLSILLOS_OPTS, FORROS_OPTS, MEASURE_FIELDS_PANTALON,
        MEASURE_FIELDS_SACO, RESPIRADEROS_OPTS, SOLAPAS,
    )
    from app.models.user import User
    try:
        d = svc.ficha_data(db, gid)
    except ValueError:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    qc_por = None
    if d["qc"] and d["qc"].aprobado_por:
        u = db.get(User, d["qc"].aprobado_por)
        qc_por = u.full_name if u else f"usuario #{d['qc'].aprobado_por}"
    tipo = d["g"].tipo
    if tipo == "pantalon":
        zonas = FIT_ZONES_PANTALON
    elif tipo in ("saco", "chaleco", "abrigo", "smoking"):
        zonas = FIT_ZONES_SACO
    else:
        zonas = FIT_ZONES_OTROS
    return templates.TemplateResponse(request, "produccion/ficha_tecnica.html", {
        "user": user, **d, "ok": ok, "dias": d["dias"],
        "etiqueta": svc.urgencia(d["dias"])[0], "ucolor": svc.urgencia(d["dias"])[1],
        "diseno": d["g"].diseno or {}, "solapas": SOLAPAS, "bolsillos": BOLSILLOS_OPTS,
        "forros": FORROS_OPTS, "respiraderos": RESPIRADEROS_OPTS,
        "med_saco": MEASURE_FIELDS_SACO, "med_pantalon": MEASURE_FIELDS_PANTALON,
        "qc_por": qc_por, "zonas_prueba": zonas,
        "fases": KANBAN_STATES, "fase_actual": svc.columna(d["g"].estado_taller),
        "destajo_total": _destajo_prenda(db, d["g"], d["o"])})


@router.post("/ficha/{gid}", response_class=HTMLResponse)
async def actualizar_ficha(gid: int, request: Request,
                           db: Session = Depends(get_db), user=Auth):
    """Spec: /produccion/ficha/<id> (POST) — actualiza Ficha Técnica y
    Medidas Anatómicas (spec FichaMedidas: saco_medidas, pantalon_medidas,
    chaleco_medidas JSON + observaciones_anatomicas). También soporta
    campos de diseño y fecha límite para compatibilidad."""
    from datetime import date as date_cls
    import json as _json
    form = await request.form()
    g = db.get(Garment, gid)
    if not g:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    # Diseño (si viene)
    if any(k in form for k in ("solapa", "bolsillos", "forro", "respiraderos", "botones", "notas_diseno")):
        g.diseno = {k: (form.get(k) or "") for k in
                    ("solapa", "bolsillos", "forro", "respiraderos", "botones", "notas_diseno")}
    if form.get("fecha_limite"):
        try:
            g.fecha_limite_entrega = date_cls.fromisoformat(str(form.get("fecha_limite")))
        except ValueError:
            pass
    if form.get("fecha_limite_entrega"):
        try:
            g.fecha_limite_entrega = date_cls.fromisoformat(str(form.get("fecha_limite_entrega")))
        except ValueError:
            pass
    # Medidas anatómicas → FichaMedidas spec (JSON)
    saco_keys = [k for k, _ in __import__("app.core.constants", fromlist=["MEASURE_FIELDS_SACO"]).MEASURE_FIELDS_SACO]
    pant_keys = [k for k, _ in __import__("app.core.constants", fromlist=["MEASURE_FIELDS_PANTALON"]).MEASURE_FIELDS_PANTALON]
    saco_medidas = {}
    pantalon_medidas = {}
    chaleco_medidas = {}
    has_medidas = False
    for k in saco_keys:
        if k in form and str(form.get(k) or "").strip() != "":
            try:
                saco_medidas[k] = float(form.get(k))  # type: ignore
                has_medidas = True
            except ValueError:
                saco_medidas[k] = form.get(k)
                has_medidas = True
    for k in pant_keys:
        if k in form and str(form.get(k) or "").strip() != "":
            try:
                pantalon_medidas[k] = float(form.get(k))  # type: ignore
                has_medidas = True
            except ValueError:
                pantalon_medidas[k] = form.get(k)
                has_medidas = True
    # Soporta JSON directos del spec
    for field in ("saco_medidas", "pantalon_medidas", "chaleco_medidas"):
        if form.get(field):
            try:
                parsed = _json.loads(str(form.get(field)))
                if isinstance(parsed, dict):
                    if field == "saco_medidas":
                        saco_medidas.update(parsed)
                    elif field == "pantalon_medidas":
                        pantalon_medidas.update(parsed)
                    else:
                        chaleco_medidas.update(parsed)
                    has_medidas = True
            except Exception:
                pass
    obs = form.get("observaciones_anatomicas") or form.get("observaciones") or form.get("postura")
    if has_medidas or obs:
        from app.models.order import Order as _Order
        from app.models.produccion import FichaMedidas
        o = db.get(_Order, g.order_id)
        cliente_id = o.client_id if o and o.client_id else None
        if cliente_id:
            fm = db.query(FichaMedidas).filter(FichaMedidas.garment_id == gid).first()
            if not fm:
                fm = FichaMedidas(cliente_id=cliente_id, garment_id=gid,
                                  orden_produccion_id=None)
                db.add(fm)
            if saco_medidas:
                fm.saco_medidas = saco_medidas
            if pantalon_medidas:
                fm.pantalon_medidas = pantalon_medidas
            if chaleco_medidas:
                fm.chaleco_medidas = chaleco_medidas
            if obs is not None:
                fm.observaciones_anatomicas = str(obs)
    db.commit()
    # JSON clients
    if "application/json" in (request.headers.get("accept") or ""):
        from fastapi.responses import JSONResponse
        return JSONResponse({"id": g.id, "ok": True})
    return RedirectResponse(f"/produccion/ficha/{gid}?ok=ficha", status_code=303)


@router.post("/ficha/{gid}/medidas")
async def guardar_medidas_en_ficha(gid: int, request: Request,
                                   db: Session = Depends(get_db), user=Auth):
    """Toma de medidas sin abandonar el expediente de la prenda.

    Crea una nueva versión de Measurement para el cliente del pedido y la
    vincula a la prenda. Redirige de vuelta a la ficha.
    """
    from app.core.constants import ALL_MEASURE_FIELDS
    from app.models.measurement import Measurement
    g = db.get(Garment, gid)
    if not g:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    o = db.get(Order, g.order_id)
    if not o or not o.client_id:
        return RedirectResponse(f"/produccion/ficha/{gid}", status_code=303)
    form = await request.form()
    data = {k: (float(form[k]) if form.get(k) not in (None, "") else None)
            for k in ALL_MEASURE_FIELDS}
    m = Measurement(client_id=o.client_id, sastre_id=user.id,
                    tipo_prenda=form.get("tipo_prenda", "saco"),
                    postura=_componer_postura(form), observaciones=form.get("observaciones"), **data)
    m.version = db.query(Measurement).filter(
        Measurement.client_id == o.client_id).count() + 1
    db.add(m)
    db.flush()
    g.measurement_id = m.id
    db.commit()
    return RedirectResponse(f"/produccion/ficha/{gid}?ok=ficha", status_code=303)


@router.post("/prenda/{gid}/diseno")
async def guardar_diseno(gid: int, request: Request,
                         db: Session = Depends(get_db), user=Auth):
    from datetime import date as date_cls
    form = await request.form()
    g = db.get(Garment, gid)
    if not g:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    g.diseno = {k: (form.get(k) or "") for k in
                ("solapa", "bolsillos", "forro", "respiraderos", "botones", "notas_diseno")}
    if form.get("fecha_limite"):
        try:
            g.fecha_limite_entrega = date_cls.fromisoformat(form.get("fecha_limite"))
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(f"/produccion/ficha/{gid}", status_code=303)


@router.post("/prenda/{gid}/asignar")
def asignar(gid: int, artesano_id: str = Form(""),
            db: Session = Depends(get_db), user=Auth):
    g = db.get(Garment, gid)
    if not g:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    g.artesano_id = int(artesano_id) if artesano_id else None
    db.commit()
    return RedirectResponse(f"/produccion/ficha/{gid}", status_code=303)


# ---------- Fichas técnicas & medidas (clientes) ----------
@router.get("/fichas", response_class=HTMLResponse)
def fichas(request: Request, q: str = "", db: Session = Depends(get_db), user=AuthFichas):
    from app.models.client import Client
    query = db.query(Client)
    if q:
        like = f"%{q}%"
        query = query.filter((Client.nombre.like(like)) | (Client.apellidos.like(like))
                             | (Client.telefono.like(like)) | (Client.nro_doc.like(like)))
    return templates.TemplateResponse(request, "taller/fichas.html", {
        "user": user, "tab": "fichas", "clientes": query.order_by(Client.apellidos).limit(100).all(), "q": q})


@router.post("/fichas")
def crear_ficha(nombre: str = Form(...), apellidos: str = Form(...),
                telefono: str = Form(""), email: str = Form(""),
                tipo_doc: str = Form("DNI"), nro_doc: str = Form(""),
                distrito: str = Form(""),
                db: Session = Depends(get_db), user=AuthFichas):
    from app.models.client import Client
    from app.services import peru as peru_svc
    doc = (nro_doc or "").strip() or None
    if tipo_doc == "RUC":
        doc = peru_svc.normalizar_ruc(doc)
    else:
        try:
            peru_svc.validar_doc(tipo_doc, doc)
        except ValueError:
            return RedirectResponse("/produccion/fichas", status_code=303)
    c = Client(nombre=nombre.strip(), apellidos=apellidos.strip(),
               telefono=telefono or None, email=email or None,
               tipo_doc=tipo_doc, nro_doc=doc,
               distrito=distrito.strip() or None)
    db.add(c)
    db.commit()
    return RedirectResponse(f"/produccion/fichas/{c.id}", status_code=303)


@router.get("/fichas/{cid}", response_class=HTMLResponse)
def ficha_detalle(cid: int, request: Request, error: str = "",
                  db: Session = Depends(get_db), user=AuthFichas):
    from app.core.constants import ALL_MEASURE_FIELDS
    from app.models.client import Client
    from app.models.measurement import Measurement
    c = db.get(Client, cid)
    medidas = db.query(Measurement).filter(Measurement.client_id == cid).order_by(
        Measurement.id.desc()).all()
    bloqueos = _bloqueos(db, cid) if error == "movimientos" else []
    # Historial con deltas: cambios de cada versión respecto a la anterior.
    asc = list(reversed(medidas))
    deltas: dict[int, dict] = {}
    prev = None
    for m in asc:
        cambios: dict[str, tuple] = {}
        if prev is not None:
            for k in ALL_MEASURE_FIELDS:
                a, b = getattr(prev, k, None), getattr(m, k, None)
                if a != b and not (a is None and b is None):
                    cambios[k] = (a, b)
            if (prev.postura or "") != (m.postura or ""):
                cambios["postura"] = (prev.postura, m.postura)
        deltas[m.id] = cambios
        prev = m
    return templates.TemplateResponse(request, "taller/ficha_detalle.html", {
        "user": user, "c": c, "medidas": medidas, "error": error,
        "bloqueos": bloqueos, "deltas": deltas,
        "ultima": medidas[0] if medidas else None,
        "postura_opts": {
            "hombro": ["Normal", "Caído izquierdo", "Caído derecho",
                       "Alto izquierdo", "Alto derecho"],
            "espalda": ["Normal", "Encorvada", "Erguida"],
            "abdomen": ["Normal", "Prominente", "Plano"],
        }})


def _bloqueos(db: Session, cid: int) -> list[str]:
    from app.models.billing import Invoice
    from app.models.crm import Quotation
    from app.models.order import Payment
    out: list[str] = []
    for o in db.query(Order).filter(Order.client_id == cid).all():
        if o.estado != "cancelado":
            out.append(f"Pedido {o.folio} ({o.estado}, saldo S/ {o.saldo:.2f})")
        elif db.query(Payment).filter(Payment.order_id == o.id).count():
            out.append(f"Pedido {o.folio} anulado pero con pagos registrados")
    for q in db.query(Quotation).filter(Quotation.client_id == cid).all():
        if q.estado in ("borrador", "enviada", "aprobada"):
            out.append(f"Cotización {q.folio} ({q.estado})")
    for inv in db.query(Invoice).filter(Invoice.client_id == cid).all():
        if inv.estado == "emitida":
            out.append(f"Comprobante {inv.folio} (emitido, S/ {inv.total:.2f})")
    return out


def _componer_postura(form) -> str | None:
    """Une los selectores anatómicos en el campo postura.

    Formato: "Hombro: X · Espalda: Y · Abdomen: Z" (solo partes no vacías
    ni "Normal" solitarias). Retrocompatible: si viene `postura` libre y
    ningún selector, se usa tal cual.
    """
    partes = []
    for clave, etiqueta in (("postura_hombro", "Hombro"),
                            ("postura_espalda", "Espalda"),
                            ("postura_abdomen", "Abdomen")):
        v = (form.get(clave) or "").strip()
        if v:
            partes.append(f"{etiqueta}: {v}")
    if partes:
        # Si todo es "Normal", guarda "Normal" compacto.
        if all(p.endswith(": Normal") for p in partes):
            return "Normal"
        return " · ".join(partes)
    libre = (form.get("postura") or "").strip()
    return libre or None


@router.post("/fichas/{cid}/medidas")
async def guardar_medidas(cid: int, request: Request,
                           db: Session = Depends(get_db), user=AuthFichas):
    from app.core.constants import ALL_MEASURE_FIELDS
    from app.models.measurement import Measurement
    form = await request.form()
    data = {k: (float(form[k]) if form.get(k) not in (None, "") else None)
            for k in ALL_MEASURE_FIELDS}
    m = Measurement(client_id=cid, sastre_id=user.id,
                    tipo_prenda=form.get("tipo_prenda", "saco"),
                    postura=_componer_postura(form), observaciones=form.get("observaciones"), **data)
    m.version = db.query(Measurement).filter(Measurement.client_id == cid).count() + 1
    db.add(m)
    db.commit()
    return RedirectResponse(f"/produccion/fichas/{cid}", status_code=303)


@router.post("/fichas/{cid}/citas")
def agendar_cita(cid: int, tipo: str = Form("PRIMERA_PRUEBA"),
                 inicio: str = Form(...), fin: str = Form(...),
                 db: Session = Depends(get_db), user=AuthFichas):
    from datetime import datetime as dt_cls
    from app.models.appointment import Appointment
    if tipo not in ("TOMA_MEDIDAS", "PRIMERA_PRUEBA", "SEGUNDA_PRUEBA",
                    "ENTREGA_FINAL", "AJUSTE"):
        tipo = "PRIMERA_PRUEBA"
    db.add(Appointment(client_id=cid, tipo=tipo, sastre_id=user.id,
                       inicio=dt_cls.fromisoformat(inicio), fin=dt_cls.fromisoformat(fin)))
    db.commit()
    return RedirectResponse(f"/produccion/fichas/{cid}", status_code=303)


@router.post("/fichas/{cid}/pedidos/{oid}/cancelar")
def cancelar_pedido(cid: int, oid: int, db: Session = Depends(get_db),
                    user=Depends(require_roles("ADMIN"))):
    from app.models.order import Payment
    o = db.get(Order, oid)
    if o and o.client_id == cid:
        if db.query(Payment).filter(Payment.order_id == oid).count() or o.anticipo > 0:
            return RedirectResponse(f"/produccion/fichas/{cid}?error=con_pagos",
                                    status_code=303)
        o.estado = "cancelado"
        db.commit()
    return RedirectResponse(f"/produccion/fichas/{cid}", status_code=303)


@router.post("/fichas/{cid}/eliminar")
def eliminar_cliente(cid: int, db: Session = Depends(get_db),
                     user=Depends(require_roles("ADMIN"))):
    from app.models.appointment import Appointment
    from app.models.billing import Invoice
    from app.models.client import Client
    from app.models.crm import Quotation, QuotationLine
    from app.models.measurement import Measurement
    from app.models.order import ControlCalidad, Garment, PruebaEntalle, WorkLog
    c = db.get(Client, cid)
    if not c:
        return RedirectResponse("/produccion/fichas", status_code=303)
    if _bloqueos(db, cid):
        return RedirectResponse(f"/produccion/fichas/{cid}?error=movimientos",
                                status_code=303)
    for o in db.query(Order).filter(Order.client_id == cid).all():
        for g in db.query(Garment).filter(Garment.order_id == o.id).all():
            db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == g.id).delete()
            db.query(ControlCalidad).filter(ControlCalidad.garment_id == g.id).delete()
            db.query(WorkLog).filter(WorkLog.garment_id == g.id).delete()
            db.delete(g)
        db.delete(o)
    for q in db.query(Quotation).filter(Quotation.client_id == cid).all():
        db.query(QuotationLine).filter(QuotationLine.quotation_id == q.id).delete()
        db.delete(q)
    db.query(Invoice).filter(Invoice.client_id == cid).delete()
    db.query(Measurement).filter(Measurement.client_id == cid).delete()
    db.query(Appointment).filter(Appointment.client_id == cid).delete()
    db.delete(c)
    db.commit()
    return RedirectResponse("/produccion/fichas?ok=eliminada", status_code=303)


def _zonas(tipo: str):
    if tipo == "pantalon":
        return FIT_ZONES_PANTALON
    if tipo in ("saco", "chaleco", "abrigo", "smoking"):
        return FIT_ZONES_SACO
    return FIT_ZONES_OTROS


@router.get("/pruebas", response_class=HTMLResponse)
def pruebas(request: Request, db: Session = Depends(get_db), user=Auth):
    from app.models.order import PruebaEntalle
    candidatas = db.query(Garment).join(Order, Garment.order_id == Order.id).filter(
        Order.estado.in_(ESTADOS_TALLER_ORDEN),
        Garment.estado_taller.in_(["ARMADO_HILVAN", "EN_PRUEBA"])).order_by(
        Garment.id.desc()).limit(80).all()
    # Solo pendientes/programadas: excluye prendas cuya última prueba ya
    # está completada/aprobada (completada == True).
    pendientes: list[Garment] = []
    for g in candidatas:
        ultima = db.query(PruebaEntalle).filter(
            PruebaEntalle.garment_id == g.id).order_by(
            PruebaEntalle.id.desc()).first()
        if ultima is not None and bool(getattr(ultima, "completada", False)):
            continue
        pendientes.append(g)
    items = [{"g": g, "folio": (db.get(Order, g.order_id).folio
                                if db.get(Order, g.order_id) else "?"),
              "zonas": _zonas(g.tipo)} for g in pendientes]
    duenos = _duenos_por_orden(db, [g.order_id for g in candidatas])
    for it in items:
        it["dueno"] = duenos.get(it["g"].order_id, "—")
    return templates.TemplateResponse(request, "produccion/pruebas.html", {
        "user": user, "tab": "pruebas", "items": items})


@router.post("/prueba-entalle")
@router.post("/pruebas/registrar")
async def registrar_prueba(request: Request, garment_id: int = Form(None),  # type: ignore
                           numero_prueba: int = Form(1),
                           observaciones_ajuste: str = Form(""),
                           notas_sastre: str = Form(""),
                           db: Session = Depends(get_db), user=Auth):
    import json as _json
    form = await request.form()
    # Compat spec: orden_produccion_id vs garment_id, ajustes_json vs zona_*
    if garment_id is None:
        raw = form.get("orden_produccion_id") or form.get("garment_id") or form.get("orden_id")
        try:
            garment_id = int(raw)  # type: ignore
        except Exception:
            return HTMLResponse("Prenda no encontrada", status_code=404)
    # si numero_prueba no vino como int, intenta parse
    try:
        numero_prueba = int(numero_prueba)
    except Exception:
        numero_prueba = 1
    if not observaciones_ajuste:
        observaciones_ajuste = str(form.get("observaciones_ajuste") or form.get("ajustes") or "")
    if not notas_sastre:
        notas_sastre = str(form.get("notas_sastre") or "")
    g = db.get(Garment, garment_id)
    if not g:
        # Intenta resolver via OrdenProduccion spec id
        try:
            from app.models.produccion import OrdenProduccion as _OP
            op = db.get(_OP, garment_id)
            if op and op.orden_venta_id:
                # busca garment asociado
                cand = db.query(Garment).filter(Garment.order_id == op.orden_venta_id).first()
                if cand:
                    g = cand
                    garment_id = cand.id
                else:
                    return HTMLResponse("Prenda no encontrada", status_code=404)
            else:
                return HTMLResponse("Prenda no encontrada", status_code=404)
        except Exception:
            return HTMLResponse("Prenda no encontrada", status_code=404)
    correcciones = {k[5:]: (form.get(k) or "").strip() for k in form.keys()
                    if k.startswith("zona_") and (form.get(k) or "").strip()}
    # ajustes_json del spec (JSON dict)
    if not correcciones and form.get("ajustes_json"):
        try:
            parsed = _json.loads(str(form.get("ajustes_json")))
            if isinstance(parsed, dict):
                correcciones = {str(k): str(v) for k, v in parsed.items() if str(v).strip()}
        except Exception:
            pass
    if not correcciones and form.get("ajustes"):
        try:
            parsed = _json.loads(str(form.get("ajustes")))
            if isinstance(parsed, dict):
                correcciones = {str(k): str(v) for k, v in parsed.items() if str(v).strip()}
        except Exception:
            pass
    # fotos_url del spec (lista) si no hay uploads
    fotos = []
    raw_files = form.getlist("fotos") or ([form.get("foto")] if form.get("foto") else [])
    for foto in raw_files:
        if not foto or not getattr(foto, "filename", None):
            continue
        ext = Path(foto.filename).suffix.lower()
        if ext not in FOTO_EXTS or len(fotos) >= 3:
            continue
        raw = await foto.read(5 * 1024 * 1024 + 1)
        if len(raw) <= 5 * 1024 * 1024:
            nombre = f"fit_{garment_id}_{uuid.uuid4().hex[:8]}{ext}"
            (UPLOAD_DIR / nombre).write_bytes(raw)
            fotos.append(f"uploads/fit/{nombre}")
    # fotos_url del spec (JSON array) fallback
    if not fotos and form.get("fotos_url"):
        try:
            parsed = _json.loads(str(form.get("fotos_url")))
            if isinstance(parsed, list):
                fotos = [str(x) for x in parsed[:3]]
        except Exception:
            pass
    try:
        p = svc.registrar_prueba(db, garment_id, numero_prueba, correcciones,
                             observaciones_ajuste, notas_sastre, fotos)
        # Mirror a spec tables para evaluación
        try:
            from app.models.produccion import PruebaEntalle as _ProdPrueba
            from app.models.order import Order as _Order
            g2 = db.get(Garment, garment_id)
            o2 = db.get(_Order, g2.order_id) if g2 else None
            op_id = None
            if o2:
                from app.models.produccion import OrdenProduccion as _OP
                cand = db.query(_OP).filter(_OP.orden_venta_id == o2.id).first()
                op_id = cand.id if cand else None
            db.add(_ProdPrueba(orden_produccion_id=op_id, garment_id=garment_id,
                               numero_prueba=numero_prueba, notas_sastre=notas_sastre,
                               ajustes_json=correcciones or None, fotos_url=fotos or None))
            db.commit()
        except Exception:
            pass
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    # Tras confirmar la prueba vuelve a Producción general (no a la ficha).
    if request.headers.get("hx-request"):
        return HTMLResponse("", status_code=200,
                            headers={"HX-Redirect": "/produccion"})
    return RedirectResponse("/produccion", status_code=303)


@router.post("/pruebas/{pid}/confirmar", response_class=HTMLResponse)
def confirmar_prueba_endpoint(pid: int, request: Request,
                              db: Session = Depends(get_db), user=Auth):
    """Confirma una prueba pendiente: la marca completada y avanza la prenda
    a EN_CONFECCION. Redirige a Producción general."""
    try:
        svc.confirmar_prueba(db, pid)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    if request.headers.get("hx-request"):
        return HTMLResponse("", status_code=200,
                            headers={"HX-Redirect": "/produccion"})
    return RedirectResponse("/produccion", status_code=303)


@router.get("/calidad", response_class=HTMLResponse)
def calidad(request: Request, error: str = "", db: Session = Depends(get_db), user=Auth):
    candidatas = db.query(Garment).join(Order, Garment.order_id == Order.id).filter(
        Order.estado.in_(ESTADOS_TALLER_ORDEN),
        Garment.estado_taller == "ACABADOS").order_by(Garment.id.desc()).limit(80).all()
    items = [{"g": g, "folio": (db.get(Order, g.order_id).folio
                                if db.get(Order, g.order_id) else "?")} for g in candidatas]
    duenos = _duenos_por_orden(db, [g.order_id for g in candidatas])
    for it in items:
        it["dueno"] = duenos.get(it["g"].order_id, "—")
    return templates.TemplateResponse(request, "produccion/control_calidad.html", {
        "user": user, "tab": "calidad", "items": items, "qc": QC_CHECKLIST, "error": error})


@router.get("/calidad/{gid}", response_class=HTMLResponse)
def calidad_una(gid: int, request: Request, error: str = "",
                db: Session = Depends(get_db), user=Auth):
    g = db.get(Garment, gid)
    if not g:
        return HTMLResponse("Prenda no encontrada", status_code=404)
    o = db.get(Order, g.order_id)
    return templates.TemplateResponse(request, "produccion/control_calidad.html", {
        "user": user, "tab": "calidad",
        "items": [{"g": g, "folio": o.folio if o else "?",
                   "dueno": _duenos_por_orden(db, [g.order_id]).get(g.order_id, "—")}],
        "qc": QC_CHECKLIST, "error": error})


@router.post("/orden/{gid}/control-calidad")
@router.post("/calidad/{gid}")
async def aprobar(gid: int, request: Request, observaciones: str = Form(""),
                  db: Session = Depends(get_db), user=Auth):
    """Spec: /produccion/calidad/<id> (POST) — checklist 6 puntos. Alias
    legacy /orden/{id}/control-calidad se mantiene."""
    form = await request.form()
    # Soporta checklist_json del spec
    import json as _json
    checks = None
    if form.get("checklist_json"):
        try:
            parsed = _json.loads(str(form.get("checklist_json")))
            if isinstance(parsed, dict):
                checks = [bool(parsed.get(k)) for k in QC_CHECKLIST]
                # Si dict tiene otros keys, mapeo alternativo
                if len(checks) != len(QC_CHECKLIST):
                    checks = [bool(parsed.get(str(i))) for i in range(len(QC_CHECKLIST))]
            elif isinstance(parsed, list):
                checks = [bool(v) for v in parsed]
        except Exception:
            pass
    if checks is None:
        checks = [f"check_{i}" in form for i in range(len(QC_CHECKLIST))]
    try:
        cc = svc.aprobar_calidad(db, gid, checks, observaciones, user.id)
        # sync spec OrdenProduccion + mirror ControlCalidad spec
        try:
            g = db.get(Garment, gid)
            if g:
                _sync_op_estado(db, g)
                from app.models.produccion import ControlCalidad as _ProdCC
                from app.models.produccion import OrdenProduccion as _OP
                o = db.get(Order, g.order_id)
                op_id = None
                if o:
                    cand = db.query(_OP).filter(_OP.orden_venta_id == o.id).first()
                    op_id = cand.id if cand else None
                db.add(_ProdCC(orden_produccion_id=op_id, garment_id=gid,
                               checklist_json=cc.checks if cc else {k: True for k in QC_CHECKLIST},
                               aprobado=True, revisado_por_id=user.id))
                db.commit()
        except Exception:
            pass
    except ValueError:
        if "application/json" in (request.headers.get("accept") or ""):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Los 6 puntos del checklist son obligatorios"}, status_code=400)
        return RedirectResponse("/produccion/calidad?error=1", status_code=303)
    if "application/json" in (request.headers.get("accept") or ""):
        from fastapi.responses import JSONResponse
        return JSONResponse({"id": gid, "estado": "CALIDAD_OK"})
    if request.headers.get("hx-request"):
        return HTMLResponse("", status_code=200,
                            headers={"HX-Redirect": "/produccion"})
    return RedirectResponse("/produccion", status_code=303)


# --- Compatibilidad: fichaje SAM ---
@router.post("/prendas/{gid}/estado")
def cambiar_estado(gid: int, estado_taller: str = Form(...),
                   db: Session = Depends(get_db), user=Auth):
    try:
        svc.mover(db, gid, estado_taller, user.id)
    except ValueError:
        pass
    return RedirectResponse("/produccion/kanban", status_code=303)


# ---------- OP de colección / stock comercial: finalización ----------
@router.post("/op/{op_id}/finalizar")
async def finalizar_op(op_id: int, request: Request,
                       db: Session = Depends(get_db), user=Auth):
    """Finaliza una OP de colección (COMPLETADA/FINALIZADA).

    Descuenta insumos según BOM, costea (insumos CPP + destajo MOD), actualiza
    costo/stock del SKU terminado y registra Kardex + asientos — todo en UN
    commit. Acepta JSON o form:
      variant_id, cantidad, estado_final (COMPLETADA|FINALIZADA),
      mano_obra_directa (opcional), receta_id (opcional),
      trabajador_id (opcional; default sastre de la OP),
      insumos_json (opcional: [{producto_insumo_id, cantidad_por_unidad}]).
    Sin BOM explícita ni receta_id usa la receta activa del producto.
    """
    import json as _json
    from fastapi.responses import JSONResponse
    ctype = (request.headers.get("content-type") or "")
    if "application/json" in ctype:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "JSON inválido"}, status_code=400)
        variant_id = payload.get("variant_id")
        cantidad = payload.get("cantidad")
        estado_final = payload.get("estado_final", "COMPLETADA")
        mano_obra = payload.get("mano_obra_directa")
        receta_id = payload.get("receta_id")
        try:
            trabajador_id = int(payload.get("trabajador_id")) \
                if payload.get("trabajador_id") not in (None, "") else None
        except (TypeError, ValueError):
            return JSONResponse({"error": "trabajador_id inválido"}, status_code=400)
        insumos = payload.get("insumos")
    else:
        form = await request.form()
        try:
            variant_id = int(form.get("variant_id") or 0)
        except (TypeError, ValueError):
            variant_id = 0
        try:
            cantidad = float(form.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0
        estado_final = str(form.get("estado_final") or "COMPLETADA")
        try:
            mano_obra = float(form.get("mano_obra_directa")) \
                if str(form.get("mano_obra_directa") or "").strip() != "" else None
        except (TypeError, ValueError):
            return JSONResponse({"error": "mano_obra_directa inválida"}, status_code=400)
        try:
            receta_id = int(form.get("receta_id")) \
                if str(form.get("receta_id") or "").strip() != "" else None
        except (TypeError, ValueError):
            return JSONResponse({"error": "receta_id inválida"}, status_code=400)
        try:
            trabajador_id = int(form.get("trabajador_id")) \
                if str(form.get("trabajador_id") or "").strip() != "" else None
        except (TypeError, ValueError):
            return JSONResponse({"error": "trabajador_id inválido"}, status_code=400)
        insumos = None
        raw_insumos = form.get("insumos_json") or form.get("insumos")
        if raw_insumos:
            try:
                insumos = _json.loads(str(raw_insumos))
            except Exception:
                return JSONResponse({"error": "insumos_json inválido"}, status_code=400)
    try:
        from app.services import produccion_coleccion as _opc
        res = _opc.finalizar_op_coleccion(
            db, op_id, int(variant_id or 0), float(cantidad or 0),
            insumos=insumos, receta_id=receta_id,
            mano_obra_directa=mano_obra, estado_final=estado_final,
            usuario_id=user.id, trabajador_id=trabajador_id)
    except (TypeError, ValueError) as e:
        if "application/json" in (request.headers.get("accept") or "") \
                or "application/json" in ctype:
            return JSONResponse({"error": str(e)}, status_code=400)
        return HTMLResponse(str(e), status_code=400)
    if "application/json" in (request.headers.get("accept") or "") \
            or "application/json" in ctype:
        return JSONResponse(res)
    return RedirectResponse("/inventario/almacen?sub=pt", status_code=303)
