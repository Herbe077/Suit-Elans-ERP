"""Ámbito Comercial & CRM: clientes 360°, agenda y pipeline de oportunidades."""
import calendar as calmod
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.constants import (
    APPOINTMENT_STATUS, APPOINTMENT_TYPES, CLASIFICACION_CLIENTE, GARMENT_TYPES,
    INTERACCION_TIPOS, LEAD_OPEN, LEAD_ORIGINS, LEAD_STATUS,
)
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.appointment import Appointment
from app.models.catalog import ProductVariant
from app.models.client import Client
from app.models.company import Company, Contact
from app.models.crm import Interaccion, Lead, Quotation, QuotationLine
from app.models.order import Garment, Order
from app.services import crm as crm_svc
from app.services import peru as peru_svc

router = APIRouter(prefix="/comercial", tags=["comercial"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Ventas = Depends(require_roles("VENTA", "SASTRE-MAESTRO"))
VentasSastre = Depends(require_roles("VENTA", "SASTRE-MAESTRO", "SASTRE-ASISTENTE"))


def _ctx(base: dict, tab: str) -> dict:
    base["tab"] = tab
    return base


# ---------- Clientes unificado ----------
@router.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", tab: str = "personas",
             tipo: str = "", clasificacion: str = "", error: str = "",
             warn: str = "",
             db: Session = Depends(get_db), user=Ventas):
    personas = db.query(Client)
    empresas = db.query(Company)
    if q:
        like = f"%{q}%"
        personas = personas.filter(or_(Client.nombre.like(like), Client.apellidos.like(like),
                                       Client.telefono.like(like), Client.nro_doc.like(like)))
        empresas = empresas.filter(or_(Company.nombre_comercial.like(like),
                                       Company.ruc.like(like),
                                       Company.telefono.like(like)))
    if tab == "empresas" and clasificacion:
        empresas = empresas.filter(Company.clasificacion == clasificacion)
    if tab != "empresas" and clasificacion:
        personas = personas.filter(Client.clasificacion == clasificacion)
    return templates.TemplateResponse(request, "comercial/clientes.html", _ctx({
        "user": user, "q": q, "clasificacion": clasificacion, "error": error,
        "warn": warn,
        "clasificaciones": CLASIFICACION_CLIENTE,
        "personas": personas.order_by(Client.apellidos).limit(100).all(),
        "empresas": empresas.order_by(Company.nombre_comercial).all()}, tab))


@router.post("/clientes/persona")
def crear_persona(nombre: str = Form(...), apellidos: str = Form(...),
                  telefono: str = Form(""), email: str = Form(""),
                  tipo_doc: str = Form("DNI"), nro_doc: str = Form(""),
                  distrito: str = Form(""), clasificacion: str = Form("Nuevo"),
                  concepto: str = Form(""),
                  db: Session = Depends(get_db), user=Ventas):
    doc = (nro_doc or "").strip() or None
    if tipo_doc == "RUC":
        # RUC flexible: normaliza sin bloquear el guardado.
        doc = peru_svc.normalizar_ruc(doc)
    else:
        try:
            peru_svc.validar_doc(tipo_doc, doc)
        except ValueError:
            return RedirectResponse("/comercial/clientes", status_code=303)
    if clasificacion not in CLASIFICACION_CLIENTE:
        clasificacion = "Nuevo"
    c = Client(nombre=nombre.strip(), apellidos=apellidos.strip(),
               telefono=telefono.strip() or None, email=(email or "").strip() or None,
               tipo_doc=tipo_doc, nro_doc=doc,
               distrito=distrito.strip() or None, clasificacion=clasificacion)
    db.add(c)
    db.commit()
    db.refresh(c)
    back = f"/produccion/fichas/{c.id}"
    if (concepto or "").strip():
        from urllib.parse import quote
        back += f"?concepto={quote(concepto.strip())}"
    return RedirectResponse(back, status_code=303)


@router.post("/clientes/persona-rapido")
async def crear_persona_rapido(request: Request, db: Session = Depends(get_db),
                               user=Ventas):
    """Alta on-the-fly desde citas/CRM (JSON, sin recargar)."""
    data = await request.json()
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return JSONResponse({"error": "Nombre requerido"}, status_code=400)
    c = Client(nombre=nombre, apellidos=(data.get("apellidos") or "").strip(),
               telefono=data.get("telefono") or None,
               tipo_doc="DNI", nro_doc=None, clasificacion="Nuevo")
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "nombre": c.nombre_completo}


