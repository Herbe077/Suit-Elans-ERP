"""Clientes corporativos: empresas, contactos y listas de precio."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre_comercial: Mapped[str] = mapped_column(String(160), index=True)
    razon_social: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ruc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    direccion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    distrito: Mapped[str | None] = mapped_column(String(100), nullable=True)
    clasificacion: Mapped[str] = mapped_column(String(20), default="Nuevo",
                                               server_default="Nuevo")
    descuento_pct: Mapped[float] = mapped_column(Float, default=0.0)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def doc_label(self) -> str:
        return f"RUC {self.ruc}" if self.ruc else ""


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    cargo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    es_principal: Mapped[bool] = mapped_column(Boolean, default=False)


class PriceList(Base):
    __tablename__ = "price_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), unique=True)
    descuento_pct: Mapped[float] = mapped_column(Float, default=0.0)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
