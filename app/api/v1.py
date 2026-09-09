"""API v1 JSON — integración futura (web pública, Odoo). Auth: JWT Bearer."""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core import security
from app.core.database import get_db
from app.models.appointment import Appointment
from app.models.catalog import Product, ProductVariant
from app.models.company import Company, Contact
from app.models.crm import Lead, Quotation, QuotationLine
from app.models.order import Order
from app.models.user import User
from app.schemas.api import (
    AppointmentApiIn, CompanyIn, CompanyOut, ContactIn, InvoiceIn, InvoiceOut,
    LeadIn, LeadOut, LeadPatch, OrderOut, ProductIn, QuotationIn, QuotationLineIn,
    QuotationOut, StockAdjustIn, TesoreriaPagoIn, TesoreriaPagoOut, TokenOut,
    VariantIn, VariantOut,
)
from app.services import billing as billing_svc
from app.services import crm as crm_svc
from app.services import inventory as inv_svc

router = APIRouter(prefix="/api/v1", tags=["api-v1"])

WEBHOOK_EVENTS = [
    {"event": "lead.created", "method": "GET", "resource": "/api/v1/leads/{id}"},
    {"event": "quotation.converted", "method": "GET", "resource": "/api/v1/orders/{id}"},
    {"event": "order.created", "method": "GET", "resource": "/api/v1/orders/{id}"},
    {"event": "stock.low", "method": "GET", "resource": "/api/v1/products/low-stock"},
]


def get_api_user(authorization: str | None = Header(default=None),
                 db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta token Bearer")
    payload = security.decode_token(authorization[7:])
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuario inactivo")
    return user


Auth = Depends(get_api_user)


@router.post("/auth/token", response_model=TokenOut)
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username.lower().strip()).first()
    if not user or not security.verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return TokenOut(access_token=security.create_access_token(user.email, user.role))


@router.get("/webhooks")
def webhooks(user: User = Auth):
    return {"events": WEBHOOK_EVENTS}


# --- Leads ---
@router.get("/leads", response_model=list[LeadOut])
def list_leads(estado: str = "", db: Session = Depends(get_db), user: User = Auth):
    q = db.query(Lead)
    if estado:
        q = q.filter(Lead.estado == estado)
    return q.order_by(Lead.id.desc()).limit(200).all()


@router.post("/leads", response_model=LeadOut, status_code=201)
def create_lead(data: LeadIn, db: Session = Depends(get_db), user: User = Auth):
    lead = Lead(**data.model_dump(), vendedor_id=user.id)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/leads/{lid}", response_model=LeadOut)
def get_lead(lid: int, db: Session = Depends(get_db), user: User = Auth):
    lead = db.get(Lead, lid)
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    return lead


@router.patch("/leads/{lid}", response_model=LeadOut)
def patch_lead(lid: int, data: LeadPatch, db: Session = Depends(get_db), user: User = Auth):
    lead = db.get(Lead, lid)
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    if data.estado:
        lead.estado = data.estado
    if data.notas is not None:
        lead.notas = data.notas
    db.commit()
    db.refresh(lead)
    return lead


# --- Quotations ---
@router.post("/quotations", response_model=QuotationOut, status_code=201)
def create_quotation(data: QuotationIn, db: Session = Depends(get_db), user: User = Auth):
    q = Quotation(folio=crm_svc.next_quotation_folio(db), lead_id=data.lead_id,
                  client_id=data.client_id, company_id=data.company_id,
                  vendedor_id=user.id, validez_hasta=data.validez_hasta,
                  descuento_pct=data.descuento_pct, notas=data.notas)
    db.add(q)
    db.flush()
    for l in data.lineas:
        db.add(QuotationLine(quotation_id=q.id, **l.model_dump()))
    db.commit()
    return crm_svc.recalc_quotation(db, q.id)


@router.get("/quotations/{qid}", response_model=QuotationOut)
def get_quotation(qid: int, db: Session = Depends(get_db), user: User = Auth):
    q = db.get(Quotation, qid)
    if not q:
        raise HTTPException(404, "Cotización no encontrada")
    return q


@router.post("/quotations/{qid}/lines", response_model=QuotationOut)
def add_line(qid: int, data: QuotationLineIn, db: Session = Depends(get_db), user: User = Auth):
    if not db.get(Quotation, qid):
        raise HTTPException(404, "Cotización no encontrada")
    db.add(QuotationLine(quotation_id=qid, **data.model_dump()))
    db.commit()
    return crm_svc.recalc_quotation(db, qid)