@router.post("/clientes/empresa")
def crear_empresa(nombre_comercial: str = Form(...), ruc: str = Form(""),
                  telefono: str = Form(""), email: str = Form(""),
                  descuento_pct: str = Form("0"), distrito: str = Form(""),
                  clasificacion: str = Form("Nuevo"),
                  db: Session = Depends(get_db), user=Ventas):
    """Alta de empresa B2B → tabla `companies` (personas van a `clients`).

    Payload esperado del formulario: nombre_comercial, ruc, telefono,
    distrito, clasificacion, descuento_pct. El RUC es flexible: se normaliza
    (sin espacios/guiones/puntos) y nunca bloquea el guardado; si no pasa la
    verificación SUNAT se avisa con `?warn=ruc` de forma no bloqueante.
    """
    nombre = (nombre_comercial or "").strip()
    if not nombre:
        return RedirectResponse("/comercial/clientes?tab=empresas&error=nombre_requerido",
                                status_code=303)
    ruc_val = peru_svc.normalizar_ruc(ruc)
    warn = ""
    if ruc_val and not peru_svc.validar_ruc(ruc_val):
        warn = "&warn=ruc"
    if clasificacion not in CLASIFICACION_CLIENTE:
        clasificacion = "Nuevo"
    try:
        desc = float((descuento_pct or "0").strip() or 0)
    except (ValueError, AttributeError):
        desc = 0.0
    desc = max(0.0, min(100.0, desc))
    c = Company(nombre_comercial=nombre, ruc=ruc_val,
                telefono=(telefono or "").strip() or None,
                email=(email or "").strip() or None,
                descuento_pct=desc, distrito=(distrito or "").strip() or None,
                clasificacion=clasificacion)
    db.add(c)
    db.commit()
    db.refresh(c)
    # Volver al listado para que la empresa se vea en la tabla "Empresas (N)".
    return RedirectResponse(f"/comercial/clientes?tab=empresas{warn}", status_code=303)


@router.get("/clientes/empresa/{cid}", response_class=HTMLResponse)
def ficha_empresa(cid: int, request: Request, db: Session = Depends(get_db), user=Ventas):
    return templates.TemplateResponse(request, "comercial/cliente_empresa.html", _ctx({
        "user": user, "c": db.get(Company, cid),
        "contactos": db.query(Contact).filter(Contact.company_id == cid).all(),
        "pedidos": db.query(Order).filter(Order.company_id == cid).order_by(
            Order.id.desc()).all()}, "personas"))


@router.post("/clientes/empresa/{cid}/contactos")
def add_contacto(cid: int, nombre: str = Form(...), cargo: str = Form(""),
                 telefono: str = Form(""), email: str = Form(""),
                 db: Session = Depends(get_db), user=Ventas):
    principal = db.query(Contact).filter(Contact.company_id == cid).count() == 0
    db.add(Contact(company_id=cid, nombre=nombre.strip(), cargo=cargo or None,
                   telefono=telefono or None, email=email or None, es_principal=principal))
    db.commit()
    return RedirectResponse(f"/comercial/clientes/empresa/{cid}", status_code=303)


# ---------- Edición de clientes ----------
@router.get("/clientes/persona/{pid}/editar", response_class=HTMLResponse)
def editar_persona_form(pid: int, request: Request,
                        db: Session = Depends(get_db), user=Ventas):
    c = db.get(Client, pid)
    if not c:
        return RedirectResponse("/comercial/clientes", status_code=303)
    empresa = db.get(Company, c.company_id) if c.company_id else None
    return templates.TemplateResponse(request, "comercial/cliente_editar.html", _ctx({
        "user": user, "tipo": "persona", "cliente": c, "empresa": empresa,
        "empresas": db.query(Company).order_by(Company.nombre_comercial).all(),
        "clasificaciones": CLASIFICACION_CLIENTE}, "personas"))


