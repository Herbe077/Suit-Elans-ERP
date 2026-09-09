"""Redirecciones de compatibilidad: rutas antiguas -> 6 ámbitos (302).

Mantener hasta que no queden bookmarks/integraciones usando las rutas previas.
"""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["legacy"])

REDIRECTS = {
    "/crm": "/comercial/crm",
    "/citas": "/comercial/citas",
    "/empresas": "/comercial/clientes",
    "/sastre/clientes": "/produccion/fichas",
    "/catalogo": "/inventario/catalogo",
    "/inventario": "/inventario/almacen",
    "/compras": "/inventario/compras",
    "/caja": "/ventas/caja",
    "/facturacion": "/ventas/facturacion",
    "/taller": "/produccion/kanban",
    "/taller/tablero": "/produccion/kanban",
    "/taller/pruebas": "/produccion/pruebas",
    "/taller/control-calidad": "/produccion/calidad",
    "/taller/fichas": "/produccion/fichas",
    # "/admin" no tiene ruta "" propia
    "/admin": "/admin/usuarios",
}


@router.get("/empresas/{cid}", include_in_schema=False)
def r_empresa(cid: int):
    return RedirectResponse(f"/comercial/clientes/empresa/{cid}", status_code=302)


@router.get("/sastre/clientes/{cid}", include_in_schema=False)
def r_ficha(cid: int):
    return RedirectResponse(f"/produccion/fichas/{cid}", status_code=302)


@router.get("/taller/fichas/{cid}", include_in_schema=False)
def r_ficha2(cid: int):
    return RedirectResponse(f"/produccion/fichas/{cid}", status_code=302)


@router.get("/taller/orden/{gid}/ficha", include_in_schema=False)
def r_ficha_prod(gid: int):
    return RedirectResponse(f"/produccion/ficha/{gid}", status_code=302)


@router.get("/compras/oc/{pid}", include_in_schema=False)
def r_oc(pid: int):
    return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=302)


@router.get("/crm/cotizaciones/{qid}", include_in_schema=False)
def r_cot(qid: int):
    return RedirectResponse(f"/comercial/crm/cotizaciones/{qid}", status_code=302)


@router.get("/legacy-health", include_in_schema=False)
def legacy_health():
    return {"legacy_redirects": len(REDIRECTS)}


def _build():
    for old, new in REDIRECTS.items():

        def _go(new=new):
            return RedirectResponse(new, status_code=302)

        router.get(old, include_in_schema=False)(_go)


_build()
