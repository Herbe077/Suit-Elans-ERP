from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Fabric(Base):
    """Rollo/tela en almacén. stock_metros es el disponible real."""

    __tablename__ = "fabrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    composicion: Mapped[str | None] = mapped_column(String(160), nullable=True)
    color: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proveedor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ancho_m: Mapped[float] = mapped_column(Float, default=1.5)
    precio_metro: Mapped[float] = mapped_column(Float, default=0.0)
    stock_metros: Mapped[float] = mapped_column(Float, default=0.0)
    stock_minimo: Mapped[float] = mapped_column(Float, default=10.0)
    stock_reservado: Mapped[float] = mapped_column(Float, default=0.0)
    # Para sincronía con ProductoInsumo spec: proveedor habitual
    proveedor_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)

    @property
    def stock_disponible(self) -> float:
        return round(self.stock_metros - (self.stock_reservado or 0), 2)

    @property
    def stock_fisico(self) -> float:
        return self.stock_metros


class Supply(Base):
    """Avíos: botones, hilos, cierres, entretelas, hombreras..."""

    __tablename__ = "supplies"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    tipo: Mapped[str] = mapped_column(String(60), default="avío")
    unidad: Mapped[str] = mapped_column(String(20), default="pza")
    stock: Mapped[float] = mapped_column(Float, default=0.0)
    stock_minimo: Mapped[float] = mapped_column(Float, default=20.0)
    costo_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    stock_reservado: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def stock_disponible(self) -> float:
        return round(self.stock - (self.stock_reservado or 0), 2)

    @property
    def stock_fisico(self) -> float:
        return self.stock


class StockMovement(Base):
    """Kardex unificado telas + avíos (auditable)."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_tipo: Mapped[str] = mapped_column(String(20))  # fabric | supply
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    cantidad: Mapped[float] = mapped_column(Float)  # + entrada, - salida
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    motivo: Mapped[str] = mapped_column(String(255), default="")
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def _ensure_inventory_columns():
    try:
        from sqlalchemy import inspect, text
        from app.core.database import engine
        insp = inspect(engine)
        cols_f = {c["name"] for c in insp.get_columns("fabrics")}
        with engine.begin() as conn:
            if "stock_reservado" not in cols_f:
                conn.execute(text("ALTER TABLE fabrics ADD COLUMN stock_reservado FLOAT DEFAULT 0"))
            if "proveedor_id" not in cols_f:
                conn.execute(text("ALTER TABLE fabrics ADD COLUMN proveedor_id INTEGER"))
            cols_s = {c["name"] for c in insp.get_columns("supplies")}
            if "stock_reservado" not in cols_s:
                conn.execute(text("ALTER TABLE supplies ADD COLUMN stock_reservado FLOAT DEFAULT 0"))
    except Exception:
        pass

try:
    _ensure_inventory_columns()
except Exception:
    pass