@router.post("/clientes/persona/{pid}/editar")
def editar_persona(pid: int, nombre: str = Form(...), apellidos: str = Form(...),
                   telefono: str = Form(""), email: str = Form(""),
                   tipo_doc: str = Form("DNI"), nro_doc: str = Form(""),
                   direccion: str = Form(""), distrito: str = Form(""),
                   clasificacion: str = Form("Nuevo"), company_id: str = Form(""),
                   db: Session = Depends(get_db), user=Ventas):
    c = db.get(Client, pid)
    if not c:
        return RedirectResponse("/comercial/clientes", status_code=303)
    doc = (nro_doc or "").strip() or None
    if tipo_doc == "RUC":
        doc = peru_svc.normalizar_ruc(doc)
    else:
        try:
            peru_svc.validar_doc(tipo_doc, doc)
        except ValueError:
            return RedirectResponse("/comercial/clientes", status_code=303)
    c.nombre = nombre.strip()
    c.apellidos = apellidos.strip()
    c.telefono = telefono.strip() or None
    c.email = (email or "").strip() or None
    c.tipo_doc = tipo_doc
    c.nro_doc = doc
    c.direccion = direccion.strip() or None
    c.distrito = distrito.strip() or None
    c.clasificacion = clasificacion if clasificacion in CLASIFICACION_CLIENTE else "Nuevo"
    c.company_id = int(company_id) if company_id and company_id.isdigit() else None
    db.commit()
    return RedirectResponse("/comercial/clientes", status_code=303)


@router.get("/clientes/empresa/{cid}/editar", response_class=HTMLResponse)
def editar_empresa_form(cid: int, request: Request,
                        db: Session = Depends(get_db), user=Ventas):
    c = db.get(Company, cid)
    if not c:
        return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)
    return templates.TemplateResponse(request, "comercial/cliente_editar.html", _ctx({
        "user": user, "tipo": "empresa", "compania": c,
        "clasificaciones": CLASIFICACION_CLIENTE}, "empresas"))


@router.post("/clientes/empresa/{cid}/editar")
def editar_empresa(cid: int, nombre_comercial: str = Form(...),
                   ruc: str = Form(""), telefono: str = Form(""),
                   email: str = Form(""), direccion: str = Form(""),
                   distrito: str = Form(""), clasificacion: str = Form("Nuevo"),
                   descuento_pct: str = Form("0"),
                   db: Session = Depends(get_db), user=Ventas):
    c = db.get(Company, cid)
    if not c:
        return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)
    c.nombre_comercial = nombre_comercial.strip()
    c.ruc = peru_svc.normalizar_ruc(ruc)
    c.telefono = telefono.strip() or None
    c.email = (email or "").strip() or None
    c.direccion = direccion.strip() or None
    c.distrito = distrito.strip() or None
    c.clasificacion = clasificacion if clasificacion in CLASIFICACION_CLIENTE else "Nuevo"
    try:
        c.descuento_pct = max(0.0, min(100.0, float((descuento_pct or "0").strip() or 0)))
    except (ValueError, AttributeError):
        c.descuento_pct = 0.0
    db.commit()
    return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)


# ---------- Colaboradores B2B (beneficiarios finales) ----------
@router.post("/clientes/empresa/{cid}/colaboradores")
def add_colaborador(cid: int, nombre: str = Form(...), apellidos: str = Form(""),
                    telefono: str = Form(""), distrito: str = Form(""),
                    clasificacion: str = Form("Nuevo"),
                    db: Session = Depends(get_db), user=Ventas):
    """Registra un colaborador (persona) vinculado a la empresa B2B."""
    if not db.get(Company, cid):
        return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)
    if clasificacion not in CLASIFICACION_CLIENTE:
        clasificacion = "Nuevo"
    c = Client(nombre=nombre.strip(), apellidos=apellidos.strip(),
               telefono=telefono.strip() or None, tipo_doc="DNI", nro_doc=None,
               distrito=distrito.strip() or None, clasificacion=clasificacion,
               company_id=cid)
    db.add(c)
    db.commit()
    return RedirectResponse(f"/comercial/clientes/detalle?tipo=empresa&id={cid}",
                            status_code=303)


