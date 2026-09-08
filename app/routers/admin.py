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

PERSONAL_DIR = BASE_DIR / "app" / "static" / "uploads" / "personal"
PERSONAL_DIR.mkdir(parents=True, exist_ok=True)
PERSONAL_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
PERSONAL_MAX_BYTES = 5 * 1024 * 1024


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


# ---------- Personal y Contratos ----------
def _personal_ctx():
    from app.models.personnel import PUESTOS, TIPOS_CONTRATO
    return {"puestos": PUESTOS, "tipos": TIPOS_CONTRATO}


async def _guardar_doc(upload, prefijo: str, emp_id: int | None) -> str | None:
    """Guarda CV/contrato adjunto (máx 5 MB, pdf/imagen). Retorna filename."""
    import uuid as _uuid
    from pathlib import Path as _Path
    if not upload or not getattr(upload, "filename", None):
        return None
    ext = _Path(upload.filename).suffix.lower()
    if ext not in PERSONAL_EXTS:
        raise ValueError(f"Extensión no permitida: {ext}")
    raw = await upload.read(PERSONAL_MAX_BYTES + 1)
    if len(raw) > PERSONAL_MAX_BYTES:
        raise ValueError("Archivo mayor a 5 MB")
    nombre = f"emp_{emp_id or 'new'}_{prefijo}_{_uuid.uuid4().hex[:8]}{ext}"
    (PERSONAL_DIR / nombre).write_bytes(raw)
    return nombre


@router.get("/personal", response_class=HTMLResponse)
def personal_list(request: Request, editar: str = "", error: str = "",
                  db: Session = Depends(get_db), user=Auth):
    from app.models.personnel import Empleado, ensure_empleados_table
    from app.models.user import User as _User
    ensure_empleados_table(db)
    emp = None
    if editar.isdigit():
        emp = db.get(Empleado, int(editar))
    return templates.TemplateResponse(request, "admin/personal.html", {
        "user": user, "error": error, "emp": emp,
        "empleados": db.query(Empleado).order_by(Empleado.apellidos).all(),
        "usuarios": db.query(_User).order_by(_User.full_name).all(),
        **_personal_ctx()})


@router.post("/personal", response_class=HTMLResponse)
async def personal_crear(request: Request, db: Session = Depends(get_db), user=Auth):
    from app.models.personnel import PUESTOS, TIPOS_CONTRATO, Empleado
    form = await request.form()
    nombres = (form.get("nombres") or "").strip()
    apellidos = (form.get("apellidos") or "").strip()
    dni = (form.get("dni") or "").strip() or None
    ruc = (form.get("ruc") or "").strip() or None
    puesto = (form.get("puesto") or "SASTRE_MAESTRO").strip().upper()
    tipo = (form.get("tipo_contrato") or "DESTAJO_4TA").strip().upper()
    if not nombres or not apellidos:
        return RedirectResponse("/admin/personal?error=nombres", status_code=303)
    if not dni and not ruc:
        return RedirectResponse("/admin/personal?error=documento", status_code=303)
    if puesto not in PUESTOS or tipo not in TIPOS_CONTRATO:
        return RedirectResponse("/admin/personal?error=datos", status_code=303)
    try:
        uid = int(form.get("user_id")) if form.get("user_id") else None
    except Exception:
        uid = None
    emp = Empleado(nombres=nombres, apellidos=apellidos, dni=dni, ruc=ruc,
                   telefono=(form.get("telefono") or "").strip() or None,
                   puesto=puesto, tipo_contrato=tipo,
                   aplica_retencion_8=str(form.get("aplica_retencion_8") or "").lower() in ("1", "on", "true"),
                   user_id=uid)
    db.add(emp)
    db.flush()
    try:
        emp.cv_filename = await _guardar_doc(form.get("cv"), "cv", emp.id) or None
        emp.contrato_filename = await _guardar_doc(form.get("contrato"), "contrato", emp.id) or None
    except ValueError:
        db.rollback()
        return RedirectResponse("/admin/personal?error=archivo", status_code=303)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse("/admin/personal?error=duplicado", status_code=303)
    return RedirectResponse("/admin/personal", status_code=303)


@router.post("/personal/{eid}", response_class=HTMLResponse)
async def personal_editar(eid: int, request: Request, db: Session = Depends(get_db), user=Auth):
    from app.models.personnel import PUESTOS, TIPOS_CONTRATO, Empleado
    emp = db.get(Empleado, eid)
    if not emp:
        return RedirectResponse("/admin/personal", status_code=303)
    form = await request.form()
    if form.get("nombres"):
        emp.nombres = str(form.get("nombres")).strip()
    if form.get("apellidos"):
        emp.apellidos = str(form.get("apellidos")).strip()
    emp.dni = (form.get("dni") or "").strip() or None
    emp.ruc = (form.get("ruc") or "").strip() or None
    if (form.get("puesto") or "").strip().upper() in PUESTOS:
        emp.puesto = str(form.get("puesto")).strip().upper()
    if (form.get("tipo_contrato") or "").strip().upper() in TIPOS_CONTRATO:
        emp.tipo_contrato = str(form.get("tipo_contrato")).strip().upper()
    emp.aplica_retencion_8 = str(form.get("aplica_retencion_8") or "").lower() in ("1", "on", "true")
    emp.telefono = (form.get("telefono") or "").strip() or None
    try:
        emp.user_id = int(form.get("user_id")) if form.get("user_id") else None
    except Exception:
        emp.user_id = None
    emp.activo = str(form.get("activo") or "1").lower() in ("1", "on", "true")
    try:
        cv = await _guardar_doc(form.get("cv"), "cv", emp.id)
        if cv:
            emp.cv_filename = cv
        ct = await _guardar_doc(form.get("contrato"), "contrato", emp.id)
        if ct:
            emp.contrato_filename = ct
    except ValueError:
        db.rollback()
        return RedirectResponse(f"/admin/personal?editar={eid}&error=archivo", status_code=303)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(f"/admin/personal?editar={eid}&error=duplicado", status_code=303)
    return RedirectResponse("/admin/personal", status_code=303)


@router.post("/personal/{eid}/eliminar", response_class=HTMLResponse)
def personal_eliminar(eid: int, db: Session = Depends(get_db), user=Auth):
    from app.models.personnel import Empleado
    emp = db.get(Empleado, eid)
    if emp:
        db.delete(emp)
        db.commit()
    return RedirectResponse("/admin/personal", status_code=303)


@router.get("/personal/{eid}/documento", response_class=HTMLResponse)
def personal_documento(eid: int, tipo: str = "cv", db: Session = Depends(get_db), user=Auth):
    from fastapi.responses import FileResponse
    from app.models.personnel import Empleado
    emp = db.get(Empleado, eid)
    fname = (emp.cv_filename if tipo == "cv" else emp.contrato_filename) if emp else None
    path = (PERSONAL_DIR / fname) if fname else None
    if not path or not path.is_file():
        return HTMLResponse("Documento no encontrado", status_code=404)
    return FileResponse(str(path), filename=fname)
