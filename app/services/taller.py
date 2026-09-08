"""Lógica de producción del taller: kanban 7 fases, urgencia, ficha,
pruebas y calidad + automatizaciones entre módulos.

Sin lógica de pagos/destajo (módulo aislado taller_cierre).
"""
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from app.core.constants import KANBAN_MAP, KANBAN_STATES
from app.models.order import ControlCalidad, Garment, Order, PruebaEntalle


def columna(estado: str) -> str:
    return KANBAN_MAP.get(estado, "POR_CORTAR")


def dias_restantes(g: Garment, order: Order | None) -> int | None:
    limite = (g.fecha_limite_entrega if g and g.fecha_limite_entrega
              else order.fecha_entrega if order else None)
    if not limite:
        return None
    return (limite - date.today()).days


def urgencia(dias: int | None) -> tuple[str, str]:
    """(etiqueta, color tailwind)."""
    if dias is None:
        return ("sin fecha", "bg-slate-200 text-slate-600")
    if dias < 0:
        return (f"vencida {abs(dias)}d", "bg-red-600 text-white")
    if dias <= 2:
        return (f"{dias}d", "bg-red-100 text-red-800")
    if dias <= 7:
        return (f"{dias}d", "bg-amber-100 text-amber-800")
    return (f"{dias}d", "bg-emerald-100 text-emerald-800")


def tiene_notas_ajuste(db: Session, gid: int) -> bool:
    for p in db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == gid).all():
        if (p.correcciones or {}) or (p.observaciones_ajuste or "").strip() \
                or (p.notas_sastre or "").strip():
            return True
    return False


def tiene_prueba(db: Session, gid: int) -> bool:
    """Existe al menos una prueba registrada (incluso sin observaciones)."""
    return db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == gid).first() is not None


def puede_pasar_a_confeccion(db: Session, gid: int) -> bool:
    """Permite pasar a EN_CONFECCION si hay prueba con o sin observaciones."""
    return tiene_prueba(db, gid) or tiene_notas_ajuste(db, gid)


def _agendar_prueba(db: Session, g: Garment, order: Order | None) -> None:
    """Automatización: al entrar a EN_PRUEBA se agenda la cita con el cliente
    (si no tiene una futura programada/confirmada)."""
    from app.models.appointment import Appointment
    if not order or not order.client_id:
        return
    futura = db.query(Appointment).filter(
        Appointment.client_id == order.client_id,
        Appointment.inicio >= datetime.now(),
        Appointment.estado.in_(["PROGRAMADA", "CONFIRMADA"])).first()
    if futura:
        return
    n_pruebas = db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == g.id).count()
    inicio = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    db.add(Appointment(
        client_id=order.client_id, order_id=order.id, garment_id=g.id,
        tipo="SEGUNDA_PRUEBA" if n_pruebas else "PRIMERA_PRUEBA",
        sastre_id=g.artesano_id or order.sastre_id,
        inicio=inicio, fin=inicio + timedelta(minutes=30),
        notas=f"Auto-agendada al pasar {order.folio} a EN_PRUEBA"))


def _avisar_ventas(db: Session, g: Garment, order: Order | None,
                   usuario_id: int | None) -> None:
    """Automatización: al llegar a CALIDAD_OK se deja aviso para liquidar saldo."""
    from app.models.crm import Interaccion
    if not order:
        return
    nombre = order.folio
    db.add(Interaccion(
        client_id=order.client_id, company_id=order.company_id,
        tipo="nota", usuario_id=usuario_id,
        texto=f"Prenda {g.tipo} #{g.id} ({nombre}) lista en taller "
              f"(CALIDAD_OK). Coordinar liquidación de saldo y entrega."))


