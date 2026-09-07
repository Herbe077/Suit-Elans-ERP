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
    concepto: str
    categoria: str = "prenda_medida"
    garment_tipo: str | None = None
    variant_id: int | None = None
    cantidad: float = 1.0
    precio_unitario: float = 0.0


class QuotationIn(BaseModel):
    lead_id: int | None = None
    client_id: int | None = None
    company_id: int | None = None
    validez_hasta: date | None = None
    descuento_pct: float = 0.0
    notas: str | None = None
    lineas: list[QuotationLineIn] = Field(default_factory=list)


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
    codigo: str
    nombre: str
    linea: str = "comercial"
    collection_id: int | None = None
    precio_base: float = 0.0


class VariantIn(BaseModel):
    talla: str = "M"
    sku: str
    stock: float = 0.0
    stock_minimo: float = 5.0
    precio: float = 0.0


class VariantOut(VariantIn):
    id: int
    product_id: int

    model_config = {"from_attributes": True}


class StockAdjustIn(BaseModel):
    variant_id: int
    cantidad: float  # + entrada / - salida
    motivo: str = ""


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
    igv_pct: float = 18.0


class InvoiceOut(BaseModel):
    id: int
    serie: str
    numero: str
    subtotal: float
    igv: float
    total: float
    estado: str

    model_config = {"from_attributes": True}
