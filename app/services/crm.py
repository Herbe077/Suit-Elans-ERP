"""CRM: folios, totales, conversión cotización → pedido."""
from datetime import date
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.crm import Quotation, QuotationLine
from app.models.order import Garment, Order
from app.services.orders import next_folio, recalc_total


def next_quotation_folio(db: Session) -> str:
    year = date.today().year
    count = db.query(Quotation).count() + 1
    return f"COT-{year}-{count:04d}"


def recalc_quotation(db: Session, qid: int) -> Quotation:
    q = db.get(Quotation, qid)
    lines = db.query(QuotationLine).filter(QuotationLine.quotation_id == qid).all()
    q.subtotal = round(sum(l.importe for l in lines), 2)
    q.total = round(q.subtotal * (1 - (q.descuento_pct or 0) / 100), 2)
    # Descuento corporativo automático si no se fijó manual
    if not q.descuento_pct and q.company_id:
        comp = db.get(Company, q.company_id)
        if comp and comp.descuento_pct:
            q.descuento_pct = comp.descuento_pct
            q.total = round(q.subtotal * (1 - comp.descuento_pct / 100), 2)
    db.commit()
    db.refresh(q)
    return q


def convert_to_order(db: Session, qid: int, user_id: int | None) -> Order:
    """Convierte cotización aprobada en pedido con trazabilidad; crea prendas de medida."""
    q = db.get(Quotation, qid)
    if q.estado == "convertida":
        raise ValueError("La cotización ya fue convertida")
    if q.estado not in ("aprobada", "enviada", "borrador"):
        raise ValueError(f"No convertible desde estado {q.estado}")
    order = Order(
        folio=next_folio(db),
        client_id=q.client_id, company_id=q.company_id,
        lead_id=q.lead_id, quotation_id=q.id,
        sastre_id=user_id, estado="confirmado",
        canal="corporativo" if q.company_id else "sastreria",
    )
    db.add(order)
    db.flush()
    for l in db.query(QuotationLine).filter(QuotationLine.quotation_id == qid).all():
        if l.categoria == "prenda_medida" and l.garment_tipo:
            g = Garment(order_id=order.id, tipo=l.garment_tipo, precio=l.precio_unitario)
            db.add(g)
            db.flush()
            from app.services.taller import codigo_qr
            g.codigo_qr = codigo_qr(order.folio, g.id)
        elif l.categoria == "prenda_comercial" and l.variant_id:
            from app.models.catalog import ProductVariant
            v = db.get(ProductVariant, l.variant_id)
            if v and v.stock >= l.cantidad:
                v.stock = round(v.stock - l.cantidad, 2)
    q.estado = "convertida"
    db.commit()
    recalc_total(db, order.id)
    # El pedido conserva el total cotizado (con descuento corporativo incluido)
    order.total = q.total
    db.commit()
    db.refresh(order)
    return order
