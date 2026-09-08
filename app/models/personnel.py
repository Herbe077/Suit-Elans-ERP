"""Personal y Contratos: sastres y operarios con perfil tributario para RxH."""
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PUESTOS = ("SASTRE_MAESTRO", "PANTALONERO", "CHALEQUERO", "CORTADOR")
TIPOS_CONTRATO = ("DESTAJO_4TA", "PLANILLA_5TA")


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

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres or ''} {self.apellidos or ''}".strip()

    @property
    def documento(self) -> str | None:
        return self.ruc or self.dni