@router.post("/clientes/empresa/{cid}/colaboradores/vincular")
def vincular_colaborador(cid: int, client_id: int = Form(...),
                         db: Session = Depends(get_db), user=Ventas):
    """Asocia una persona existente como colaborador de la empresa B2B."""
    if not db.get(Company, cid):
        return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)
    c = db.get(Client, client_id)
    if c:
        c.company_id = cid
        db.commit()
    return RedirectResponse(f"/comercial/clientes/detalle?tipo=empresa&id={cid}",
                            status_code=303)


@router.post("/clientes/empresa/{cid}/colaboradores/{pid}/desvincular")
def desvincular_colaborador(cid: int, pid: int,
                            db: Session = Depends(get_db), user=Ventas):
    c = db.get(Client, pid)
    if c and c.company_id == cid:
        c.company_id = None
        db.commit()
    return RedirectResponse(f"/comercial/clientes/detalle?tipo=empresa&id={cid}",
                            status_code=303)


@router.get("/clientes/detalle", response_class=HTMLResponse)
def cliente_detalle(request: Request, tipo: str = "persona", id: int = 0,
                    db: Session = Depends(get_db), user=Ventas):
    """Ficha 360°: info, compras, medidas (link taller), citas e interacciones."""
    from app.models.measurement import Measurement
    ctx: dict = {"user": user, "tipo": tipo}
    if tipo == "empresa":
        c = db.get(Company, id)
        if not c:
            return RedirectResponse("/comercial/clientes?tab=empresas", status_code=303)
        ctx.update(compania=c,
                   contactos=db.query(Contact).filter(Contact.company_id == id).all(),
                   colaboradores=db.query(Client).filter(
                       Client.company_id == id).order_by(Client.apellidos).all(),
                   personas_libres=db.query(Client).filter(
                       Client.company_id.is_(None)).order_by(
                       Client.apellidos).limit(200).all(),
                   clasificaciones=CLASIFICACION_CLIENTE,
                   pedidos=db.query(Order).filter(Order.company_id == id).order_by(
                       Order.id.desc()).all(),
                   inter=db.query(Interaccion).filter(
                       Interaccion.company_id == id).order_by(
                       Interaccion.id.desc()).limit(30).all())
    else:
        c = db.get(Client, id)
        if not c:
            return RedirectResponse("/comercial/clientes", status_code=303)
        meds = db.query(Measurement).filter(Measurement.client_id == id).order_by(
            Measurement.id.desc()).all()
        ctx.update(cliente=c, ultima=meds[0] if meds else None, n_medidas=len(meds),
                   pedidos=db.query(Order).filter(Order.client_id == id).order_by(
                       Order.id.desc()).all(),
                   citas=db.query(Appointment).filter(Appointment.client_id == id).order_by(
                       Appointment.inicio.desc()).limit(20).all(),
                   cots=db.query(Quotation).filter(Quotation.client_id == id).order_by(
                       Quotation.id.desc()).limit(20).all(),
                   inter=db.query(Interaccion).filter(
                       Interaccion.client_id == id).order_by(
                       Interaccion.id.desc()).limit(30).all())
    ctx["tipos_inter"] = INTERACCION_TIPOS
    return templates.TemplateResponse(request, "comercial/cliente_detalle.html",
                                      _ctx(ctx, "personas"))


@router.post("/interacciones")
def add_interaccion(tipo_ent: str = Form(...), ent_id: int = Form(...),
                    tipo: str = Form("nota"), texto: str = Form(...),
                    back: str = Form("/comercial/clientes"),
                    db: Session = Depends(get_db), user=Ventas):
    if tipo not in INTERACCION_TIPOS:
        tipo = "nota"
    kw = {"lead_id": None, "client_id": None, "company_id": None}
    if tipo_ent == "lead":
        kw["lead_id"] = ent_id
    elif tipo_ent == "empresa":
        kw["company_id"] = ent_id
    else:
        kw["client_id"] = ent_id
    db.add(Interaccion(tipo=tipo, texto=texto.strip(), usuario_id=user.id, **kw))
    db.commit()
    return RedirectResponse(back, status_code=303)


