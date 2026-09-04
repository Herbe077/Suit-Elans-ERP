"""Comprobantes internos: correlativos por serie + IGV."""
from sqlalchemy.orm import Session

from app.core.constants import IGV_PCT
from app.models.billing import Invoice
from app.models.order import Garment, Order


def next_numero(db: Session, serie: str) -> str:
    nums = [int(i.numero) for i in db.query(Invoice).filter(Invoice.serie == serie).all()
            if i.numero.isdigit()]
    return f"{(max(nums) + 1 if nums else 1):06d}"


def lines_for_order(db: Session, order_id: int) -> list[dict]:
    o = db.get(Order, order_id)
    garments = db.query(Garment).filter(Garment.order_id == order_id).all()
    lineas = ([{"concepto": f"Confección {g.tipo}", "importe": g.precio} for g in garments]
              if garments else [{"concepto": f"Pedido {o.folio}", "importe": o.total}])
    # Concilia con el total del pedido (puede incluir descuento corporativo/cotización)
    dif = round(o.total - sum(l["importe"] for l in lineas), 2)
    if abs(dif) >= 0.01:
        lineas.append({"concepto": "Descuento comercial", "importe": dif})
    return lineas


def emit_invoice_flush(db: Session, serie: str, order_id: int | None, client_id: int | None,
                       company_id: int | None, igv_pct: float, usuario_id: int | None,
                       lineas: list[dict] | None = None) -> Invoice:
    """Variante componible: flush SIN commit (para transacciones atómicas)."""
    lineas = lineas if lineas is not None else (
        lines_for_order(db, order_id) if order_id else [])
    subtotal = round(sum(l["importe"] for l in lineas), 2)
    igv = round(subtotal * igv_pct / 100, 2)
    inv = Invoice(serie=serie, numero=next_numero(db, serie), order_id=order_id,
                  client_id=client_id, company_id=company_id,
                  subtotal=subtotal, igv_pct=igv_pct, igv=igv,
                  total=round(subtotal + igv, 2), usuario_id=usuario_id)
    db.add(inv)
    db.flush()
    db.refresh(inv)
    return inv


def emit_invoice(db: Session, serie: str, order_id: int | None, client_id: int | None,
                 company_id: int | None, igv_pct: float, usuario_id: int | None,
                 lineas: list[dict] | None = None) -> Invoice:
    inv = emit_invoice_flush(db, serie, order_id, client_id, company_id,
                             igv_pct, usuario_id, lineas)
    db.commit()
    db.refresh(inv)
    return inv


def default_igv() -> float:
    return IGV_PCT
