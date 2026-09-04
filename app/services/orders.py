"""Pedidos: folios, totales, estados."""
from datetime import date
from sqlalchemy.orm import Session

from app.models.order import Garment, Order


def next_folio(db: Session) -> str:
    year = date.today().year
    count = db.query(Order).count() + 1
    return f"SE-{year}-{count:04d}"


def recalc_total(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    garments = db.query(Garment).filter(Garment.order_id == order_id).all()
    order.total = round(sum(g.precio for g in garments), 2)
    db.commit()
    db.refresh(order)
    return order