@router.get("/buscar")
def buscar(q: str = "", db: Session = Depends(get_db), user=VentasSastre):
    """Autocompletado global: nombre, RUC/DNI o teléfono. JSON."""
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    out = []
    for c in db.query(Client).filter(or_(Client.nombre.like(like),
                                         Client.apellidos.like(like),
                                         Client.nro_doc.like(like),
                                         Client.telefono.like(like))).limit(8).all():
        out.append({"tipo": "persona", "id": c.id, "nombre": c.nombre_completo,
                    "doc": c.doc_label, "telefono": c.telefono or ""})
    for e in db.query(Company).filter(or_(Company.nombre_comercial.like(like),
                                          Company.ruc.like(like),
                                          Company.telefono.like(like))).limit(8).all():
        out.append({"tipo": "empresa", "id": e.id, "nombre": e.nombre_comercial,
                    "doc": e.doc_label, "telefono": e.telefono or ""})
    return out


# ---------- Citas / Agenda operativa ----------
def _rango_dia(d: date) -> tuple[datetime, datetime]:
    from datetime import time as t
    return datetime.combine(d, t.min), datetime.combine(d, t.max)


@router.get("/citas", response_class=HTMLResponse)
def agenda(request: Request, vista: str = "lista", fecha: str = "",
           estado: str = "", db: Session = Depends(get_db), user=VentasSastre):
    try:
        base = date.fromisoformat(fecha) if fecha else date.today()
    except ValueError:
        base = date.today()
    if vista not in ("lista", "dia", "semana", "mes"):
        vista = "lista"
    ini, fin = _rango_dia(base)
    if vista == "semana":
        ini = datetime.combine(base - timedelta(days=base.weekday()), datetime.min.time())
        fin = ini + timedelta(days=7) - timedelta(seconds=1)
    elif vista == "mes":
        ini = datetime(base.year, base.month, 1)
        fin = datetime(base.year + (base.month == 12), base.month % 12 + 1, 1) - timedelta(seconds=1)
    q = db.query(Appointment).filter(Appointment.inicio >= ini, Appointment.inicio <= fin)
    if estado:
        q = q.filter(Appointment.estado == estado)
    citas = q.order_by(Appointment.inicio).limit(300).all()
    hoy_ini, hoy_fin = _rango_dia(date.today())
    pendientes_hoy = db.query(Appointment).filter(
        Appointment.inicio >= hoy_ini, Appointment.inicio <= hoy_fin,
        Appointment.estado == "PROGRAMADA").count()
    clientes = db.query(Client).order_by(Client.apellidos).all()
    cmap = {c.id: c for c in clientes}
    prendas = db.query(Garment).filter(
        Garment.estado_taller.notin_(["CALIDAD_OK", "entregado", "terminado"])).order_by(
        Garment.id.desc()).limit(100).all()
    folios = {o.id: o.folio for o in db.query(Order).all()}
    cal = None
    if vista == "mes":
        cal = _calendario_mes(base.year, base.month, db)
    return templates.TemplateResponse(request, "comercial/citas.html", _ctx({
        "user": user, "citas": citas, "clientes": clientes, "cmap": cmap,
        "vista": vista, "fecha": base.isoformat(), "estado": estado,
        "estados": APPOINTMENT_STATUS, "tipos": APPOINTMENT_TYPES,
        "prendas": prendas, "folios": folios, "cal": cal,
        "pendientes_hoy": pendientes_hoy,
        "semana_ini": ini.date().isoformat(), "semana_fin": fin.date().isoformat(),
    }, "citas"))


def _calendario_mes(year: int, month: int, db: Session) -> list:
    ini = datetime(year, month, 1)
    fin = datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(seconds=1)
    citas = db.query(Appointment).filter(Appointment.inicio >= ini,
                                         Appointment.inicio <= fin).all()
    por_dia: dict[int, list] = {}
    for a in citas:
        por_dia.setdefault(a.inicio.day, []).append(a)
    cmap = {c.id: c.nombre_completo for c in db.query(Client).all()}
    semanas = []
    for sem in calmod.monthcalendar(year, month):
        semanas.append([{"dia": d, "citas": [
            {"id": a.id, "hora": a.inicio.strftime("%H:%M"), "tipo": a.tipo,
             "estado": a.estado,
             "cliente": cmap.get(a.client_id, "?")} for a in por_dia.get(d, [])]}
            for d in sem])
    return semanas


