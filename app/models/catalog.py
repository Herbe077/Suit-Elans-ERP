"""Línea comercial: colecciones, productos y variantes por talla (SKU con stock)."""
from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True)
    temporada: Mapped[str | None] = mapped_column(String(60), nullable=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    linea: Mapped[str] = mapped_column(String(20), default="comercial")  # medida|comercial
    collection_id: Mapped[int | None] = mapped_column(ForeignKey("collections.id"), nullable=True)
    precio_base: Mapped[float] = mapped_column(Float, default=0.0)


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    talla: Mapped[str] = mapped_column(String(10), default="M")
    sku: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    stock: Mapped[float] = mapped_column(Float, default=0.0)
    stock_minimo: Mapped[float] = mapped_column(Float, default=5.0)
    precio: Mapped[float] = mapped_column(Float, default=0.0)
    # Costo unitario del activo (valorización Kardex PCGE Cta 23). El precio
    # es de venta; el costo refleja el valor real del inventario.
    costo_unitario: Mapped[float] = mapped_column(Float, default=0.0)
