"""Reportes PDF: ficha de medidas, ticket, cotización y comprobante."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.client import Client
from app.models.company import Company
from app.models.crm import Quotation, QuotationLine
from app.models.measurement import Measurement
from app.models.order import Garment, Order
from app.services.reports import cotizacion_pdf, ficha_medidas_pdf, ticket_pedido_pdf

router = APIRouter(prefix="/reportes", tags=["reportes"])
Auth = Depends(require_roles("SASTRE-MAESTRO", "SASTRE-ASISTENTE", "VENTA"))


@router.get("/medidas/{mid}.pdf")
def pdf_medidas(mid: int, db: Session = Depends(get_db), user=Auth):
    m = db.get(Measurement, mid)
    c = db.get(Client, m.client_id)
    medidas = {k: getattr(m, k) for k in (
        "cuello", "hombro", "sisa", "pecho", "cintura_saco", "cadera",
        "largo_manga", "largo_espalda", "largo_saco", "cintura_pantalon",
        "tiro", "largo_pantalon") if getattr(m, k) is not None}
    pdf = ficha_medidas_pdf(c.nombre_completo, medidas, m.observaciones or "")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=ficha_{mid}.pdf"})


def _order_name(db: Session, o: Order) -> str:
    if o.client_id and db.get(Client, o.client_id):
        return db.get(Client, o.client_id).nombre_completo
    if o.company_id and db.get(Company, o.company_id):
        return db.get(Company, o.company_id).nombre_comercial
    return "—"


@router.get("/pedido/{oid}.pdf")
def pdf_pedido(oid: int, db: Session = Depends(get_db), user=Auth):
    o = db.get(Order, oid)
    prendas = [{"tipo": g.tipo, "precio": g.precio}
               for g in db.query(Garment).filter(Garment.order_id == oid).all()]
    pdf = ticket_pedido_pdf(o.folio, _order_name(db, o), prendas, o.total, o.anticipo)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=ticket_{o.folio}.pdf"})


@router.get("/cotizacion/{qid}.pdf")
def pdf_cotizacion(qid: int, db: Session = Depends(get_db), user=Auth):
    q = db.get(Quotation, qid)
    nombre = "—"
    if q.client_id and db.get(Client, q.client_id):
        nombre = db.get(Client, q.client_id).nombre_completo
    elif q.company_id and db.get(Company, q.company_id):
        nombre = db.get(Company, q.company_id).nombre_comercial
    lineas = [{"concepto": l.concepto, "cantidad": l.cantidad, "importe": l.importe}
              for l in db.query(QuotationLine).filter(QuotationLine.quotation_id == qid).all()]
    pdf = cotizacion_pdf(q.folio, nombre, lineas, q.subtotal, q.descuento_pct, q.total,
                         str(q.validez_hasta) if q.validez_hasta else "")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename={q.folio}.pdf"})
