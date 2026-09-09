"""Cierre de jornada y pagos por taller — lógica aislada.

Solo toca tablas taller_*. El total se calcula siempre desde las tarifas
vigentes en DB (nunca se confía en el total enviado por el cliente) y cada
detalle congela la tarifa histórica aplicada.
"""
from datetime import date, datetime, time
from decimal import Decimal
from sqlalchemy.orm import Session

from app.core.constants import TALLER_TAREAS
from app.models.taller import TallerCierreDetalle, TallerCierreJornada, TallerTarea
from app.models.user import User


def seed_tareas(db: Session) -> int:
    """Carga idempotente del catálogo de 44 tareas (por par descripción+tarifa,
    pues el catálogo oficial repite una descripción). Retorna creadas."""
    creadas = 0
    for descripcion, tarifa in TALLER_TAREAS:
        if not db.query(TallerTarea).filter(
                TallerTarea.descripcion == descripcion,
                TallerTarea.tarifa == Decimal(str(tarifa))).first():
            db.add(TallerTarea(descripcion=descripcion, tarifa=Decimal(str(tarifa))))
            creadas += 1
    db.commit()
    return creadas


def registrar_cierre(db: Session, user_id: int, producto_referencia: str,
                     tarea_ids: list[int]) -> TallerCierreJornada:
    if not tarea_ids:
        raise ValueError("Selecciona al menos una actividad")
    tareas = db.query(TallerTarea).filter(
        TallerTarea.id.in_(tarea_ids), TallerTarea.activa.is_(True)).all()
    if len(tareas) != len(set(tarea_ids)):
        raise ValueError("Hay tareas inválidas o inactivas")
    total = sum((t.tarifa for t in tareas), Decimal("0"))
    cierre = TallerCierreJornada(user_id=user_id,
                                 producto_referencia=(producto_referencia or "").strip()[:160],
                                 total_pago=total)
    db.add(cierre)
    db.flush()
    for t in tareas:
        db.add(TallerCierreDetalle(cierre_id=cierre.id, tarea_id=t.id,
                                   tarifa_aplicada=t.tarifa))
    db.commit()
    db.refresh(cierre)
    return cierre


def inicio_hoy() -> datetime:
    return datetime.combine(date.today(), time.min)


def ref_producto(folio: str, tipo: str, gid: int) -> str:
    return f"{folio} · {tipo} #{gid}"


def reclamos_hoy(db: Session) -> dict[str, dict[int, str]]:
    """Mapa {producto_ref: {tarea_id: trabajador}} de lo ya cobrado hoy.

    Base del bloqueo: una actividad cobrada hoy por un operario en una
    prenda no puede volver a cobrarse por otro.
    """
    inicio = inicio_hoy()
    cierres = db.query(TallerCierreJornada).filter(
        TallerCierreJornada.fecha >= inicio).all()
    if not cierres:
        return {}
    users = {u.id: u.full_name for u in db.query(User).filter(
        User.id.in_({c.user_id for c in cierres})).all()}
    dets = db.query(TallerCierreDetalle).filter(
        TallerCierreDetalle.cierre_id.in_([c.id for c in cierres])).all()
    cmap = {c.id: c for c in cierres}
    reclamos: dict[str, dict[int, str]] = {}
    for d in dets:
        c = cmap[d.cierre_id]
        reclamos.setdefault(c.producto_referencia, {})[d.tarea_id] = \
            users.get(c.user_id, f"usuario#{c.user_id}")
    return reclamos


def tareas_reclamadas_otros(db: Session, ref: str, user_id: int) -> set[int]:
    """IDs de tareas de esta prenda ya cobradas hoy por OTROS operarios."""
    inicio = inicio_hoy()
    cids = [c.id for c in db.query(TallerCierreJornada).filter(
        TallerCierreJornada.fecha >= inicio,
        TallerCierreJornada.producto_referencia == ref,
        TallerCierreJornada.user_id != user_id).all()]
    if not cids:
        return set()
    return {d.tarea_id for d in db.query(TallerCierreDetalle).filter(
        TallerCierreDetalle.cierre_id.in_(cids)).all()}


def tareas_propias_hoy(db: Session, ref: str, user_id: int) -> set[int]:
    inicio = inicio_hoy()
    cids = [c.id for c in db.query(TallerCierreJornada).filter(
        TallerCierreJornada.fecha >= inicio,
        TallerCierreJornada.producto_referencia == ref,
        TallerCierreJornada.user_id == user_id).all()]
    if not cids:
        return set()
    return {d.tarea_id for d in db.query(TallerCierreDetalle).filter(
        TallerCierreDetalle.cierre_id.in_(cids)).all()}


def rango_fechas(preset: str, desde: str = "", hasta: str = "") -> tuple[datetime, datetime]:
    hoy = date.today()
    if preset == "diario":
        ini, fin = hoy, hoy
    elif preset == "mensual":
        ini, fin = hoy.replace(day=1), hoy
    elif preset == "personalizado" and desde and hasta:
        ini = date.fromisoformat(desde)
        fin = date.fromisoformat(hasta)
    else:  # semanal
        preset = "semanal"
        ini = fin = None
        from datetime import timedelta
        ini, fin = hoy - timedelta(days=6), hoy
    if ini > fin:
        ini, fin = fin, ini
    return datetime.combine(ini, time.min), datetime.combine(fin, time.max)


def reporte_pagos(db: Session, inicio: datetime, fin: datetime,
                  user_id: int | None = None) -> dict:
    q = db.query(TallerCierreJornada).filter(
        TallerCierreJornada.fecha >= inicio, TallerCierreJornada.fecha <= fin)
    if user_id:
        q = q.filter(TallerCierreJornada.user_id == user_id)
    cierres = q.order_by(TallerCierreJornada.fecha.desc()).all()
    if not cierres:
        return {"por_trabajador": [], "detalle": [], "total": 0.0,
                "cierre_ids": [], "inicio": inicio, "fin": fin}
    users = {u.id: u for u in db.query(User).filter(
        User.id.in_({c.user_id for c in cierres})).all()}
    tareas = {t.id: t for t in db.query(TallerTarea).all()}
    dets = db.query(TallerCierreDetalle).filter(
        TallerCierreDetalle.cierre_id.in_([c.id for c in cierres])).all()
    por_trab: dict[int, dict] = {}
    for c in cierres:
        u = users.get(c.user_id)
        nombre = u.full_name if u else f"usuario#{c.user_id}"
        agg = por_trab.setdefault(c.user_id, {"nombre": nombre, "total": 0.0, "jornadas": 0})
        agg["total"] += float(c.total_pago or 0)
        agg["jornadas"] += 1
    detalle = []
    cmap = {c.id: c for c in cierres}
    for d in dets:
        c = cmap[d.cierre_id]
        u = users.get(c.user_id)
        detalle.append({
            "fecha": c.fecha, "trabajador": u.full_name if u else f"usuario#{c.user_id}",
            "producto": c.producto_referencia,
            "tarea": tareas[d.tarea_id].descripcion if d.tarea_id in tareas else f"tarea#{d.tarea_id}",
            "tarifa": float(d.tarifa_aplicada or 0),
        })
    detalle.sort(key=lambda r: r["fecha"], reverse=True)
    total = round(sum(a["total"] for a in por_trab.values()), 2)
    for a in por_trab.values():
        a["total"] = round(a["total"], 2)
    return {"por_trabajador": sorted(por_trab.values(), key=lambda a: -a["total"]),
            "detalle": detalle, "total": total,
            "cierre_ids": [c.id for c in cierres], "inicio": inicio, "fin": fin}
