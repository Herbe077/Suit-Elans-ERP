"""Compras: recepción que alimenta kardex + totales OC."""
from sqlalchemy.orm import Session

from app.models.purchasing import PurchaseLine, PurchaseOrder
from app.services.inventory import apply_movement


def next_po_folio(db: Session) -> str:
    count = db.query(PurchaseOrder).count() + 1
    return f"OC-{count:05d}"


def recalc_po(db: Session, pid: int) -> PurchaseOrder:
    po = db.get(PurchaseOrder, pid)
    lines = db.query(PurchaseLine).filter(PurchaseLine.purchase_id == pid).all()
    po.total = round(sum(l.importe for l in lines), 2)
    # Totales con IGV incluido (18%): desagrega base/igv para coherencia fiscal
    if po.total:
        po.subtotal = round(po.total / 1.18, 2)
        po.igv = round(po.total - po.subtotal, 2)
    else:
        po.subtotal = 0.0; po.igv = 0.0
    db.commit()
    db.refresh(po)
    # espeja totales a OrdenCompra del mismo folio
    try:
        from app.models.inventario import OrdenCompra
        oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first()
        if oc:
            oc.monto_total = po.total; oc.subtotal = po.subtotal; oc.igv = po.igv
            db.commit()
    except Exception:
        pass
    return po


def receive_line(db: Session, lid: int, cantidad: float, usuario_id: int | None) -> PurchaseLine:
    """Registra recepción parcial/total y genera entrada de kardex."""
    line = db.get(PurchaseLine, lid)
    if cantidad <= 0 or cantidad > line.pendiente + 1e-9:
        raise ValueError(f"Cantidad inválida (pendiente: {line.pendiente})")
    apply_movement(db, line.item_tipo, line.item_id, cantidad, "entrada",
                   f"OC {db.get(PurchaseOrder, line.purchase_id).folio}", usuario_id)
    line.cantidad_recibida = round(line.cantidad_recibida + cantidad, 2)
    po = db.get(PurchaseOrder, line.purchase_id)
    lines = db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).all()
    if all(l.pendiente <= 1e-9 for l in lines):
        po.estado = "recibida"
    else:
        po.estado = "recibida_parcial"
    db.commit()
    db.refresh(line)
    return line


ESTADO_OC_MAP = {"borrador": "BORRADOR", "enviada": "ENVIADA", "recibida_parcial": "RECIBIDA_PARCIAL",
                 "recibida": "RECIBIDA", "cancelada": "CANCELADA", "COMPLETADA": "COMPLETADA"}


def sincronizar_espejo_compras(db: Session) -> dict:
    """Crea el espejo faltante PO<->OrdenCompra por folio (no duplica). Retorna conteo."""
    from app.models.inventario import OrdenCompra
    creadas = {"oc": 0, "po": 0}
    for po in db.query(PurchaseOrder).all():
        if po.folio and not db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first():
            db.add(OrdenCompra(proveedor_id=po.supplier_id, estado=ESTADO_OC_MAP.get((po.estado or "").lower(), "BORRADOR"),
                               monto_total=po.total or 0, subtotal=po.subtotal or 0, igv=po.igv or 0, folio=po.folio))
            creadas["oc"] += 1
    for oc in db.query(OrdenCompra).all():
        if oc.folio and not db.query(PurchaseOrder).filter(PurchaseOrder.folio == oc.folio).first():
            db.add(PurchaseOrder(folio=oc.folio, supplier_id=oc.proveedor_id,
                                 estado=(oc.estado or "BORRADOR").lower(),
                                 subtotal=oc.subtotal or 0, igv=oc.igv or 0, total=oc.monto_total or 0))
            creadas["po"] += 1
    if creadas["oc"] or creadas["po"]:
        db.commit()
    return creadas
