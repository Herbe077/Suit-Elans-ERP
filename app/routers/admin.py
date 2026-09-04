"""Ámbito Administración: usuarios/roles, operaciones SAM y sede."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import BASE_DIR
from app.core.constants import ROLES_CANONICOS
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.config import DEFAULTS
from app.models.order import Operation
from app.models.user import User
from app.services import config as config_svc

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Auth = Depends(require_roles("ADMIN"))


def _is_admin_role(role: str) -> bool:
    return role.lower() == "admin" if role else False


@router.get("/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, error: str = "", ok: str = "", uid: str = "",
             db: Session = Depends(get_db), user=Auth):
    detalle = _vinculos(db, int(uid)) if error == "vinculos" and uid.isdigit() else []
    return templates.TemplateResponse(request, "admin/usuarios.html", {
        "user": user, "error": error, "ok": ok, "detalle": detalle,
        "usuarios": db.query(User).order_by(User.email).all(),
        "ops": db.query(Operation).order_by(Operation.codigo).all(),
        "roles": ROLES_CANONICOS})


def _vinculos(db: Session, uid: int) -> list[str]:
    """Historial que impide borrar al usuario."""
    from app.models.appointment import Appointment
    from app.models.billing import CashMovement, Invoice
    from app.models.crm import Lead, Quotation
    from app.models.inventory import StockMovement
    from app.models.measurement import Measurement
    from app.models.order import ControlCalidad, Order, Payment, WorkLog
    from app.models.purchasing import PurchaseOrder
    from app.models.taller import TallerCierreJornada
    reglas = [
        (Order, Order.sastre_id, "pedidos como sastre/responsable"),
        (Lead, Lead.vendedor_id, "leads asignados"),
        (Quotation, Quotation.vendedor_id, "cotizaciones emitidas"),
        (WorkLog, WorkLog.operario_id, "fichajes de taller"),
        (TallerCierreJornada, TallerCierreJornada.user_id, "cierres de jornada"),
        (CashMovement, CashMovement.usuario_id, "movimientos de caja"),
        (Payment, Payment.usuario_id, "pagos registrados"),
        (Invoice, Invoice.usuario_id, "comprobantes emitidos"),
        (Appointment, Appointment.sastre_id, "citas asignadas"),
        (Measurement, Measurement.sastre_id, "tomas de medidas"),
        (ControlCalidad, ControlCalidad.aprobado_por, "controles de calidad"),
        (PurchaseOrder, PurchaseOrder.usuario_id, "órdenes de compra"),
        (StockMovement, StockMovement.usuario_id, "movimientos de kardex"),
    ]
    out = []
    for model, field, label in reglas:
        n = db.query(model).filter(field == uid).count()
        if n:
            out.append(f"{n} {label}")
    # Prendas asignadas como artesano
    from app.models.order import Garment
    n = db.query(Garment).filter(Garment.artesano_id == uid).count()
    if n:
        out.append(f"{n} prendas asignadas como artesano")
    return out


@router.post("/usuarios/{uid}/eliminar")
def eliminar_usuario(uid: int, db: Session = Depends(get_db), user=Auth):
    target = db.get(User, uid)
    if not target:
        return RedirectResponse("/admin/usuarios", status_code=303)
    if uid == user.id:
        return RedirectResponse("/admin/usuarios?error=propio", status_code=303)
    otros_admin = db.query(User).filter(func.lower(User.role) == "admin", User.is_active.is_(True),
                                        User.id != uid).count()
    if _is_admin_role(target.role) and not otros_admin:
        return RedirectResponse("/admin/usuarios?error=ultimo", status_code=303)
    if _vinculos(db, uid):
        return RedirectResponse(f"/admin/usuarios?error=vinculos&uid={uid}",
                                status_code=303)
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/usuarios?ok=eliminado", status_code=303)


@router.post("/usuarios/{uid}/toggle")
def toggle_usuario(uid: int, db: Session = Depends(get_db), user=Auth):
    """Activa/desactiva el acceso sin borrar el historial."""
    target = db.get(User, uid)
    if target and uid != user.id:
        if not (_is_admin_role(target.role) and target.is_active
                and db.query(User).filter(func.lower(User.role) == "admin",
                                          User.is_active.is_(True),
                                          User.id != uid).count() == 0):
            target.is_active = not target.is_active
            db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios")
def crear_usuario(email: str = Form(...), full_name: str = Form(...),
                  password: str = Form(...), role: str = Form(...),
                  db: Session = Depends(get_db), user=Auth):
    # Normaliza role a canónico si viene en minúsculas
    role = role.strip()
    # mantiene el valor tal cual (ADMIN, VENTA, etc.) para RBAC case-insensitive
    db.add(User(email=email.lower().strip(), full_name=full_name,
                hashed_password=security.hash_password(password), role=role))
    db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/operaciones")
def crear_op(codigo: str = Form(...), nombre: str = Form(...),
             tipo_prenda: str = Form("saco"), sam_minutos: float = Form(30),
             db: Session = Depends(get_db), user=Auth):
    db.add(Operation(codigo=codigo.strip(), nombre=nombre.strip(),
                     tipo_prenda=tipo_prenda, sam_minutos=sam_minutos))
    db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.get("/configuracion", response_class=HTMLResponse)
def configuracion(request: Request, db: Session = Depends(get_db), user=Auth):
    return templates.TemplateResponse(request, "admin/configuracion.html", {
        "user": user,
        "vals": {k: config_svc.get(db, k, v) for k, v in DEFAULTS.items()}})


@router.post("/configuracion")
async def guardar_config(request: Request, db: Session = Depends(get_db), user=Auth):
    form = await request.form()
    for clave in DEFAULTS:
        config_svc.set(db, clave, form.get(clave) or "")
    return RedirectResponse("/admin/configuracion", status_code=303)