@router.post("/quotations/{qid}/convert", response_model=OrderOut)
def convert(qid: int, db: Session = Depends(get_db), user: User = Auth):
    q = db.get(Quotation, qid)
    if not q:
        raise HTTPException(404, "Cotización no encontrada")
    if not q.client_id and not q.company_id:
        raise HTTPException(400, "La cotización necesita cliente o empresa para convertirse")
    if q.estado == "borrador":
        q.estado = "aprobada"
        db.commit()
    try:
        order = crm_svc.convert_to_order(db, qid, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return order


# --- Companies ---
@router.get("/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db), user: User = Auth):
    return db.query(Company).order_by(Company.nombre_comercial).limit(200).all()


@router.post("/companies", response_model=CompanyOut, status_code=201)
def create_company(data: CompanyIn, db: Session = Depends(get_db), user: User = Auth):
    c = Company(**data.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.post("/companies/{cid}/contacts", status_code=201)
def add_contact(cid: int, data: ContactIn, db: Session = Depends(get_db), user: User = Auth):
    if not db.get(Company, cid):
        raise HTTPException(404, "Empresa no encontrada")
    ct = Contact(company_id=cid, **data.model_dump())
    db.add(ct)
    db.commit()
    return {"id": ct.id}


# --- Catalog / stock ---
@router.get("/products")
def list_products(db: Session = Depends(get_db), user: User = Auth):
    out = []
    for p in db.query(Product).order_by(Product.codigo).all():
        variants = db.query(ProductVariant).filter(ProductVariant.product_id == p.id).all()
        out.append({"id": p.id, "codigo": p.codigo, "nombre": p.nombre, "linea": p.linea,
                    "precio_base": p.precio_base,
                    "variantes": [VariantOut.model_validate(v).model_dump() for v in variants]})
    return out


@router.post("/products", status_code=201)
def create_product(data: ProductIn, db: Session = Depends(get_db), user: User = Auth):
    p = Product(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id}


@router.post("/products/{pid}/variants", response_model=VariantOut, status_code=201)
def add_variant(pid: int, data: VariantIn, db: Session = Depends(get_db), user: User = Auth):
    if not db.get(Product, pid):
        raise HTTPException(404, "Producto no encontrado")
    v = ProductVariant(product_id=pid, **data.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.post("/stock/adjust")
def adjust_stock(data: StockAdjustIn, db: Session = Depends(get_db), user: User = Auth):
    try:
        mov = inv_svc.apply_movement(db, "variant", data.variant_id, data.cantidad,
                                     "entrada" if data.cantidad >= 0 else "salida",
                                     data.motivo or "Ajuste API", user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"movement_id": mov.id}


@router.get("/products/low-stock")
def low_stock(db: Session = Depends(get_db), user: User = Auth):
    falt = inv_svc.low_stock(db)
    return {"variantes": [f"{v.sku} ({v.stock})" for v in falt["variants"]],
            "telas": [f"{t.codigo} ({t.stock_metros})" for t in falt["fabrics"]],
            "avios": [f"{s.codigo} ({s.stock})" for s in falt["supplies"]],
            "insumos": [f"{p.sku} ({p.stock_disponible})" for p in falt["productos"]]}


# --- Orders / appointments / invoices ---
@router.get("/orders", response_model=list[OrderOut])
def list_orders(db: Session = Depends(get_db), user: User = Auth):
    return db.query(Order).order_by(Order.id.desc()).limit(200).all()


@router.get("/orders/{oid}", response_model=OrderOut)
def get_order(oid: int, db: Session = Depends(get_db), user: User = Auth):
    o = db.get(Order, oid)
    if not o:
        raise HTTPException(404, "Pedido no encontrado")
    return o


@router.get("/appointments")
def list_appointments(db: Session = Depends(get_db), user: User = Auth):
    appts = db.query(Appointment).order_by(Appointment.inicio).limit(200).all()
    return [{"id": a.id, "client_id": a.client_id, "tipo": a.tipo,
             "inicio": a.inicio.isoformat(), "fin": a.fin.isoformat(), "estado": a.estado}
            for a in appts]


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(data: InvoiceIn, db: Session = Depends(get_db), user: User = Auth):
    inv = billing_svc.emit_invoice(db, data.serie, data.order_id, data.client_id,
                                   data.company_id, data.igv_pct, user.id)
    return inv


@router.get("/invoices/{iid}", response_model=InvoiceOut)
def get_invoice(iid: int, db: Session = Depends(get_db), user: User = Auth):
    inv = db.get(billing_svc.Invoice, iid)
    if not inv:
        raise HTTPException(404, "Comprobante no encontrado")
    return inv


@router.post("/tesoreria/pagar-gasto", response_model=TesoreriaPagoOut)
def tesoreria_pagar_gasto(data: TesoreriaPagoIn, db: Session = Depends(get_db),
                          user: User = Auth):
    """Pago unificado de Tesorería (gasto + CxP + caja + diario)."""
    from app.services import contabilidad as contab
    from app.services import tesoreria_service as tes
    try:
        if data.gasto_id:
            return tes.ejecutar_pago_proveedor(
                db, data.gasto_id, medio_pago=data.medio_pago or "banco",
                monto=data.monto, usuario_id=user.id,
                voucher=data.voucher,
                permitir_sobregiro=bool(data.permitir_sobregiro))
        if data.cxp_id:
            if not data.monto:
                raise ValueError("monto es obligatorio para pagar CxP")
            r = contab.pagar_proveedor(
                db, data.cxp_id, float(data.monto),
                cuenta_codigo=tes.cuenta_por_medio(data.medio_pago or "banco"),
                usuario_id=user.id, voucher=data.voucher,
                permitir_sobregiro=bool(data.permitir_sobregiro))
            cxp = db.get(contab.CuentaPorPagar, data.cxp_id)
            return {"gasto_id": None, "cxp_id": data.cxp_id,
                    "asiento_id": r["asiento_id"],
                    "asiento_numero": r["asiento_numero"],
                    "monto": float(data.monto), "gasto_estado": None,
                    "cxp_estado": cxp.estado if cxp else r["estado"],
                    "advertencia_sobregiro": r.get("advertencia_sobregiro")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    raise HTTPException(status_code=400, detail="Indica gasto_id o cxp_id")
