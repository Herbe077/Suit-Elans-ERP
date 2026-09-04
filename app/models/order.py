from datetime import date, datetime
from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, String, Text, false, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    folio: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    quotation_id: Mapped[int | None] = mapped_column(ForeignKey("quotations.id"), nullable=True)
    pricelist_id: Mapped[int | None] = mapped_column(ForeignKey("price_lists.id"), nullable=True)
    canal: Mapped[str] = mapped_column(String(20), default="sastreria", index=True)
    sastre_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="cotizado", index=True)
    prioridad: Mapped[str] = mapped_column(String(20), default="normal")
    fecha_pedido: Mapped[date] = mapped_column(Date, default=date.today)
    fecha_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    anticipo: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def saldo(self) -> float:
        return round(self.total - self.anticipo, 2)


class Garment(Base):
    """Cada prenda del pedido (unidad MES a seguir en taller)."""

    __tablename__ = "garments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    tela_id: Mapped[int | None] = mapped_column(ForeignKey("fabrics.id"), nullable=True)
    measurement_id: Mapped[int | None] = mapped_column(ForeignKey("measurements.id"), nullable=True)
    precio: Mapped[float] = mapped_column(Float, default=0.0)
    estado_taller: Mapped[str] = mapped_column(String(20), default="POR_CORTAR", index=True)
    sam_estimado: Mapped[float] = mapped_column(Float, default=0.0)
    sam_real: Mapped[float] = mapped_column(Float, default=0.0)
    artesano_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    diseno: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # diseno: {solapa, bolsillos, forro, respiraderos, botones, notas_diseno}
    tela_reservada: Mapped[bool] = mapped_column(Boolean, default=False,
                                                 server_default=false())
    codigo_qr: Mapped[str | None] = mapped_column(String(60), nullable=True, unique=True,
                                                  index=True)
    fecha_limite_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    paso_confeccion: Mapped[bool] = mapped_column(Boolean, default=False,
                                                  server_default=false())


class Operation(Base):
    """Catálogo de operaciones con SAM estándar."""

    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    tipo_prenda: Mapped[str] = mapped_column(String(20), default="saco")
    sam_minutos: Mapped[float] = mapped_column(Float, default=30.0)


class WorkLog(Base):
    """Fichaje taller: operario registra inicio/fin por operación."""

    __tablename__ = "work_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    garment_id: Mapped[int] = mapped_column(ForeignKey("garments.id"), index=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), index=True)
    operario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    minutos_reales: Mapped[float] = mapped_column(Float, default=0.0)
    estado: Mapped[str] = mapped_column(String(20), default="terminado")
    incidencia: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    monto: Mapped[float] = mapped_column(Float)
    metodo: Mapped[str] = mapped_column(String(40), default="efectivo")
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PruebaEntalle(Base):
    """Reporte de prueba de entalle (Fit Test 1 o 2) sobre una prenda."""

    __tablename__ = "pruebas_entalle"

    id: Mapped[int] = mapped_column(primary_key=True)
    garment_id: Mapped[int] = mapped_column(ForeignKey("garments.id"), index=True)
    numero_prueba: Mapped[int] = mapped_column(default=1)  # 1 | 2 | 3
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    correcciones: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # correcciones: {zona: nota} p. ej. {"hombros": "bajar 0.5cm izq"}
    observaciones_ajuste: Mapped[str | None] = mapped_column(Text, nullable=True)
    notas_sastre: Mapped[str | None] = mapped_column(Text, nullable=True)
    foto_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fotos: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["uploads/fit/..."]
    completada: Mapped[bool] = mapped_column(default=False)


class ControlCalidad(Base):
    """Checklist pre-entrega de 6 puntos aprobado por jefatura de taller."""

    __tablename__ = "controles_calidad"

    id: Mapped[int] = mapped_column(primary_key=True)
    garment_id: Mapped[int] = mapped_column(ForeignKey("garments.id"), index=True)
    aprobado_por: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    aprobado: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
