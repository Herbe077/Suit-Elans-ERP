"""Dashboard con KPIs financieros y operativos."""
from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.appointment import Appointment
from app.models.catalog import ProductVariant
from app.models.crm import Lead
from app.models.inventory import Fabric, Supply
from app.models.order import Garment, Order, Payment

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    hoy = date.today()
    ventas_mes = db.query(func.coalesce(func.sum(Payment.monto), 0)).filter(
        func.date(Payment.created_at) >= hoy.replace(day=1).isoformat()
    ).scalar() or 0
    pedidos_activos = db.query(Order).filter(Order.estado.notin_(["entregado", "cancelado"])).count()
    prendas_taller = db.query(Garment).filter(
        Garment.estado_taller.notin_(["CALIDAD_OK", "entregado", "terminado"])).count()
    citas_hoy = db.query(Appointment).filter(func.date(Appointment.inicio) == hoy.isoformat()).count()
    telas_bajas = db.query(Fabric).filter(Fabric.stock_metros <= Fabric.stock_minimo).count()
    avios_bajos = db.query(Supply).filter(Supply.stock <= Supply.stock_minimo).count()
    var_bajas = db.query(ProductVariant).filter(
        ProductVariant.stock <= ProductVariant.stock_minimo).count()
    proximas = (
        db.query(Appointment).filter(Appointment.estado.in_(["PROGRAMADA", "CONFIRMADA"]))
        .order_by(Appointment.inicio).limit(6).all()
    )
    pedidos = db.query(Order).order_by(Order.created_at.desc()).limit(8).all()
    from app.core.constants import LEAD_OPEN
    leads_abiertos = db.query(Lead).filter(Lead.estado.in_(LEAD_OPEN)).count()
    por_cobrar = round(sum(max(0.0, o.total - o.anticipo) for o in db.query(Order).filter(
        Order.estado.notin_(["cancelado"])).all()), 2)
    return templates.TemplateResponse(request, "dashboard/panel.html", {
        "user": user,
        "ventas_mes": ventas_mes, "pedidos_activos": pedidos_activos,
        "prendas_taller": prendas_taller, "citas_hoy": citas_hoy,
        "alertas_stock": telas_bajas + avios_bajos + var_bajas,
        "leads_abiertos": leads_abiertos, "por_cobrar": por_cobrar,
        "proximas": proximas, "pedidos": pedidos,
    })
