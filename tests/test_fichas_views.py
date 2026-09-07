"""Views móvil: ficha responsive, sub-bar scroll, bottom nav y zona de peligro."""


def _ficha(client, auth_cookies):
    from app.core.database import SessionLocal
    from app.models.client import Client
    db = SessionLocal()
    c = db.query(Client).filter(Client.nro_doc == "VIEWS-DNI").first()
    if not c:
        c = Client(nombre="Views", apellidos="Test", tipo_doc="DNI",
                   nro_doc="VIEWS-DNI", clasificacion="Nuevo")
        db.add(c)
        db.commit()
        db.refresh(c)
    cid = c.id
    db.close()
    return client.get(f"/produccion/fichas/{cid}", cookies=auth_cookies).text


def test_ficha_grilla_movil_y_etiquetas(client, auth_cookies):
    t = _ficha(client, auth_cookies)
    assert "grid grid-cols-1 sm:grid-cols-2 gap-3" in t  # 1 columna en móvil
    assert "Cintura saco (cm)" in t and "cintura_saco (cm)" not in t
    assert "Largo manga (cm)" in t


def test_subbar_scroll_y_bottom_nav(client, auth_cookies):
    t = _ficha(client, auth_cookies)
    assert "flex overflow-x-auto whitespace-nowrap scrollbar-none py-2" in t
    assert ".scrollbar-none" in t
    for item in ("Panel", "POS", "Taller", "Caja", "Menú"):
        assert item in t
    assert "Tareo" not in t  # fuera del bottom nav, vive en el drawer


def test_zona_peligro_abajo(client, auth_cookies):
    t = _ficha(client, auth_cookies)
    assert "Zona de Peligro" in t
    assert "Sí, eliminar definitivamente" in t
    assert t.index("Zona de Peligro") > t.index("Agendar prueba")