def mover(db: Session, gid: int, nuevo: str, usuario_id: int | None = None) -> Garment:
    if nuevo not in KANBAN_STATES and nuevo != "pausado":
        raise ValueError(f"Estado inválido: {nuevo}")
    g = db.get(Garment, gid)
    if not g:
        raise ValueError("Prenda no encontrada")
    order = db.get(Order, g.order_id)
    if nuevo == "EN_CONFECCION" and not puede_pasar_a_confeccion(db, gid):
        raise ValueError("Registra la prueba de entalle (con o sin observaciones) antes de la costura final")
    if nuevo == "ACABADOS" and not g.paso_confeccion and g.estado_taller != "EN_CONFECCION":
        raise ValueError("La prenda debe pasar por EN_CONFECCION antes de ACABADOS")
    if nuevo == "CALIDAD_OK":
        raise ValueError("Usa el checklist de control de calidad para aprobar")
    anterior = g.estado_taller
    # Integración inventario: al pasar a EN_CORTE descontar físico y liberar reservado
    if nuevo == "EN_CORTE" and anterior != "EN_CORTE":
        if g.tela_id:
            from app.core.constants import CONSUMO_TELA_M
            from app.models.inventory import Fabric
            from app.services.inventory import ensure_producto_for_fabric
            from app.models.inventario import MovimientoKardex
            consumo = CONSUMO_TELA_M.get(g.tipo, 1.5)
            # Legacy Fabric ya fue descontado en reserva, solo liberar si había reservado flag
            # Para spec ProductoInsumo: descontar físico y liberar reservado
            try:
                fab = db.get(Fabric, g.tela_id)
                if fab:
                    prod = ensure_producto_for_fabric(db, fab)
                    # liberar reservado y consumir físico spec
                    if (prod.stock_reservado or 0) >= consumo - 1e-9:
                        prod.stock_reservado = round(max(0, (prod.stock_reservado or 0) - consumo), 2)
                        prod.stock_fisico = round(max(0, (prod.stock_fisico or 0) - consumo), 2)
                    else:
                        # si no había reserva (venta directa), solo fisico
                        prod.stock_fisico = round(max(0, (prod.stock_fisico or 0) - consumo), 2)
                    db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="SALIDA_TALLER",
                                            cantidad=consumo, orden_venta_id=order.id if order else None,
                                            usuario_id=usuario_id, observacion="Consumo EN_CORTE"))
                    # legacy fabric físico ya descontado, asegurar consistencia reservado
                    if hasattr(fab, 'stock_reservado') and (fab.stock_reservado or 0) > 0:
                        fab.stock_reservado = round(max(0, (fab.stock_reservado or 0) - consumo), 2)
            except Exception:
                pass
    g.estado_taller = nuevo
    if nuevo == "EN_CONFECCION":
        g.paso_confeccion = True
    db.commit()
    if nuevo == "EN_PRUEBA" and anterior != "EN_PRUEBA":
        _agendar_prueba(db, g, order)
        db.commit()
    db.refresh(g)
    return g


def registrar_prueba(db: Session, gid: int, numero: int, correcciones: dict,
                     observaciones: str, notas_sastre: str,
                     fotos: list[str]) -> PruebaEntalle:
    if numero not in (1, 2, 3):
        raise ValueError("numero_prueba debe ser 1, 2 o 3")
    g = db.get(Garment, gid)
    if not g:
        raise ValueError("Prenda no encontrada")
    hay_notas = bool(correcciones or (observaciones or "").strip()
                     or (notas_sastre or "").strip())
    p = PruebaEntalle(garment_id=gid, numero_prueba=numero, correcciones=correcciones,
                      observaciones_ajuste=observaciones or None,
                      notas_sastre=notas_sastre or None,
                      fotos=fotos or None, foto_path=(fotos or [None])[0],
                      completada=not hay_notas)
    db.add(p)
    if not hay_notas:
        # Prueba aprobada/sin ajustes: avanza al siguiente paso del taller
        # (confección) para que salga de "Pruebas pendientes".
        if columna(g.estado_taller) in ("POR_CORTAR", "EN_CORTE",
                                        "ARMADO_HILVAN", "EN_PRUEBA"):
            g.estado_taller = "EN_CONFECCION"
            g.paso_confeccion = True
    else:
        # Con observaciones: queda en prueba (habilita el pase manual).
        if numero == 1 and columna(g.estado_taller) in ("POR_CORTAR", "EN_CORTE",
                                                        "ARMADO_HILVAN"):
            g.estado_taller = "EN_PRUEBA"
    db.commit()
    db.refresh(p)
    return p