@router.post("/citas")
def crear_cita(client_id: int = Form(...), tipo: str = Form(...),
               inicio: str = Form(...), fin: str = Form(...),
               garment_id: str = Form(""), notas: str = Form(""),
               db: Session = Depends(get_db), user=VentasSastre):
    if tipo not in APPOINTMENT_TYPES:
        tipo = "TOMA_MEDIDAS"
    db.add(Appointment(client_id=client_id, tipo=tipo, sastre_id=user.id,
                       garment_id=int(garment_id) if garment_id else None,
                       inicio=datetime.fromisoformat(inicio),
                       fin=datetime.fromisoformat(fin),
                       notas=notas or None))
    db.commit()
    return RedirectResponse("/comercial/citas", status_code=303)


@router.post("/citas/{cid}/estado")
def estado_cita(cid: int, estado: str = Form(...), motivo: str = Form(""),
                db: Session = Depends(get_db), user=VentasSastre):
    a = db.get(Appointment, cid)
    if estado in APPOINTMENT_STATUS:
        a.estado = estado
        if motivo:
            a.notas = ((a.notas or "") + f" | {motivo}").strip(" |")
        db.commit()
    return RedirectResponse("/comercial/citas", status_code=303)


@router.post("/citas/{cid}/cotizar")
def cotizar_cita(cid: int, db: Session = Depends(get_db), user=Ventas):
    """Convierte una cita de toma de medidas en cotización borrador."""
    a = db.get(Appointment, cid)
    if not a:
        return RedirectResponse("/comercial/citas", status_code=303)
    q = Quotation(folio=crm_svc.next_quotation_folio(db), client_id=a.client_id,
                  vendedor_id=user.id, notas=f"Generada desde cita #{a.id} ({a.tipo})")
    db.add(q)
    db.commit()
    a.estado = "CONFIRMADA"
    db.commit()
    return RedirectResponse(f"/comercial/crm/cotizaciones/{q.id}", status_code=303)


# ---------- CRM / Pipeline ----------
@router.get("/crm", response_class=HTMLResponse)
def panel(request: Request, canal: str = "", db: Session = Depends(get_db), user=Ventas):
    q = db.query(Lead)
    if canal:
        q = q.filter(Lead.canal == canal)
    leads = q.order_by(Lead.id.desc()).limit(300).all()
    columnas = {e: [l for l in leads if l.estado == e] for e in LEAD_STATUS}
    canales = db.query(Lead.canal, func.count(Lead.id)).group_by(Lead.canal).all()
    inter = {}
    lids = [l.id for l in leads]
    if lids:
        for i in db.query(Interaccion).filter(Interaccion.lead_id.in_(lids)).order_by(
                Interaccion.id.desc()).all():
            inter.setdefault(i.lead_id, []).append(i)
    return templates.TemplateResponse(request, "comercial/crm.html", _ctx({
        "user": user, "columnas": columnas, "etapas": LEAD_STATUS,
        "canal": canal, "canales": canales, "inter": inter,
        "origenes": LEAD_ORIGINS,
        "cotizaciones": db.query(Quotation).order_by(Quotation.id.desc()).limit(40).all(),
        "clientes": db.query(Client).order_by(Client.apellidos).all(),
        "empresas": db.query(Company).order_by(Company.nombre_comercial).all()}, "crm"))


@router.post("/crm/leads")
def crear_lead(nombre: str = Form(...), telefono: str = Form(""),
               email: str = Form(""), empresa: str = Form(""),
               tipo_cliente: str = Form("individual"), origen: str = Form("visita_tienda"),
               interes: str = Form("sastreria"), canal: str = Form(""),
               db: Session = Depends(get_db), user=Ventas):
    db.add(Lead(nombre=nombre.strip(), telefono=telefono or None, email=email or None,
                empresa=empresa or None, tipo_cliente=tipo_cliente, origen=origen,
                interes=interes, canal=canal or origen, vendedor_id=user.id))
    db.commit()
    return RedirectResponse("/comercial/crm", status_code=303)


@router.post("/crm/leads/{lid}/estado")
def estado_lead(lid: int, estado: str = Form(...), motivo_perdida: str = Form(""),
                db: Session = Depends(get_db), user=Ventas):
    lead = db.get(Lead, lid)
    if estado in LEAD_STATUS:
        lead.estado = estado
        lead.motivo_perdida = motivo_perdida.strip() or None if estado == "PERDIDO" else None
        db.commit()
    return RedirectResponse("/comercial/crm", status_code=303)


