"""API v1: auth Bearer, leads, catálogo/stock y webhooks."""


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_api_leads(client, api_token):
    r = client.post("/api/v1/leads", json={"nombre": "Lead API", "origen": "web"},
                    headers=_h(api_token))
    assert r.status_code == 201
    lid = r.json()["id"]
    r = client.patch(f"/api/v1/leads/{lid}", json={"estado": "CITA_AGENDADA"},
                     headers=_h(api_token))
    assert r.json()["estado"] == "CITA_AGENDADA"
    r = client.get("/api/v1/leads?estado=CITA_AGENDADA", headers=_h(api_token))
    assert any(l["id"] == lid for l in r.json())


def test_api_sin_token(client):
    assert client.get("/api/v1/leads").status_code == 401
    assert client.get("/api/v1/leads", headers={"Authorization": "Bearer xxx"}).status_code == 401


def test_api_catalogo_y_stock(client, api_token):
    r = client.post("/api/v1/products", json={"codigo": "P-API", "nombre": "Prod API"},
                    headers=_h(api_token))
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.post(f"/api/v1/products/{pid}/variants",
                    json={"talla": "M", "sku": "P-API-M", "stock": 10, "precio": 100},
                    headers=_h(api_token))
    assert r.status_code == 201
    vid = r.json()["id"]
    r = client.post("/api/v1/stock/adjust",
                    json={"variant_id": vid, "cantidad": -3, "motivo": "venta test"},
                    headers=_h(api_token))
    assert r.status_code == 200
    r = client.get("/api/v1/products", headers=_h(api_token))
    prod = next(p for p in r.json() if p["id"] == pid)
    assert prod["variantes"][0]["stock"] == 7
    # Sin stock suficiente
    r = client.post("/api/v1/stock/adjust", json={"variant_id": vid, "cantidad": -100},
                    headers=_h(api_token))
    assert r.status_code == 400


def test_api_quotation_convert(client, api_token):
    r = client.post("/api/v1/quotations",
                    json={"lineas": [{"concepto": "Saco medida",
                                      "categoria": "prenda_medida",
                                      "garment_tipo": "saco",
                                      "cantidad": 1, "precio_unitario": 2000}]},
                    headers=_h(api_token))
    assert r.status_code == 201
    qid = r.json()["id"]
    assert r.json()["total"] == 2000.0
    # Sin cliente ni empresa → 400
    r = client.post(f"/api/v1/quotations/{qid}/convert", headers=_h(api_token))
    assert r.status_code == 400
    r = client.get("/api/v1/webhooks", headers=_h(api_token))
    assert any(e["event"] == "quotation.converted" for e in r.json()["events"])