def confirmar_prueba(db: Session, prueba_id: int) -> PruebaEntalle:
    """Marca una prueba como completada/aprobada y avanza la prenda a
    EN_CONFECCION (siguiente paso del taller). No toca SAM ni kardex."""
    p = db.get(PruebaEntalle, prueba_id)
    if not p:
        raise ValueError("Prueba no encontrada")
    g = db.get(Garment, p.garment_id)
    if not g:
        raise ValueError("Prenda no encontrada")
    p.completada = True
    if columna(g.estado_taller) in ("POR_CORTAR", "EN_CORTE",
                                    "ARMADO_HILVAN", "EN_PRUEBA"):
        g.estado_taller = "EN_CONFECCION"
        g.paso_confeccion = True
    db.commit()
    db.refresh(p)
    return p


def aprobar_calidad(db: Session, gid: int, checks: list[bool], observaciones: str,
                    usuario_id: int | None) -> ControlCalidad:
    from app.core.constants import QC_CHECKLIST
    if len(checks) != len(QC_CHECKLIST) or not all(checks):
        raise ValueError("Los 6 puntos del checklist son obligatorios")
    g = db.get(Garment, gid)
    if not g:
        raise ValueError("Prenda no encontrada")
    cc = ControlCalidad(garment_id=gid, aprobado_por=usuario_id, aprobado=True,
                        checks={name: True for name in QC_CHECKLIST},
                        observaciones=observaciones or None)
    db.add(cc)
    g.estado_taller = "CALIDAD_OK"
    g.paso_confeccion = True
    db.commit()
    _avisar_ventas(db, g, db.get(Order, g.order_id), usuario_id)
    db.commit()
    db.refresh(cc)
    return cc


def codigo_qr(order_folio: str, gid: int) -> str:
    return f"{order_folio}-P{gid:04d}"


def asegurar_qr(db: Session, g: Garment, order: Order | None) -> str:
    if not g.codigo_qr and order:
        g.codigo_qr = codigo_qr(order.folio, g.id)
        db.commit()
    return g.codigo_qr or f"P{g.id:04d}"


def ficha_data(db: Session, gid: int) -> dict:
    """Todo lo que la ficha técnica necesita en una sola lectura."""
    from app.models.client import Client
    from app.models.inventory import Fabric
    from app.models.measurement import Measurement
    from app.models.order import Operation
    from app.models.user import User
    g = db.get(Garment, gid)
    if not g:
        raise ValueError("Prenda no encontrada")
    o = db.get(Order, g.order_id)
    c = db.get(Client, o.client_id) if o and o.client_id else None
    m = db.get(Measurement, g.measurement_id) if g.measurement_id else None
    if not m and c:
        m = db.query(Measurement).filter(Measurement.client_id == c.id).order_by(
            Measurement.id.desc()).first()
    tela = db.get(Fabric, g.tela_id) if g.tela_id else None
    artesano = db.get(User, g.artesano_id) if g.artesano_id else None
    equipo = db.query(User).filter(User.role.in_(["sastre", "taller"]),
                                   User.is_active.is_(True)).order_by(User.full_name).all()
    pruebas = db.query(PruebaEntalle).filter(PruebaEntalle.garment_id == gid).order_by(
        PruebaEntalle.id.desc()).all()
    qc = db.query(ControlCalidad).filter(ControlCalidad.garment_id == gid).order_by(
        ControlCalidad.id.desc()).first()
    ops = db.query(Operation).order_by(Operation.codigo).all()
    return {"g": g, "o": o, "c": c, "m": m, "tela": tela, "artesano": artesano,
            "equipo": equipo, "pruebas": pruebas, "qc": qc, "ops": ops,
            "dias": dias_restantes(g, o),
            "qr": asegurar_qr(db, g, o),
            "con_notas": tiene_notas_ajuste(db, gid)}
