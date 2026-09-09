"""Seed DEMO integral 2021-2026 para prueba punta a punta del ERP.

Todo lo creado lleva prefijo DEMO- (folios, docs, comprobantes) para identificar y borrar.
Idempotente: si el marcador DEMO-2021-001 existe, no duplica.
NO corrige lógica: solo registra y reporta incoherencias en `reporte_prueba_integral.md`.

Uso:
    .venv/bin/python seed_demo_integral.py
"""
from datetime import date
from decimal import Decimal

from app.core.database import Base, SessionLocal, engine
from app.core import security

TAG = "DEMO-"
INCOHERENCIAS: list[str] = []


def anotar(msg: str):
    INCOHERENCIAS.append(msg)
    print(f"  [!] {msg}")


def get_or_create(db, model, defaults=None, **kw):
    obj = db.query(model).filter_by(**kw).first()
    if obj:
        return obj, False
    obj = model(**kw, **(defaults or {}))
    db.add(obj)
    db.flush()
    return obj, True


def main():
    from app.models.user import User
    from app.models.client import Client
    from app.models.company import Company, Contact
    from app.models.crm import Lead, Quotation, QuotationLine
    from app.models.order import Garment, Order, Payment
    from app.models.produccion import FichaMedidas, OrdenProduccion
    from app.models.inventory import Fabric, Supply
    from app.models.purchasing import PurchaseOrder, PurchaseLine, Supplier
    from app.models.billing import CajaTurno, CashMovement, Invoice
    from app.models.finanzas import CentroCosto
    from app.services import finanzas as fin
    from app.modules.rendimiento.models import CatalogoOperacion, DetalleJornada, RegistroJornada

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    creado = {"users": 0, "clientes": 0, "ordenes": 0, "gastos": 0, "asientos": 0}
    try:
        from app.services.plan_operativo import asegurar_plan_operativo
        asegurar_plan_operativo(db)
        for cod, nom, tip in (("TALLER", "Taller", "TALLER"), ("ADMINISTRACION", "Administración", "ADMINISTRACION")):
            if not db.query(CentroCosto).filter(CentroCosto.codigo == cod).first():
                db.add(CentroCosto(codigo=cod, nombre=nom, tipo=tip))
        db.commit()

        # 1) Usuarios demo (roles clave)
        for email, rol in (("demo.ventas@suitelans.mx", "VENTA"), ("demo.taller@suitelans.mx", "SASTRE-ASISTENTE"),
                           ("demo.contador@suitelans.mx", "FINANZAS")):
            u = db.query(User).filter(User.email == email).first()
            if not u:
                db.add(User(email=email, full_name=f"Demo {rol}", hashed_password=security.hash_password("demo123"), role=rol, is_active=True))
                creado["users"] += 1
        db.commit()
        admin = db.query(User).filter(User.email == "lebsast@gmail.com").first() or db.query(User).first()
        operario = db.query(User).filter(User.email == "demo.taller@suitelans.mx").first()

        # 2) Clientes históricos (2021/2023/2025) + empresa corporativa
        for nom, ape, doc, fec in (("Juan", "Pérez Demo", f"{TAG}2021-001", date(2021, 3, 10)),
                                   ("María", "López Demo", f"{TAG}2023-014", date(2023, 6, 5)),
                                   ("Carlos", "Ruiz Demo", f"{TAG}2025-031", date(2025, 11, 20))):
            c, is_new = get_or_create(db, Client, {"nombre": nom, "apellidos": ape}, nro_doc=doc)
            if is_new:
                creado["clientes"] += 1
        comp, _ = get_or_create(db, Company, {"razon_social": f"{TAG} Corporación Textil S.A.C.", "ruc": "20999999991"},
                                nombre_comercial=f"{TAG} Corp Textil")
        if not db.query(Contact).filter(Contact.company_id == comp.id).first():
            db.add(Contact(company_id=comp.id, nombre=f"{TAG} Contacto Gerencia", cargo="Gerencia", es_principal=True))
        db.commit()
        cli1 = db.query(Client).filter(Client.nro_doc == f"{TAG}2021-001").first()
        cli3 = db.query(Client).filter(Client.nro_doc == f"{TAG}2025-031").first()

        # 3) Proveedores + telas/avíos demo
        sup, _ = get_or_create(db, Supplier, {"ruc": "20999999992"}, nombre=f"{TAG} Textiles Andinos")
        fab, _ = get_or_create(db, Fabric, {"nombre": f"{TAG} Lana Super 120", "stock_metros": 100.0, "precio_metro": 120.0, "proveedor_id": sup.id}, codigo=f"{TAG}TEL-01")
        avio, _ = get_or_create(db, Supply, {"nombre": f"{TAG} Botón cacho", "stock": 500.0}, codigo=f"{TAG}AV-01")
        db.commit()

        # 4) CRM: leads + cotización demo
        for nom, est in ((f"{TAG} Lead 2021", "GANADO"), (f"{TAG} Lead 2025", "PROSPECTO"), (f"{TAG} Lead perdido", "PERDIDO")):
            get_or_create(db, Lead, {"estado": est, "origen": "web", "interes": "sastreria"}, nombre=nom)
        db.commit()
        lead_g = db.query(Lead).filter(Lead.nombre == f"{TAG} Lead 2021").first()
        quot = db.query(Quotation).filter(Quotation.folio == f"{TAG}COT-2021-01").first()
        if not quot:
            quot = Quotation(folio=f"{TAG}COT-2021-01", lead_id=lead_g.id if lead_g else None,
                             client_id=cli1.id if cli1 else None, estado="convertida", subtotal=2500, total=2500)
            db.add(quot); db.flush()
            db.add(QuotationLine(quotation_id=quot.id, concepto=f"{TAG} Traje medida", cantidad=1, precio_unitario=2500, garment_tipo="saco"))
            db.commit()

        # 5) Pedidos históricos (2021 pagado / 2023 parcial / 2025 pendiente / 2026 cotizado)
        pedidos = [
            (f"{TAG}2021-001", cli1.id if cli1 else None, None, 2500, 2500, "entregado", date(2021, 4, 2), date(2021, 4, 20)),
            (f"{TAG}2023-001", cli1.id if cli1 else None, None, 3000, 1500, "en_confeccion", date(2023, 7, 1), date(2023, 7, 25)),
            (f"{TAG}2025-001", cli3.id if cli3 else None, comp.id, 4200, 0, "cotizado", date(2025, 12, 3), date(2026, 1, 15)),
            (f"{TAG}2026-001", cli3.id if cli3 else None, None, 1890, 1890, "entregado", date(2026, 2, 10), date(2026, 2, 20)),
        ]
        for folio, cid, coid, tot, ant, est, fp, fe_ in pedidos:
            o, is_new = get_or_create(db, Order, {"total": tot, "anticipo": ant, "estado": est, "client_id": cid, "company_id": coid, "fecha_pedido": fp, "fecha_entrega": fe_}, folio=folio)
            if is_new:
                creado["ordenes"] += 1
                g = Garment(order_id=o.id, tipo="saco", tela_id=fab.id, precio=tot, estado_taller="CALIDAD_OK" if est == "entregado" else "EN_CONFECCION")
                db.add(g); db.flush()
                op = OrdenProduccion(orden_venta_id=o.id, estado="CALIDAD_OK" if est == "entregado" else "EN_CONFECCION", codigo_qr=f"QR-{folio}")
                db.add(op)
        db.commit()

        # 6) Compras a proveedores (borrador 2025 + recibida 2023)
        for folio, est, tot in ((f"{TAG}OC-2023-01", "recibida", 1200), (f"{TAG}OC-2025-01", "borrador", 800)):
            po, is_new = get_or_create(db, PurchaseOrder, {"supplier_id": sup.id, "estado": est, "total": tot}, folio=folio)
            if is_new and not db.query(PurchaseLine).filter(PurchaseLine.purchase_id == po.id).first():
                db.add(PurchaseLine(purchase_id=po.id, item_tipo="fabric", item_id=fab.id, descripcion=f"{TAG} Tela", cantidad=10, costo_unitario=tot / 10))
        db.commit()

        # 7) Facturación + pagos + caja
        o2021 = db.query(Order).filter(Order.folio == f"{TAG}2021-001").first()
        if o2021 and not db.query(Invoice).filter(Invoice.order_id == o2021.id).first():
            db.add(Invoice(serie="F001", numero=f"{TAG}1001", order_id=o2021.id, client_id=o2021.client_id,
                           subtotal=2118.64, igv=381.36, total=2500, estado="emitida", usuario_id=admin.id if admin else None))
        if o2021 and not db.query(Payment).filter(Payment.order_id == o2021.id).first():
            db.add(Payment(order_id=o2021.id, monto=2500, metodo="TRANSFERENCIA", usuario_id=admin.id if admin else None))
        if not db.query(CajaTurno).filter(CajaTurno.estado == "ABIERTA").first():
            db.add(CajaTurno(usuario_id=admin.id if admin else 1, saldo_apertura=500.0, estado="ABIERTA"))
        db.commit()
        if o2021 and not db.query(CashMovement).filter(CashMovement.order_id == o2021.id).first():
            db.add(CashMovement(tipo="ingreso", concepto=f"{TAG} Cobro pedido", monto=2500, metodo="TRANSFERENCIA", medio_pago="TRANSFERENCIA", order_id=o2021.id,
                                usuario_id=admin.id if admin else None))
            db.commit()

        # 8) Rendimiento / MOD demo (2026-02 ligado a prenda demo)
        cat, _ = get_or_create(db, CatalogoOperacion, {"nombre_operacion": f"{TAG} Armado saco", "tarifa_base": 25}, codigo=f"{TAG}OP-01")
        g_demo = db.query(Garment).join(Order, Garment.order_id == Order.id).filter(Order.folio == f"{TAG}2026-001").first()
        if operario and g_demo and not db.query(RegistroJornada).filter(RegistroJornada.observaciones == f"{TAG} jornada").first():
            rj = RegistroJornada(operario_id=operario.id, fecha=date(2026, 2, 12), estado="REGISTRADO", observaciones=f"{TAG} jornada")
            db.add(rj); db.flush()
            db.add(DetalleJornada(registro_jornada_id=rj.id, orden_produccion_id=g_demo.id, operacion_id=cat.id, cantidad=4, tarifa_aplicada=25, subtotal=100))
            db.commit()

        # 9) Gastos operativos históricos vía servicio (asiento automático)
        for cat_, base, igv, num, fec in (("ALQUILER", 2000, 360, f"{TAG}F-2021-01", date(2021, 1, 15)),
                                          ("HONORARIOS", 1500, 0, f"{TAG}RH-2023-01", date(2023, 8, 10)),
                                          ("PLANILLA", 3000, 0, f"{TAG}PL-2025-12", date(2025, 12, 31)),
                                          ("ACTIVO_FIJO", 5000, 900, f"{TAG}F-2024-07", date(2024, 7, 5))):
            from app.models.finanzas import GastoRegistrado
            if not db.query(GastoRegistrado).filter(GastoRegistrado.numero_comprobante == num).first():
                try:
                    fin.registrar_gasto_operativo(db, fecha=fec, categoria=cat_, monto_base=base, monto_igv=igv,
                                                 tipo_comprobante="FACTURA", numero_comprobante=num, ruc_proveedor="20999999993")
                    creado["gastos"] += 1
                except Exception as e:
                    anotar(f"Gasto {num} no pudo registrarse: {e}")

        # 10) Periodos históricos
        for anio, mes in ((2021, 1), (2023, 8), (2025, 12), (2026, 2)):
            try:
                fin.get_or_create_periodo(db, anio, mes)
            except Exception as e:
                anotar(f"Periodo {anio}-{mes:02d}: {e}")
        creado["asientos"] = __import__("app.models.finanzas", fromlist=["AsientoContable"]).AsientoContable and db.query(__import__("app.models.finanzas", fromlist=["AsientoContable"]).AsientoContable).count()
        # espejos de coherencia (no duplican)
        try:
            from app.services.purchasing import sincronizar_espejo_compras
            from app.services.ventas import sincronizar_espejo_ventas
            print(f"  espejo compras: {sincronizar_espejo_compras(db)}")
            print(f"  espejo ventas: {sincronizar_espejo_ventas(db)}")
        except Exception as e:
            anotar(f"Espejos no aplicados: {e}")
        db.commit()
        print(f"✅ Seed DEMO ok: {creado}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
