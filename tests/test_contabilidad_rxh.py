"""RxH de destajo: destino 921 (nunca 941) y split 6322/40172/4241.

Aunque el sastre figure en planilla 5ta, la naturaleza del comprobante
(RECIBO_HONORARIOS / Honorarios RxH) manda el costo al taller.
"""
from datetime import date
from decimal import Decimal


def _mapa(db, asiento_id):
    from app.models.finanzas import CuentaContable, LineaAsientoContable
    out = {}
    for l in db.query(LineaAsientoContable).filter(
            LineaAsientoContable.asiento_id == asiento_id).all():
        c = db.get(CuentaContable, l.cuenta_id)
        out[c.codigo] = (Decimal(str(l.debe)), Decimal(str(l.haber)))
    return out


def _asientos_gasto(db, gid, origen="HONORARIOS"):
    from app.models.finanzas import AsientoContable
    return db.query(AsientoContable).filter(
        AsientoContable.origen_id == gid,
        AsientoContable.origen_tipo == origen).all()


def test_rxh_planilla_destino_921_y_split():
    from app.core import security
    from app.core.database import SessionLocal
    from app.models.personnel import Empleado
    from app.models.user import User
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    from app.services import rendimiento_rxh as rxh
    db = SessionLocal()
    u = User(email="rxh-planilla@t.pe", full_name="Sastre Planilla",
             hashed_password=security.hash_password("x"),
             role="SASTRE-ASISTENTE", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    # En Personal figura como PLANILLA 5TA: el destino igual debe ser 921.
    db.add(Empleado(nombres="Sastre", apellidos="Planilla",
                    dni="RXHC11111111", puesto="SASTRE_MAESTRO",
                    tipo_contrato="PLANILLA_5TA", aplica_retencion_8=True))
    db.commit()
    cat = CatalogoOperacion(codigo="RXHC-PLAN", nombre_operacion="Op plan",
                            tarifa_base=Decimal("800"), activa=True)
    db.add(cat)
    db.flush()
    reg = RegistroJornada(operario_id=u.id, fecha=date.today(),
                          estado="PENDIENTE")
    db.add(reg)
    db.flush()
    db.add(DetalleJornada(registro_jornada_id=reg.id, operacion_id=cat.id,
                          cantidad=1, tarifa_aplicada=Decimal("800"),
                          subtotal=Decimal("800")))
    db.commit()
    r = rxh.generar_provision_rxh(db, u.id, "RXH-DEST-921",
                                  ruc_dni="RXHC11111111")
    assert r["retencion"] == 64.0 and r["neto"] == 736.0
    assert r["empleado_id"] is not None
    # Naturaleza: 6322 bruto / 40172 ret / 4241 neto
    mapas = [_mapa(db, a.id) for a in _asientos_gasto(db, r["gasto_id"])]
    nat = next(m for m in mapas if "6322" in m)
    assert nat["6322"] == (Decimal("800"), Decimal("0"))
    assert nat["40172"] == (Decimal("0"), Decimal("64"))
    assert nat["4241"] == (Decimal("0"), Decimal("736"))
    # Destino: 921 obligatorio, jamás 941
    dest = [m for m in mapas if "921" in m or "941" in m]
    assert dest, "falta asiento de destino"
    assert any("921" in m for m in dest)
    assert not any("941" in m for m in dest)
    d921 = next(m for m in dest if "921" in m)
    assert d921["921"] == (Decimal("800"), Decimal("0"))
    assert d921["791"] == (Decimal("0"), Decimal("800"))
    # CxP por el neto, sin tocar caja
    from app.models.finanzas import CuentaPorPagar, MovimientoFinanciero
    cxp = db.get(CuentaPorPagar, r["cxp_id"])
    assert cxp.saldo_pendiente == 736.0
    assert db.query(MovimientoFinanciero).filter(
        MovimientoFinanciero.comprobante_ref == "RXH-DEST-921").count() == 0
    # limpieza
    from app.models.finanzas import (AsientoContable, GastoRegistrado,
                                     LineaAsientoContable)
    from app.models.personnel import Empleado
    from app.models.purchasing import Supplier
    from app.modules.rendimiento.models import (CatalogoOperacion,
                                                DetalleJornada,
                                                RegistroJornada)
    aids = [x.id for x in _asientos_gasto(db, r["gasto_id"])]
    db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id.in_(aids)).delete(
        synchronize_session=False)
    db.query(AsientoContable).filter(
        AsientoContable.id.in_(aids)).delete(synchronize_session=False)
    db.query(GastoRegistrado).filter(
        GastoRegistrado.id == r["gasto_id"]).delete()
    db.query(CuentaPorPagar).filter(
        CuentaPorPagar.id == r["cxp_id"]).delete()
    db.query(DetalleJornada).filter(
        DetalleJornada.registro_jornada_id == reg.id).delete()
    db.query(RegistroJornada).filter(RegistroJornada.id == reg.id).delete()
    db.query(CatalogoOperacion).filter(
        CatalogoOperacion.codigo == "RXHC-PLAN").delete()
    db.query(Supplier).filter(
        Supplier.id == r["proveedor_id"]).delete()
    db.query(Empleado).filter(Empleado.dni == "RXHC11111111").delete()
    db.query(User).filter(User.id == u.id).delete()
    db.commit()
    db.close()


def test_gasto_manual_rxh_destino_921():
    from datetime import date as _d
    from app.core.database import SessionLocal
    from app.services import finanzas as f
    db = SessionLocal()
    g, a = f.registrar_gasto_operativo(
        db, fecha=_d.today(), categoria="HONORARIOS_RXH", monto_base=1000,
        tipo_comprobante="RECIBO_HONORARIOS", numero_comprobante="RXH-MAN-921",
        retencion=80)
    assert Decimal(str(g.monto_igv)) == Decimal("0")
    mapa = _mapa(db, a.id)
    assert mapa["6322"] == (Decimal("1000"), Decimal("0"))
    assert mapa["40172"] == (Decimal("0"), Decimal("80"))
    assert mapa["424"] == (Decimal("0"), Decimal("920"))
    dest = [m for m in (_mapa(db, x.id) for x in _asientos_gasto(db, g.id))
            if "921" in m or "941" in m]
    assert any("921" in m for m in dest)
    assert not any("941" in m for m in dest)
    # limpieza
    from app.models.finanzas import (AsientoContable, GastoRegistrado,
                                     LineaAsientoContable)
    aids = [x.id for x in _asientos_gasto(db, g.id)] + [a.id]
    db.query(LineaAsientoContable).filter(
        LineaAsientoContable.asiento_id.in_(aids)).delete(
        synchronize_session=False)
    db.query(AsientoContable).filter(
        AsientoContable.id.in_(aids)).delete(synchronize_session=False)
    db.query(GastoRegistrado).filter(GastoRegistrado.id == g.id).delete()
    db.commit()
    db.close()
