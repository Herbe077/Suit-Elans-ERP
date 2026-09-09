from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    garment_id: Mapped[int | None] = mapped_column(ForeignKey("garments.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(30), default="TOMA_MEDIDAS")
    sastre_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    inicio: Mapped[datetime] = mapped_column(DateTime, index=True)
    fin: Mapped[datetime] = mapped_column(DateTime)
    estado: Mapped[str] = mapped_column(String(20), default="PROGRAMADA", index=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
