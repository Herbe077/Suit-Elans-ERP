"""Caja: ingresos/egresos, saldos y cuentas por cobrar/pagar."""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.billing import CashMovement
from app.models.order import Order
from app.models.purchasing import PurchaseOrder


def add_movement(db: Session, tipo: str, concepto: str, monto: float, metodo: str,
                 usuario_id: int | None, order_id: int | None = None,
                 purchase_id: int | None = None,
                 turno_id: int | None = None) -> CashMovement:
    from app.services import medios_pago as _mp
    if monto <= 0:
        raise ValueError("El monto debe ser positivo")
    metodo_n = _mp.normalizar_medio(metodo, default="EFECTIVO")
    m = CashMovement(tipo=tipo, concepto=concepto, monto=round(monto, 2),
                     metodo=metodo_n, medio_pago=metodo_n,
                     cuenta_contable_id=_mp.id_cuenta(
                         db, _mp.cuenta_por_medio(metodo_n)),
                     usuario_id=usuario_id, order_id=order_id, purchase_id=purchase_id,
                     turno_id=turno_id)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def balance(db: Session) -> dict:
    ing = db.query(func.coalesce(func.sum(CashMovement.monto), 0)).filter(
        CashMovement.tipo == "ingreso").scalar() or 0
    egr = db.query(func.coalesce(func.sum(CashMovement.monto), 0)).filter(
        CashMovement.tipo == "egreso").scalar() or 0
    por_cobrar = sum(max(0.0, o.total - o.anticipo)
                     for o in db.query(Order).filter(
                         Order.estado.notin_(["cancelado"])).all())
    por_pagar = sum(p.total for p in db.query(PurchaseOrder).filter(
        PurchaseOrder.estado.notin_(["recibida", "cancelada"])).all())
    return {"ingresos": round(ing, 2), "egresos": round(egr, 2),
            "saldo": round(ing - egr, 2),
            "por_cobrar": round(por_cobrar, 2), "por_pagar": round(por_pagar, 2)}
