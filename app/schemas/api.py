"""Esquemas Pydantic para API v1 (integración web/Odoo)."""
from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LeadIn(BaseModel):
    nombre: str
    telefono: str | None = None
    email: str | None = None
    empresa: str | None = None
    tipo_cliente: str = "individual"
    origen: str = "web"
    interes: str = "sastreria"
    notas: str | None = None


class LeadOut(LeadIn):
    id: int
    estado: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class LeadPatch(BaseModel):
    estado: str | None = None
    notas: str | None = None


class QuotationLineIn(BaseModel):
    concepto: str = Field(min_length=1, max_length=255)
    categoria: str = "prenda_medida"
    garment_tipo: str | None = None
    variant_id: int | None = Field(default=None, gt=0)
    cantidad: float = Field(default=1.0, gt=0)
    precio_unitario: float = Field(default=0.0, ge=0)


class QuotationIn(BaseModel):
    lead_id: int | None = Field(default=None, gt=0)
    client_id: int | None = Field(default=None, gt=0)
    company_id: int | None = Field(default=None, gt=0)
    validez_hasta: date | None = None
    descuento_pct: float = Field(default=0.0, ge=0, le=100)
    notas: str | None = Field(default=None, max_length=1000)
    lineas: list[QuotationLineIn] = Field(default_factory=list, min_length=1)


class QuotationOut(BaseModel):
    id: int
    folio: str
    estado: str
    subtotal: float
    descuento_pct: float
    total: float

    model_config = {"from_attributes": True}


class CompanyIn(BaseModel):
    nombre_comercial: str
    razon_social: str | None = None
    ruc: str | None = None
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    distrito: str | None = None
    clasificacion: str = "Nuevo"
    descuento_pct: float = 0.0

    @field_validator("ruc")
    @classmethod
    def _ruc_valido(cls, v: str | None) -> str | None:
        if v:
            # RUC flexible: normaliza sin bloquear el guardado.
            from app.services.peru import normalizar_ruc
            return normalizar_ruc(v)
        return v


class CompanyOut(CompanyIn):
    id: int

    model_config = {"from_attributes": True}


class ContactIn(BaseModel):
    nombre: str
    cargo: str | None = None
    telefono: str | None = None
    email: str | None = None
    es_principal: bool = False


class ProductIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=60)
    nombre: str = Field(min_length=1, max_length=160)
    linea: str = "comercial"
    collection_id: int | None = Field(default=None, gt=0)
    precio_base: float = Field(default=0.0, ge=0)


class VariantIn(BaseModel):
    talla: str = Field(default="M", min_length=1, max_length=20)
    sku: str = Field(min_length=1, max_length=60)
    stock: float = Field(default=0.0, ge=0)
    stock_minimo: float = Field(default=5.0, ge=0)
    precio: float = Field(default=0.0, ge=0)


class VariantOut(VariantIn):
    id: int
    product_id: int

    model_config = {"from_attributes": True}


class StockAdjustIn(BaseModel):
    variant_id: int = Field(gt=0)
    cantidad: float
    motivo: str = Field(default="", max_length=255)

    @field_validator("cantidad")
    @classmethod
    def cantidad_no_cero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("cantidad no puede ser cero")
        return v


class AppointmentApiIn(BaseModel):
    client_id: int
    tipo: str = "PRIMERA_PRUEBA"
    inicio: datetime
    fin: datetime
    notas: str | None = None


class OrderOut(BaseModel):
    id: int
    folio: str
    estado: str
    canal: str
    total: float
    anticipo: float

    model_config = {"from_attributes": True}


class InvoiceIn(BaseModel):
    serie: str = "B001"
    order_id: int | None = None
    client_id: int | None = None
    company_id: int | None = None
    igv_pct: float = Field(default=18.0, ge=0, le=100)


class InvoiceOut(BaseModel):
    id: int
    serie: str
    numero: str
    subtotal: float
    igv: float
    total: float
    estado: str

    model_config = {"from_attributes": True}


class TesoreriaPagoIn(BaseModel):
    gasto_id: int | None = Field(default=None, gt=0)
    cxp_id: int | None = Field(default=None, gt=0)
    monto: float | None = Field(default=None, gt=0)
    medio_pago: str = Field(default="banco", min_length=1, max_length=30)
    voucher: str | None = Field(default=None, max_length=60)
    permitir_sobregiro: bool = Field(
        default=False,
        description="1041 exige True si no hay saldo; 1011 siempre bloquea sin saldo")

    @field_validator("medio_pago")
    @classmethod
    def normalizar_medio(cls, v: str) -> str:
        from app.services.medios_pago import normalizar_medio
        return normalizar_medio(v)


class TesoreriaPagoOut(BaseModel):
    gasto_id: int | None = None
    cxp_id: int | None = None
    asiento_id: int
    asiento_numero: str
    monto: float
    gasto_estado: str | None = None
    cxp_estado: str | None = None
    advertencia_sobregiro: str | None = None