@router.post("/crm/leads/{lid}/interaccion")
def interaccion_lead(lid: int, tipo: str = Form("nota"), texto: str = Form(...),
                     db: Session = Depends(get_db), user=Ventas):
    if tipo not in ("llamada", "whatsapp", "nota", "visita"):
        tipo = "nota"
    db.add(Interaccion(lead_id=lid, tipo=tipo, texto=texto.strip(), usuario_id=user.id))
    db.commit()
    return RedirectResponse("/comercial/crm", status_code=303)


@router.post("/crm/cotizaciones")
def crear_cotizacion(lead_id: str = Form(""), client_id: str = Form(""),
                     company_id: str = Form(""), validez_hasta: str = Form(""),
                     db: Session = Depends(get_db), user=Ventas):
    from datetime import date as date_cls
    try:
        validez = date_cls.fromisoformat(validez_hasta) if validez_hasta else None
    except ValueError:
        validez = None
    q = Quotation(folio=crm_svc.next_quotation_folio(db),
                  lead_id=int(lead_id) if lead_id else None,
                  client_id=int(client_id) if client_id else None,
                  company_id=int(company_id) if company_id else None,
                  vendedor_id=user.id, validez_hasta=validez)
    if q.company_id and not q.descuento_pct:
        comp = db.get(Company, q.company_id)
        if comp:
            q.descuento_pct = comp.descuento_pct
    db.add(q)
    db.commit()
    if q.lead_id:
        lead = db.get(Lead, q.lead_id)
        if lead and lead.estado == "PROSPECTO":
            lead.estado = "COTIZACION_ENVIADA"
            db.commit()
    return RedirectResponse(f"/comercial/crm/cotizaciones/{q.id}", status_code=303)


@router.get("/crm/cotizaciones/{qid}", response_class=HTMLResponse)
def ver_cotizacion(qid: int, request: Request, db: Session = Depends(get_db), user=Ventas):
    return templates.TemplateResponse(request, "comercial/cotizacion.html", _ctx({
        "user": user, "q": db.get(Quotation, qid),
        "lineas": db.query(QuotationLine).filter(QuotationLine.quotation_id == qid).all(),
        "tipos": GARMENT_TYPES,
        "variantes": db.query(ProductVariant).order_by(ProductVariant.sku).all()}, "crm"))


@router.post("/crm/cotizaciones/{qid}/lineas")
def add_linea(qid: int, concepto: str = Form(...), categoria: str = Form("prenda_medida"),
              garment_tipo: str = Form(""), variant_id: str = Form(""),
              cantidad: float = Form(1), precio_unitario: float = Form(0),
              db: Session = Depends(get_db), user=Ventas):
    db.add(QuotationLine(quotation_id=qid, concepto=concepto.strip(), categoria=categoria,
                         garment_tipo=garment_tipo or None,
                         variant_id=int(variant_id) if variant_id else None,
                         cantidad=cantidad, precio_unitario=precio_unitario))
    db.commit()
    crm_svc.recalc_quotation(db, qid)
    return RedirectResponse(f"/comercial/crm/cotizaciones/{qid}", status_code=303)


@router.post("/crm/cotizaciones/{qid}/estado")
def estado_cot(qid: int, estado: str = Form(...),
               db: Session = Depends(get_db), user=Ventas):
    db.get(Quotation, qid).estado = estado
    db.commit()
    return RedirectResponse(f"/comercial/crm/cotizaciones/{qid}", status_code=303)


@router.post("/crm/cotizaciones/{qid}/convertir")
def convertir(qid: int, db: Session = Depends(get_db), user=Ventas):
    q = db.get(Quotation, qid)
    if q.estado == "borrador":
        q.estado = "aprobada"
        db.commit()
    try:
        crm_svc.convert_to_order(db, qid, user.id)
        if q.lead_id:
            lead = db.get(Lead, q.lead_id)
            if lead:
                lead.estado = "GANADO_EN_TALLER"
                db.commit()
    except ValueError:
        pass
    return RedirectResponse("/comercial/crm", status_code=303)
