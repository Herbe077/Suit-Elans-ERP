"""Aprobación automática B2B a EN_PRODUCCION (corporativos bypassan adelanto)."""
from app.models.client import Client
from app.models.company import Company
from app.models.crm import Quotation, QuotationLine
from app.models.order import Order
from app.models.ventas import OrdenVenta
from app.services import crm as crm_svc
from app.services import ventas as ventas_svc


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _persona(db, email="b2c@t.pe", corp=False):
    c = db.query(Client).filter(Client.email == email).first()
    if not c:
        c = Client(nombre="Nat", apellidos="Ural", email=email,
                   es_corporativo=corp)
        db.add(c)
        db.commit()
        db.refresh(c)
    else:
        c.es_corporativo = corp
        db.commit()
    return c


def _empresa(db, ruc="20999000111"):
    e = db.query(Company).filter(Company.ruc == ruc).first()
    if not e:
        e = Company(nombre_comercial="Corp SA", ruc=ruc)
        db.add(e)
        db.commit()
        db.refresh(e)
    return e


def _order(db, folio, **kw):
    from app.services.orders import next_folio  # noqa
    o = Order(folio=folio, estado="cotizado", total=1000.0, **kw)
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def test_b2b_salta_adelanto_y_b2c_no():
    db = _db()
    emp = _empresa(db)
    b2c = _persona(db, "b2c@t.pe")
    o_b2b = _order(db, "SE-CORP-1", company_id=emp.id)
    o_b2c = _order(db, "SE-CORP-2", client_id=b2c.id)
    assert ventas_svc.confirmar_si_corresponde(db, o_b2b) is True
    assert db.get(Order, o_b2b.id).estado == "en_produccion"
    assert ventas_svc.confirmar_si_corresponde(db, o_b2c) is False
    assert db.get(Order, o_b2c.id).estado == "cotizado"
    db.close()


def test_flag_cliente_corporativo_tambien_autoaprueba():
    db = _db()
    c = _persona(db, "b2bflag@t.pe", corp=True)
    o = _order(db, "SE-CORP-3", client_id=c.id)
    assert ventas_svc.es_pedido_corporativo(db, o) is True
    ventas_svc.aprobar_produccion(db, o)
    assert db.get(Order, o.id).estado == "en_produccion"
    db.close()


def test_convertir_cotizacion_corporativa_va_a_produccion():
    db = _db()
    emp = _empresa(db, "20999000222")
    q = Quotation(folio="COT-CORP-1", company_id=emp.id, estado="aprobada",
                  total=500.0)
    db.add(q)
    db.commit()
    db.refresh(q)
    o = crm_svc.convert_to_order(db, q.id, None)
    assert o.estado == "en_produccion"
    assert db.get(Quotation, q.id).estado == "convertida"
    db.close()


def test_convertir_cotizacion_b2c_sigue_confirmado():
    db = _db()
    c = _persona(db, "b2c2@t.pe")
    q = Quotation(folio="COT-B2C-1", client_id=c.id, estado="aprobada",
                  total=500.0)
    db.add(q)
    db.commit()
    db.refresh(q)
    o = crm_svc.convert_to_order(db, q.id, None)
    assert o.estado == "confirmado"
    db.close()


def test_crear_orden_endpoint_corporativa(client, auth_cookies):
    db = _db()
    c = _persona(db, "b2bendpoint@t.pe", corp=True)
    cid = c.id
    db.close()
    r = client.post("/ventas/ordenes",
                    data={"client_id": str(cid), "concepto": "Terno corp",
                          "total": "1200", "garment_tipo": "saco"},
                    cookies=auth_cookies, follow_redirects=False)
    assert r.status_code == 303
    db = _db()
    o = db.query(Order).filter(Order.concepto == "Terno corp").order_by(
        Order.id.desc()).first()
    assert o is not None and o.estado == "en_produccion"
    ov = db.query(OrdenVenta).filter(
        OrdenVenta.legacy_order_id == o.id).first()
    assert ov is not None and ov.estado == "EN_PRODUCCION"
    db.close()
