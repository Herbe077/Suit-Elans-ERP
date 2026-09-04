from datetime import date, datetime
from sqlalchemy import Date, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), index=True)
    apellidos: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tipo_doc: Mapped[str] = mapped_column(String(12), default="DNI")  # DNI|RUC|CE|PASAPORTE
    nro_doc: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    distrito: Mapped[str | None] = mapped_column(String(100), nullable=True)
    clasificacion: Mapped[str] = mapped_column(String(20), default="Nuevo",
                                               server_default="Nuevo")
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    vip: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombre} {self.apellidos}".strip()

    @property
    def doc_label(self) -> str:
        return f"{self.tipo_doc} {self.nro_doc}" if self.nro_doc else ""
