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
    assert "grid grid-cols-1 sm:grid-cols-2 gap-4 w-full" in t  # 1 col móvil
    assert "flex flex-col gap-1 w-full" in t  # grupo independiente por medida
    assert "w-full px-3 py-2 border rounded-md" in t  # inputs full-width
    assert "Cintura saco (cm)" in t and "cintura_saco (cm)" not in t
    assert "Largo manga (cm)" in t
    assert 'for="med-cuello"' in t  # label asociado al input


def test_subbar_scroll_y_bottom_nav(client, auth_cookies):
    t = _ficha(client, auth_cookies)
    assert "overflow-x-auto whitespace-nowrap scrollbar-none" in t
    assert "bg-neutral-100/70" in t  # submenú unificado
    assert ".scrollbar-none" in t
    for item in ("Panel", "POS", "Taller", "Rendimiento", "Menú"):
        assert item in t
    assert "Tareo" not in t  # fuera del bottom nav, vive en el drawer


def test_zona_peligro_abajo(client, auth_cookies):
    t = _ficha(client, auth_cookies)
    assert "Zona de Peligro" in t
    assert "Sí, eliminar definitivamente" in t
    assert t.index("Zona de Peligro") > t.index("Agendar prueba")
