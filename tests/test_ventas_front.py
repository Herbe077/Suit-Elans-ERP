"""Front-office: turnos, cobro con automatizaciones, bloqueo de entrega y POS."""


def _uid_front() -> int:
    from app.core.database import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    uid = db.query(User).filter(User.email == "front@t.pe").first().id
    db.close()
    return uid


def _front():
    from fastapi.testclient import TestClient
    from app.core import security
    from app.core.database import SessionLocal
    from app.main import app
    from app.models.user import User
    db = SessionLocal()
    u = db.query(User).filter(User.email == "front@t.pe").first()
    if not u:
        u = User(email="front@t.pe", full_name="Front", role="ventas",
                 hashed_password=security.hash_password("x"), is_active=True)
        db.add(u)
        db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "front@t.pe", "password": "x"},
               follow_redirects=False)
    assert r.status_code == 303
    return c, {"suitelans_token": r.cookies.get("suitelans_token")}


def _pedido_con_tela(db):
    from app.models.client import Client
    from app.models.inventory import Fabric
    from app.models.order import Garment, Order
    c = Client(nombre="Front", apellidos="Test")
    db.add(c)
    db.flush()
    f = Fabric(codigo=f"T-FRONT-{c.id}", nombre="Tela front", stock_metros=10.0)
    db.add(f)
    db.flush()
    o = Order(folio=f"SE-FRONT-{c.id}", client_id=c.id, estado="cotizado", total=1000.0)
    db.add(o)
    db.flush()
    g = Garment(order_id=o.id, tipo="saco", tela_id=f.id, precio=1000.0)
    db.add(g)
    db.commit()
    return o.id, g.id, f.id


def test_turno_requerido_y_apertura():
    c, ck = _front()
    from app.core.database import SessionLocal
    from app.models.billing import CajaTurno
    db = SessionLocal()
    oid, _, _ = _pedido_con_tela(db)
    db.close()
    # Sin turno: el cobro se rechaza
    r = c.post("/ventas/cobro", data={"order_id": str(oid), "monto": "500",
                                      "back": "/ventas/ordenes"}, cookies=ck)
    assert "error=turno" in str(r.url)
    # Abrir turno
    r = c.post("/ventas/caja/abrir", data={"saldo_apertura": "100"}, cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    t = db.query(CajaTurno).filter(CajaTurno.estado == "ABIERTA",
                                   CajaTurno.usuario_id == _uid_front()).first()
    assert t and t.saldo_apertura == 100.0
    db.close()
    # Doble apertura se rechaza
    r = c.post("/ventas/caja/abrir", data={"saldo_apertura": "0"}, cookies=ck)
    assert "error=turno" in str(r.url)


def test_anticipo_confirma_y_reserva():
    c, ck = _front()
    from app.core.database import SessionLocal
    from app.models.billing import CashMovement
    from app.models.inventory import Fabric
    from app.models.order import Garment, Order, Payment
    db = SessionLocal()
    oid, gid, fid = _pedido_con_tela(db)
    stock_antes = db.get(Fabric, fid).stock_metros
    db.close()
    # Anticipo 50% (>= mínimo) → confirma + reserva tela + cash con turno
    r = c.post("/ventas/cobro", data={"order_id": str(oid), "monto": "500",
                                      "metodo": "yape", "tipo": "ADELANTO",
                                      "back": "/ventas/ordenes"}, cookies=ck)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    o = db.get(Order, oid)
    assert o.estado == "confirmado" and o.anticipo == 500.0
    g = db.query(Garment).filter(Garment.order_id == oid).first()
    assert g.tela_reservada is True
    assert db.get(Fabric, g.tela_id).stock_metros == round(stock_antes - 2.0, 2)
    mov = db.query(CashMovement).filter(CashMovement.order_id == oid).first()
    assert mov and mov.turno_id is not None
    assert db.query(Payment).filter(Payment.order_id == oid).count() == 1
    # Entrega bloqueada con saldo
    r = c.post(f"/ventas/ordenes/{oid}/entregar", cookies=ck)
    assert "error=saldo" in str(r.url)
    db.close()
    # Saldo final → entrega permitida (requiere prenda lista CALIDAD_OK)
    c.post("/ventas/cobro", data={"order_id": str(oid), "monto": "500",
                                   "tipo": "SALDO_FINAL", "back": "/ventas/ordenes"},
            cookies=ck)
    db = SessionLocal()
    g = db.query(Garment).filter(Garment.order_id == oid).first()
    g.estado_taller = "CALIDAD_OK"
    db.commit()
    db.close()
    r = c.post(f"/ventas/ordenes/{oid}/entregar", cookies=ck)
    db = SessionLocal()
    assert db.get(Order, oid).estado == "entregado"
    db.close()


def test_cierre_con_arqueo_y_pos():
    c, ck = _front()
    from app.core.database import SessionLocal
    from app.models.billing import CajaTurno
    db = SessionLocal()
    t = db.query(CajaTurno).filter(CajaTurno.estado == "ABIERTA",
                                   CajaTurno.usuario_id == _uid_front()).first()
    tid = t.id
    db.close()
    # Teórico = 100 apertura + 1000 cobros
    r = c.post("/ventas/caja/cerrar", data={"saldo_real": "1100"}, cookies=ck)
    db = SessionLocal()
    t = db.get(CajaTurno, tid)
    assert t.estado == "CERRADA" and t.diferencia == 0.0
    db.close()
    # POS 3 pasos con tela + cobro (requiere turno nuevo)
    c.post("/ventas/caja/abrir", data={"saldo_apertura": "0"}, cookies=ck)
    r = c.get("/ventas/pos", cookies=ck)
    assert "PASO 1" in r.text and "PASO 3" in r.text
    db = SessionLocal()
    from app.models.client import Client
    from app.models.inventory import Fabric
    cli = Client(nombre="Pos", apellidos="Tres")
    db.add(cli)
    db.flush()
    fab = Fabric(codigo="T-POS3", nombre="Tela POS", stock_metros=10.0)
    db.add(fab)
    db.commit()
    cid, fid = cli.id, fab.id
    db.close()
    r = c.post("/ventas/pos/vender",
               data={"client_id": str(cid), "concepto": "Terno POS",
                     "garment_tipo": "saco", "precio": "2000", "tela_id": str(fid),
                     "monto_cobro": "2000", "metodo": "yape"}, cookies=ck)
    assert r.status_code in (200, 303)
    from app.models.order import Garment, Order
    db = SessionLocal()
    o = db.query(Order).order_by(Order.id.desc()).first()
    assert o.estado == "confirmado"  # 100% >= mínimo
    g = db.query(Garment).filter(Garment.order_id == o.id).first()
    assert g.tela_reservada is True and db.get(Fabric, fid).stock_metros == 8.0
    db.close()
    assert c.get("/ventas/comprobantes", cookies=ck).status_code == 200
    assert c.get("/ventas/ordenes", cookies=ck).status_code == 200
