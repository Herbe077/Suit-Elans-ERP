"""Personal y Contratos: sastres y operarios con perfil tributario para RxH."""
from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


PUESTOS = ("SASTRE_MAESTRO", "PANTALONERO", "CHALEQUERO", "CORTADOR")
TIPOS_CONTRATO = ("DESTAJO_4TA", "PLANILLA_5TA")
REGIMENES_LABORALES = ("General", "MYPE Micro", "MYPE Pequeña")
# Códigos de sistema de pensiones (tasas en parámetros laborales).
SISTEMAS_PENSION = ("ONP", "AFP_INTEGRA", "AFP_HABITAT", "AFP_PRIMA",
                    "AFP_PROFUTURO")

class Empleado(Base):
    """Ficha de personal: datos base + tributario (RxH 4ta) + documentos."""

    __tablename__ = "empleados"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombres: Mapped[str] = mapped_column(String(80))
    apellidos: Mapped[str] = mapped_column(String(80))
    dni: Mapped[str | None] = mapped_column(String(15), nullable=True, unique=True)
    ruc: Mapped[str | None] = mapped_column(String(11), nullable=True, unique=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    puesto: Mapped[str] = mapped_column(String(20), default="SASTRE_MAESTRO")
    tipo_contrato: Mapped[str] = mapped_column(String(20), default="DESTAJO_4TA")
    aplica_retencion_8: Mapped[bool] = mapped_column(Boolean, default=True)
    cv_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contrato_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"),
                                               nullable=True, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Planilla mensual.
    sueldo_basico: Mapped[float] = mapped_column(Float, default=0.0)
    asignacion_familiar: Mapped[bool] = mapped_column(Boolean, default=False)
    sistema_pensiones: Mapped[str] = mapped_column(String(20), default="ONP")
    regimen_laboral: Mapped[str] = mapped_column(String(20), default="General")

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres or ''} {self.apellidos or ''}".strip()

    @property
    def documento(self) -> str | None:
        return self.ruc or self.dni


def ensure_empleados_table(db) -> None:
    """Crea la tabla empleados si falta (idempotente, agnóstico PG/SQLite).

    Cubre BDs donde alembic nunca corrió (sin tabla alembic_version).
    No usa commit propio: acompaña la transacción del llamante.
    """
    try:
        Empleado.__table__.create(db.get_bind(), checkfirst=True)
    except Exception:
        pass


class PlanillaCabecera(Base):
    """Procesamiento mensual de planilla (una fila por año-mes)."""

    __tablename__ = "planilla_cabecera"

    id: Mapped[int] = mapped_column(primary_key=True)
    anio: Mapped[int] = mapped_column(index=True)
    mes: Mapped[int] = mapped_column(index=True)
    estado: Mapped[str] = mapped_column(String(20), default="PROCESADA", index=True)
    total_bruto: Mapped[float] = mapped_column(Float, default=0.0)
    total_descuento: Mapped[float] = mapped_column(Float, default=0.0)
    total_neto: Mapped[float] = mapped_column(Float, default=0.0)
    total_essalud: Mapped[float] = mapped_column(Float, default=0.0)
    gasto_id: Mapped[int | None] = mapped_column(
        ForeignKey("gastos_registrados.id"), nullable=True)
    asiento_id: Mapped[int | None] = mapped_column(
        ForeignKey("asientos_contables.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanillaDetalle(Base):
    """Desglose por empleado de una planilla procesada."""

    __tablename__ = "planilla_detalle"

    id: Mapped[int] = mapped_column(primary_key=True)
    cabecera_id: Mapped[int] = mapped_column(
        ForeignKey("planilla_cabecera.id"), index=True)
    empleado_id: Mapped[int | None] = mapped_column(
        ForeignKey("empleados.id"), nullable=True)
    nombres: Mapped[str] = mapped_column(String(160), default="")
    sueldo_basico: Mapped[float] = mapped_column(Float, default=0.0)
    asignacion_familiar: Mapped[float] = mapped_column(Float, default=0.0)
    total_bruto: Mapped[float] = mapped_column(Float, default=0.0)
    fondo_pension: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pension_pct: Mapped[float] = mapped_column(Float, default=0.0)
    descuento_pension: Mapped[float] = mapped_column(Float, default=0.0)
    neto_pagar: Mapped[float] = mapped_column(Float, default=0.0)
    essalud: Mapped[float] = mapped_column(Float, default=0.0)
