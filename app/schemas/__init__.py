from datetime import date, datetime
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=6)
    role: str = "ventas"


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class ClientIn(BaseModel):
    nombre: str
    apellidos: str
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    fecha_nacimiento: date | None = None
    notas: str | None = None
    vip: bool = False


class MeasurementIn(BaseModel):
    client_id: int
    tipo_prenda: str = "saco"
    postura: str | None = None
    observaciones: str | None = None
    cuello: float | None = None
    hombro: float | None = None
    sisa: float | None = None
    pecho: float | None = None
    cintura_saco: float | None = None
    cadera: float | None = None
    largo_manga: float | None = None
    ancho_manga: float | None = None
    largo_espalda: float | None = None
    largo_saco: float | None = None
    caida_hombro: float | None = None
    cintura_pantalon: float | None = None
    cadera_pantalon: float | None = None
    tiro: float | None = None
    muslo: float | None = None
    rodilla: float | None = None
    tobillo: float | None = None
    largo_pantalon: float | None = None


class FabricIn(BaseModel):
    codigo: str
    nombre: str
    composicion: str | None = None
    color: str | None = None
    proveedor: str | None = None
    ancho_m: float = 1.5
    precio_metro: float = 0.0
    stock_metros: float = 0.0
    stock_minimo: float = 10.0


class SupplyIn(BaseModel):
    codigo: str
    nombre: str
    tipo: str = "avío"
    unidad: str = "pza"
    stock: float = 0.0
    stock_minimo: float = 20.0
    costo_unitario: float = 0.0


class MovementIn(BaseModel):
    item_tipo: str  # fabric | supply
    item_id: int
    cantidad: float
    tipo: str
    motivo: str = ""


class OrderIn(BaseModel):
    client_id: int
    fecha_entrega: date | None = None
    prioridad: str = "normal"
    total: float = 0.0
    anticipo: float = 0.0


class GarmentIn(BaseModel):
    order_id: int
    tipo: str
    tela_id: int | None = None
    measurement_id: int | None = None
    precio: float = 0.0


class WorkLogIn(BaseModel):
    garment_id: int
    operation_id: int
    minutos_reales: float
    incidencia: str | None = None


class AppointmentIn(BaseModel):
    client_id: int
    order_id: int | None = None
    tipo: str = "PRIMERA_PRUEBA"
    inicio: datetime
    fin: datetime
    notas: str | None = None


class PaymentIn(BaseModel):
    order_id: int
    monto: float
    metodo: str = "efectivo"
