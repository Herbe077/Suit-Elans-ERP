"""Ficha de medidas anatómicas versionada por cliente/prenda."""
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    sastre_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tipo_prenda: Mapped[str] = mapped_column(String(20), default="saco")
    version: Mapped[int] = mapped_column(default=1)
    postura: Mapped[str | None] = mapped_column(String(120), nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Saco (cm)
    cuello: Mapped[float | None] = mapped_column(Float, nullable=True)
    hombro: Mapped[float | None] = mapped_column(Float, nullable=True)
    sisa: Mapped[float | None] = mapped_column(Float, nullable=True)
    pecho: Mapped[float | None] = mapped_column(Float, nullable=True)
    cintura_saco: Mapped[float | None] = mapped_column(Float, nullable=True)
    cadera: Mapped[float | None] = mapped_column(Float, nullable=True)
    largo_manga: Mapped[float | None] = mapped_column(Float, nullable=True)
    ancho_manga: Mapped[float | None] = mapped_column(Float, nullable=True)
    largo_espalda: Mapped[float | None] = mapped_column(Float, nullable=True)
    largo_saco: Mapped[float | None] = mapped_column(Float, nullable=True)
    caida_hombro: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Pantalón (cm)
    cintura_pantalon: Mapped[float | None] = mapped_column(Float, nullable=True)
    cadera_pantalon: Mapped[float | None] = mapped_column(Float, nullable=True)
    tiro: Mapped[float | None] = mapped_column(Float, nullable=True)
    muslo: Mapped[float | None] = mapped_column(Float, nullable=True)
    rodilla: Mapped[float | None] = mapped_column(Float, nullable=True)
    tobillo: Mapped[float | None] = mapped_column(Float, nullable=True)
    largo_pantalon: Mapped[float | None] = mapped_column(Float, nullable=True)
