"""CRM: leads y cotizaciones con trazabilidad hasta el pedido."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), index=True)
    telefono: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    empresa: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tipo_cliente: Mapped[str] = mapped_column(String(20), default="individual")  # individual|corporativo
    origen: Mapped[str] = mapped_column(String(30), default="visita_tienda")
    interes: Mapped[str] = mapped_column(String(60), default="sastreria")  # sastreria|comercial|corporativo
    estado: Mapped[str] = mapped_column(String(20), default="PROSPECTO", index=True)
    canal: Mapped[str | None] = mapped_column(String(30), nullable=True)  # instagram|recomendacion|...
    motivo_perdida: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendedor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Interaccion(Base):
    """Log de interacciones: llamadas, WhatsApp, notas del asesor."""

    __tablename__ = "interacciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True,
                                                index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True,
                                                  index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True,
                                                   index=True)
    tipo: Mapped[str] = mapped_column(String(20), default="nota")  # llamada|whatsapp|nota|visita
    texto: Mapped[str] = mapped_column(Text)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Quotation(Base):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    folio: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    vendedor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="borrador", index=True)
    validez_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    descuento_pct: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QuotationLine(Base):
    __tablename__ = "quotation_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id"), index=True)
    concepto: Mapped[str] = mapped_column(String(200))
    categoria: Mapped[str] = mapped_column(String(20), default="prenda_medida")
    garment_tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id"), nullable=True)
    cantidad: Mapped[float] = mapped_column(Float, default=1.0)
    precio_unitario: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def importe(self) -> float:
        return round(self.cantidad * self.precio_unitario, 2)
