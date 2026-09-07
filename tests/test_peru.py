"""Localización Perú: RUC/DNI, moneda S/ y documentos en fichas y PDFs."""
import pytest

from app.services.peru import soles, validar_doc, validar_dni, validar_ruc


def test_ruc_valido():
    assert validar_ruc("20601234565")  # RUC demo del seed
    assert validar_ruc("20111222330")


def test_ruc_invalido():
    assert not validar_ruc("20123456789")  # verificador incorrecto
    assert not validar_ruc("123")
    assert not validar_ruc("abcdefghijk")
    assert not validar_ruc("")


def test_dni():
    assert validar_dni("12345678")
    assert not validar_dni("1234567")
    assert not validar_dni("123456789")


def test_validar_doc_opcional():
    validar_doc("DNI", None)  # vacío se acepta (campo opcional)
    validar_doc("RUC", "")
    with pytest.raises(ValueError):
        validar_doc("DNI", "123")
    with pytest.raises(ValueError):
        validar_doc("RUC", "20123456789")


def test_soles():
    assert soles(1234.5) == "S/ 1,234.50"


def test_cliente_con_dni(client, auth_cookies):
    r = client.post("/produccion/fichas",
                    data={"nombre": "María", "apellidos": "Quispe",
                          "tipo_doc": "DNI", "nro_doc": "87654321",
                          "distrito": "Miraflores"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = db.query(Client).filter(Client.nro_doc == "87654321").first()
    assert c and c.doc_label == "DNI 87654321" and c.distrito == "Miraflores"
    st, body = c.id, None
    db.close()
    # La ficha muestra el documento
    r = client.get(f"/produccion/fichas/{st}", cookies=auth_cookies)
    assert "DNI 87654321" in r.text


def test_cliente_dni_invalido_no_se_crea(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    antes = db.query(Client).count()
    db.close()
    r = client.post("/produccion/fichas",
                    data={"nombre": "X", "apellidos": "Y",
                          "tipo_doc": "DNI", "nro_doc": "123"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    assert db.query(Client).count() == antes
    db.close()


def test_empresa_ruc_flexible_no_bloquea(client, auth_cookies):
    """RUC flexible: cualquier formato se guarda (normalizado), sin bloquear."""
    from app.core.database import SessionLocal
    from app.models.company import Company
    r = client.post("/comercial/clientes/empresa", data={"nombre_comercial": "Flex S.A.C.",
                                       "ruc": "20123456789"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    c = db.query(Company).filter(Company.nombre_comercial == "Flex S.A.C.").first()
    assert c is not None and c.ruc == "20123456789"
    db.close()
    # Con guiones/espacios se normaliza y también se guarda
    r = client.post("/comercial/clientes/empresa", data={"nombre_comercial": "Flex2 S.A.C.",
                                       "ruc": "20-12345678-9"}, cookies=auth_cookies)
    assert r.status_code in (200, 303)
    db = SessionLocal()
    c = db.query(Company).filter(Company.nombre_comercial == "Flex2 S.A.C.").first()
    assert c is not None and c.ruc == "20123456789"
    db.close()


def test_api_empresa_ruc(client, api_token):
    h = {"Authorization": f"Bearer {api_token}"}
    r = client.post("/api/v1/companies",
                    json={"nombre_comercial": "API PE S.A.C.", "ruc": "20601234565"},
                    headers=h)
    assert r.status_code == 201
    # RUC flexible en API: formato no estándar también se acepta
    r = client.post("/api/v1/companies",
                    json={"nombre_comercial": "Flex API", "ruc": "123"},
                    headers=h)
    assert r.status_code == 201
    assert r.json()["ruc"] == "123"


def test_moneda_en_dashboard(client, auth_cookies):
    r = client.get("/", cookies=auth_cookies)
    assert "S/" in r.text and "${" not in r.text.replace("S/ {", "")
