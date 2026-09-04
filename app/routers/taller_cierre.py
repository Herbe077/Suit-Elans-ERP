"""Cierre de jornada y reporte de pagos por taller.

Aislamiento: solo usa tablas taller_* (+ users para identidad).
Acceso: roles admin y taller. El reporte lo ve admin completo;
el operario de taller solo ve sus propias liquidaciones.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.taller import TallerCierreJornada, TallerTarea
from app.models.user import User
from app.services import taller_cierre as svc

router = APIRouter(prefix="/taller", tags=["taller-cierre"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Auth = Depends(require_roles("SASTRE-ASISTENTE"))  # ADMIN pasa siempre


@router.get("/cierre-jornada", response_class=HTMLResponse)
def form_cierre(request: Request, ok: str = "", db: Session = Depends(get_db), user=Auth):
    from app.models.order import Garment, Order
    tareas = db.query(TallerTarea).filter(TallerTarea.activa.is_(True)).order_by(
        TallerTarea.id).all()
    prendas = db.query(Garment).filter(
        Garment.estado_taller.notin_(["terminado", "entregado"])).order_by(
        Garment.id.desc()).limit(80).all()
    folios = {o.id: o.folio for o in db.query(Order).all()}
    productos = [{"gid": g.id, "ref": svc.ref_producto(folios.get(g.order_id, "?"), g.tipo, g.id),
                  "estado": g.estado_taller} for g in prendas]
    reclamos = svc.reclamos_hoy(db)
    mias = db.query(TallerCierreJornada).filter(
        TallerCierreJornada.fecha >= svc.inicio_hoy(),
        TallerCierreJornada.user_id == user.id).order_by(
        TallerCierreJornada.id.desc()).all()
    return templates.TemplateResponse(request, "taller/cierre.html", {
        "user": user, "tareas": tareas, "productos": productos,
        "reclamos": reclamos, "mias": mias, "ok": ok})


@router.post("/cierre-jornada")
async def guardar_cierre(request: Request, db: Session = Depends(get_db), user=Auth):
    """Un cierre por prenda seleccionada. Las tareas ya cobradas hoy por
    otros en esa prenda se ignoran en servidor (candado anti-doble-cobro)."""
    from app.models.order import Garment, Order
    form = await request.form()
    creados = 0
    for key in form.keys():
        if not key.startswith("prod_"):
            continue
        try:
            gid = int(key[5:])
        except ValueError:
            continue
        g = db.get(Garment, gid)
        if not g:
            continue
        o = db.get(Order, g.order_id)
        ref = svc.ref_producto(o.folio if o else "?", g.tipo, g.id)
        try:
            ids = [int(v) for v in form.getlist(f"tasks_{gid}")]
        except ValueError:
            continue
        bloqueadas = svc.tareas_reclamadas_otros(db, ref, user.id)
        ids = [i for i in ids if i not in bloqueadas]
        if not ids:
            continue
        try:
            svc.registrar_cierre(db, user.id, ref, ids)
            creados += 1
        except ValueError:
            continue
    return RedirectResponse(f"/taller/cierre-jornada?ok={creados}", status_code=303)


@router.get("/reporte-pagos", response_class=HTMLResponse)
def reporte(request: Request, preset: str = "semanal", desde: str = "", hasta: str = "",
            empleado: str = "", db: Session = Depends(get_db), user=Auth):
    # Operario de taller: solo sus propias liquidaciones
    if user.role != "admin":
        empleado = str(user.id)
    try:
        inicio, fin = svc.rango_fechas(preset, desde, hasta)
    except ValueError:
        inicio, fin = svc.rango_fechas("semanal")
        preset = "semanal"
    rep = svc.reporte_pagos(db, inicio, fin, int(empleado) if empleado else None)
    empleados = db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name).all()
    return templates.TemplateResponse(request, "taller/reporte_pagos.html", {
        "user": user, "rep": rep, "preset": preset, "desde": desde, "hasta": hasta,
        "empleado": empleado, "empleados": empleados,
        "n_jornadas": len(rep["cierre_ids"])})
