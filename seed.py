"""Seed inicial: admin + SAM + datos base (empresa, catálogo, proveedor, lead demo)."""
from app.core import security
from app.core.config import settings
import os
from app.core.constants import DEFAULT_OPERATIONS
from app.core.database import Base, SessionLocal, engine
from app.models.catalog import Collection, Product, ProductVariant
from app.models.company import Company, Contact, PriceList
from app.models.crm import Lead
from app.models.inventory import Fabric, Supply
from app.models.order import Operation
from app.models.purchasing import Supplier
from app.models.user import User

import logging
log = logging.getLogger("suitelans.seed")

Base.metadata.create_all(bind=engine)
db = SessionLocal()

admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
admin_password = os.environ.get("ADMIN_PASSWORD")
if admin_email and admin_password and not db.query(User).filter(User.email == admin_email).first():
    db.add(User(email=admin_email, full_name=os.environ.get("ADMIN_NAME", "Administrador"),
                hashed_password=security.hash_password(admin_password), role=os.environ.get("ADMIN_ROLE", "ADMIN")))
    log.info("Usuario administrador creado")
elif not db.query(User).first():
    log.warning("No se creó administrador: define ADMIN_EMAIL y ADMIN_PASSWORD")

for op in DEFAULT_OPERATIONS:
    if not db.query(Operation).filter(Operation.codigo == op["codigo"]).first():
        db.add(Operation(**op))
log.info("Operaciones SAM verificadas")

from app.services import taller_cierre as taller_svc
n = taller_svc.seed_tareas(db)
log.info("Tareas de cierre de jornada verificadas (%s nuevas)", n)


def get_or_create(model, defaults: dict | None = None, **kw):
    obj = db.query(model).filter_by(**kw).first()
    if not obj:
        obj = model(**kw, **(defaults or {}))
        db.add(obj)
        db.flush()
    return obj


get_or_create(PriceList, nombre="General", defaults={"descuento_pct": 0.0})
get_or_create(PriceList, nombre="Corporativa 10%", defaults={"descuento_pct": 10.0})
comp = get_or_create(Company, nombre_comercial="Empresa Demo S.A.C.",
                     defaults={"razon_social": "Empresa Demo S.A.C.", "ruc": "20601234565",
                               "descuento_pct": 10.0, "distrito": "Miraflores"})
get_or_create(Contact, company_id=comp.id, nombre="Contacto Demo",
              defaults={"cargo": "Gerencia", "es_principal": True})
get_or_create(Supplier, nombre="Textiles Andinos",
              defaults={"ruc": "20111222330", "telefono": "999888777"})
get_or_create(Fabric, codigo="TEL-DEMO01",
              defaults={"nombre": "Lana Super 120 demo", "stock_metros": 50.0,
                        "precio_metro": 120.0, "stock_minimo": 10.0})
get_or_create(Supply, codigo="AV-DEMO01",
              defaults={"nombre": "Botón cacho demo", "stock": 200.0})
col = get_or_create(Collection, nombre="Esencial 2026", defaults={"temporada": "2026"})
prod = get_or_create(Product, codigo="CAM-001",
                     defaults={"nombre": "Camisa Essential", "linea": "comercial",
                               "collection_id": col.id, "precio_base": 189.0})
for talla, sku in (("S", "CAM-001-S"), ("M", "CAM-001-M"), ("L", "CAM-001-L")):
    get_or_create(ProductVariant, sku=sku,
                  defaults={"product_id": prod.id, "talla": talla,
                            "stock": 20.0, "precio": 189.0})
get_or_create(Lead, nombre="Lead Demo",
              defaults={"telefono": "999111222", "origen": "web", "interes": "sastreria"})
from app.services import config as config_svc
config_svc.seed_defaults(db)
db.commit()
# Finanzas: PCGE mínimo + centros de costo base (idempotente)
try:
    from app.services import finanzas as finanzas_svc
    from app.models.finanzas import CentroCosto
    from app.services.plan_operativo import asegurar_plan_operativo
    asegurar_plan_operativo(db)
    db.commit()
    for codigo, nombre, tipo in (
        ("TALLER", "Taller", "TALLER"),
        ("SHOWROOM", "Showroom", "SHOWROOM"),
        ("ADMINISTRACION", "Administración", "ADMINISTRACION"),
        ("COMERCIAL", "Comercial", "COMERCIAL"),
    ):
        if not db.query(CentroCosto).filter(CentroCosto.codigo == codigo).first():
            db.add(CentroCosto(codigo=codigo, nombre=nombre, tipo=tipo))
    db.commit()
    print("PCGE y centros de costo verificados")
except Exception as e:
    db.rollback()
    print(f"Aviso seed finanzas: {e}")
print("Datos base verificados (listas de precio, empresa, catálogo, proveedor, lead demo)")
print("Configuración de sede verificada")
