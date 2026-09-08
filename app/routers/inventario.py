"""Ámbito Inventario & Cadena: catálogo, almacén y compras.

Spec 5: ProductoInsumo + MovimientoKardex + OrdenCompra/DetalleOrdenCompra
Legacy Fabric/Supply/PurchaseOrder se mantienen para compatibilidad.
"""
from datetime import date
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR
from app.core.database import get_db
from app.core.deps import require_roles
from app.models.catalog import Collection, Product, ProductVariant
from app.models.inventory import Fabric, StockMovement, Supply
from app.models.inventario import DetalleOrdenCompra, MovimientoKardex, OrdenCompra, ProductoInsumo, normalizar_estado_oc
from app.models.purchasing import PurchaseLine, PurchaseOrder, Supplier
from app.services.inventory import apply_movement, low_stock
from app.services import purchasing as po_svc
from app.services import compras_kardex as ck_svc
from sqlalchemy.exc import IntegrityError
from urllib.parse import quote

router = APIRouter(prefix="/inventario", tags=["inventario"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
Almacen = Depends(require_roles("ALMACEN"))  # ADMIN pasa siempre
AlmacenVentas = Depends(require_roles("ALMACEN", "VENTA"))


def _err(base: str, msg: str):
    return RedirectResponse(f"{base}?error={quote(msg)}", status_code=303)

CATEGORIAS = ["TELA", "TELAS", "FORRO", "FORROS", "AVIO", "AVÍOS", "AVIOS", "EMPAQUE", "EMPAQUES"]
# Canónicos del spec (escritura) + aliases legacy (lectura). Ver app/models/inventario.py.
ESTADOS_OC = ["DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "BILLED", "CANCELLED",
              "BORRADOR", "ENVIADA", "RECIBIDA_PARCIAL", "COMPLETADA", "CANCELADA", "RECIBIDA",
              "borrador", "enviada", "recibida_parcial", "recibida", "cancelada"]

# ---------- Catálogo de telas y muestrarios + Insumos spec ----------
@router.get("/catalogo", response_class=HTMLResponse)
def catalogo(request: Request, categoria: str = "", q: str = "", error: str = "", db: Session = Depends(get_db), user=AlmacenVentas):
    # legacy comercial
    productos = db.query(Product).order_by(Product.codigo).all()
    variantes = {p.id: db.query(ProductVariant).filter(ProductVariant.product_id == p.id).all()
                 for p in productos}
    # spec insumos
    query = db.query(ProductoInsumo)
    if categoria:
        # normaliza plural/singular
        cat = categoria.upper()
        # acepta TELAS -> TELA etc
        if cat in ("TELAS", "TELA"):
            query = query.filter(ProductoInsumo.categoria.in_(["TELA", "TELAS"]))
        elif cat in ("FORROS", "FORRO"):
            query = query.filter(ProductoInsumo.categoria.in_(["FORRO", "FORROS"]))
        elif cat in ("AVÍOS", "AVIOS", "AVIO"):
            query = query.filter(ProductoInsumo.categoria.in_(["AVIO", "AVÍOS", "AVIOS"]))
        elif cat in ("EMPAQUES", "EMPAQUE"):
            query = query.filter(ProductoInsumo.categoria.in_(["EMPAQUE", "EMPAQUES"]))
        else:
            query = query.filter(ProductoInsumo.categoria == cat)
    if q:
        like = f"%{q}%"
        query = query.filter((ProductoInsumo.sku.like(like)) | (ProductoInsumo.nombre.like(like)))
    insumos = query.order_by(ProductoInsumo.sku).limit(200).all()
    # ensure mirror for legacy fabrics not yet in insumos for display
    # (no auto-create here to no saturar)
    return templates.TemplateResponse(request, "inventario/catalogo.html", {
        "user": user, "productos": productos, "variantes": variantes,
        "colecciones": db.query(Collection).order_by(Collection.nombre).all(),
        "faltantes": low_stock(db)["variants"],
        "insumos": insumos, "categoria": categoria, "q": q, "error": error,
        "proveedores": po_svc.proveedores_visibles(db)})


@router.post("/catalogo/colecciones")
def crear_coleccion(nombre: str = Form(...), temporada: str = Form(""),
                    db: Session = Depends(get_db), user=AlmacenVentas):
    if db.query(Collection).filter(Collection.nombre == nombre.strip()).first():
        return _err("/inventario/catalogo", "duplicado: colección existente")
    db.add(Collection(nombre=nombre.strip(), temporada=temporada or None))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _err("/inventario/catalogo", "duplicado: colección existente")
    return RedirectResponse("/inventario/catalogo", status_code=303)


@router.post("/catalogo/productos")
def crear_producto(codigo: str = Form(...), nombre: str = Form(...),
                   linea: str = Form("comercial"), collection_id: str = Form(""),
                   precio_base: float = Form(0),
                   db: Session = Depends(get_db), user=AlmacenVentas):
    if db.query(Product).filter(Product.codigo == codigo.strip()).first():
        return _err("/inventario/catalogo", "duplicado: código existente")
    p = Product(codigo=codigo.strip(), nombre=nombre.strip(), linea=linea,
                collection_id=int(collection_id) if collection_id else None,
                precio_base=precio_base)
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _err("/inventario/catalogo", "duplicado: código existente")
    return RedirectResponse("/inventario/catalogo", status_code=303)


@router.post("/catalogo/productos/{pid}/variantes")
def crear_variante(pid: int, talla: str = Form("M"), sku: str = Form(...),
                   stock: float = Form(0), precio: float = Form(0),
                   costo_unitario: float = Form(0),
                   db: Session = Depends(get_db), user=AlmacenVentas):
    if db.query(ProductVariant).filter(ProductVariant.sku == sku.strip()).first():
        return _err("/inventario/catalogo", "duplicado: SKU existente")
    if (costo_unitario or 0) < 0:
        return _err("/inventario/catalogo", "costo inválido")
    db.add(ProductVariant(product_id=pid, talla=talla.strip(), sku=sku.strip(),
                          stock=stock, precio=precio,
                          costo_unitario=costo_unitario or 0))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _err("/inventario/catalogo", "duplicado: SKU existente")
    return RedirectResponse("/inventario/catalogo", status_code=303)


@router.post("/catalogo/stock")
def ajustar_stock(variant_id: int = Form(...), cantidad: float = Form(...),
                  motivo: str = Form(""), db: Session = Depends(get_db), user=AlmacenVentas):
    # Motivo obligatorio: todo ajuste de prenda terminada queda auditado en Kardex.
    if not (motivo or "").strip():
        return _err("/inventario/catalogo", "el motivo del ajuste es obligatorio (Kardex)")
    if cantidad == 0:
        return _err("/inventario/catalogo", "cantidad debe ser distinta de cero")
    try:
        apply_movement(db, "variant", variant_id, cantidad,
                       "entrada" if cantidad >= 0 else "salida",
                       motivo.strip(), user.id)
    except ValueError as e:
        return _err("/inventario/catalogo", str(e))
    return RedirectResponse("/inventario/catalogo", status_code=303)


# Endpoint spec ProductoInsumo
@router.post("/catalogo/insumo")
def crear_insumo(sku: str = Form(...), nombre: str = Form(...),
                 categoria: str = Form("TELA"), composicion: str = Form(""),
                 color: str = Form(""), ancho_cm: float = Form(150),
                 unidad_medida: str = Form("METROS"),
                 costo_unitario: float = Form(0), precio_metro: float = Form(0),
                 stock_fisico: float = Form(0), stock_minimo: float = Form(10),
                  proveedor_id: str = Form(""),
                  db: Session = Depends(get_db), user=Almacen):
    cat = categoria.upper()
    # normaliza
    if cat not in ("TELA", "TELAS", "FORRO", "FORROS", "AVIO", "AVÍOS", "AVIOS", "EMPAQUE", "EMPAQUES"):
        cat = "TELA"
    # singulariza para storage
    if cat == "TELAS":
        cat = "TELA"
    elif cat == "FORROS":
        cat = "FORRO"
    elif cat in ("AVÍOS", "AVIOS"):
        cat = "AVIO"
    elif cat == "EMPAQUES":
        cat = "EMPAQUE"
    if db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku.strip()).first():
        return _err("/inventario/catalogo", "duplicado: SKU existente")
    prov = int(proveedor_id) if proveedor_id and proveedor_id.strip() else None
    prod = ProductoInsumo(sku=sku.strip(), nombre=nombre.strip(), categoria=cat,
                          composicion=composicion or None, color=color or None,
                          ancho_cm=ancho_cm, unidad_medida=unidad_medida.upper(),
                          costo_unitario=costo_unitario, precio_metro=precio_metro or costo_unitario,
                          stock_fisico=stock_fisico, stock_reservado=0,
                          stock_minimo=stock_minimo, proveedor_id=prov,
                          ancho_m=ancho_cm/100 if ancho_cm else 1.5)
    db.add(prod)
    try:
        db.commit()
        # mirror a Fabric/Supply para disponibilidad en POS
        if cat in ("TELA", "FORRO"):
            if not db.query(Fabric).filter(Fabric.codigo == prod.sku).first():
                db.add(Fabric(codigo=prod.sku, nombre=prod.nombre,
                              composicion=prod.composicion, color=prod.color,
                              ancho_m=prod.ancho_cm/100, precio_metro=prod.precio_metro or prod.costo_unitario,
                              stock_metros=prod.stock_fisico, stock_minimo=prod.stock_minimo,
                              proveedor_id=prov))
                db.commit()
        else:
            if not db.query(Supply).filter(Supply.codigo == prod.sku).first():
                db.add(Supply(codigo=prod.sku, nombre=prod.nombre,
                              stock=prod.stock_fisico, stock_minimo=prod.stock_minimo,
                              costo_unitario=prod.costo_unitario))
                db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse("/inventario/catalogo", status_code=303)


CATEGORIAS_INSUMO = ("TELA", "FORRO", "AVIO", "EMPAQUE")


def _norm_categoria(cat: str) -> str:
    c = (cat or "TELA").upper()
    if c in ("TELAS",):
        return "TELA"
    if c in ("FORROS",):
        return "FORRO"
    if c in ("AVÍOS", "AVIOS"):
        return "AVIO"
    if c in ("EMPAQUES",):
        return "EMPAQUE"
    return c if c in CATEGORIAS_INSUMO else "TELA"


# ---------- Edición de maestro (materia prima y prendas) ----------
# SKU/código y existencias son de solo lectura: el SKU es clave del espejo
# legacy y el stock/costo solo se mueven vía Kardex (trazabilidad).
@router.get("/catalogo/insumo/{iid}/editar", response_class=HTMLResponse)
def editar_insumo(iid: int, request: Request, error: str = "",
                  db: Session = Depends(get_db), user=Almacen):
    prod = db.get(ProductoInsumo, iid)
    if not prod:
        return HTMLResponse("Insumo no encontrado", status_code=404)
    return templates.TemplateResponse(request, "inventario/insumo_editar.html", {
        "user": user, "error": error, "ins": prod, "categorias": list(CATEGORIAS_INSUMO),
        "proveedores": po_svc.proveedores_visibles(db)})


@router.post("/catalogo/insumo/{iid}/guardar")
def guardar_insumo(iid: int, nombre: str = Form(...), categoria: str = Form("TELA"),
                   composicion: str = Form(""), color: str = Form(""),
                   ancho_cm: float = Form(150), unidad_medida: str = Form("METROS"),
                   precio_metro: float = Form(0), stock_minimo: float = Form(10),
                   proveedor_id: str = Form(""),
                   db: Session = Depends(get_db), user=Almacen):
    prod = db.get(ProductoInsumo, iid)
    if not prod:
        return HTMLResponse("Insumo no encontrado", status_code=404)
    if not nombre.strip():
        return _err(f"/inventario/catalogo/insumo/{iid}/editar", "nombre requerido")
    if ancho_cm is not None and ancho_cm < 0:
        return _err(f"/inventario/catalogo/insumo/{iid}/editar", "ancho inválido")
    if stock_minimo is not None and stock_minimo < 0:
        return _err(f"/inventario/catalogo/insumo/{iid}/editar", "stock mínimo inválido")
    try:
        prov = int(proveedor_id) if proveedor_id and proveedor_id.strip() else None
        if prov is not None and not db.get(Supplier, prov):
            return _err(f"/inventario/catalogo/insumo/{iid}/editar", "proveedor inexistente")
    except ValueError:
        return _err(f"/inventario/catalogo/insumo/{iid}/editar", "proveedor inválido")
    prod.nombre = nombre.strip()
    prod.categoria = _norm_categoria(categoria)
    prod.composicion = composicion.strip() or None
    prod.color = color.strip() or None
    prod.ancho_cm = ancho_cm
    prod.ancho_m = ancho_cm / 100 if ancho_cm else prod.ancho_m
    prod.unidad_medida = (unidad_medida or "METROS").upper()
    prod.precio_metro = precio_metro or 0
    prod.stock_minimo = stock_minimo
    prod.proveedor_id = prov
    try:
        db.commit()
        # espejo display al gemelo legacy (solo descriptivos, jamás stock)
        try:
            leg = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
            if leg:
                leg.nombre, leg.composicion, leg.color = prod.nombre, prod.composicion, prod.color
                if prod.ancho_cm:
                    leg.ancho_m = prod.ancho_cm / 100
                leg.precio_metro = prod.precio_metro
                leg.stock_minimo = prod.stock_minimo
                leg.proveedor_id = prov
            else:
                leg2 = db.query(Supply).filter(Supply.codigo == prod.sku).first()
                if leg2:
                    leg2.nombre = prod.nombre
                    leg2.stock_minimo = prod.stock_minimo
            db.commit()
        except Exception:
            db.rollback()
    except Exception as e:
        db.rollback()
        return _err(f"/inventario/catalogo/insumo/{iid}/editar", f"no se pudo guardar: {e}")
    return RedirectResponse("/inventario/catalogo", status_code=303)


@router.get("/catalogo/producto/{pid}/editar", response_class=HTMLResponse)
def editar_producto(pid: int, request: Request, error: str = "",
                    db: Session = Depends(get_db), user=Almacen):
    prod = db.get(Product, pid)
    if not prod:
        return HTMLResponse("Producto no encontrado", status_code=404)
    return templates.TemplateResponse(request, "inventario/producto_editar.html", {
        "user": user, "error": error, "prod": prod,
        "variantes": db.query(ProductVariant).filter(ProductVariant.product_id == pid).order_by(ProductVariant.talla).all(),
        "colecciones": db.query(Collection).order_by(Collection.nombre).all()})


@router.post("/catalogo/producto/{pid}/guardar")
def guardar_producto(pid: int, nombre: str = Form(...), linea: str = Form("comercial"),
                     collection_id: str = Form(""), precio_base: float = Form(0),
                     db: Session = Depends(get_db), user=Almacen):
    prod = db.get(Product, pid)
    if not prod:
        return HTMLResponse("Producto no encontrado", status_code=404)
    if not nombre.strip():
        return _err(f"/inventario/catalogo/producto/{pid}/editar", "nombre requerido")
    if precio_base is not None and precio_base < 0:
        return _err(f"/inventario/catalogo/producto/{pid}/editar", "precio inválido")
    try:
        col = int(collection_id) if collection_id and collection_id.strip() else None
        if col is not None and not db.get(Collection, col):
            return _err(f"/inventario/catalogo/producto/{pid}/editar", "colección inexistente")
    except ValueError:
        return _err(f"/inventario/catalogo/producto/{pid}/editar", "colección inválida")
    prod.nombre = nombre.strip()
    prod.linea = (linea or "comercial").strip() or "comercial"
    prod.collection_id = col
    prod.precio_base = precio_base or 0
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return _err(f"/inventario/catalogo/producto/{pid}/editar", f"no se pudo guardar: {e}")
    return RedirectResponse("/inventario/catalogo", status_code=303)


@router.post("/catalogo/variante/{vid}/guardar")
def guardar_variante(vid: int, talla: str = Form("M"), precio: float = Form(0),
                     costo_unitario: float = Form(0),
                     db: Session = Depends(get_db), user=Almacen):
    var = db.get(ProductVariant, vid)
    if not var:
        return HTMLResponse("Variante no encontrada", status_code=404)
    if precio is not None and precio < 0:
        return _err(f"/inventario/catalogo/producto/{var.product_id}/editar", "precio inválido")
    if costo_unitario is not None and costo_unitario < 0:
        return _err(f"/inventario/catalogo/producto/{var.product_id}/editar", "costo inválido")
    var.talla = (talla or "M").strip() or "M"
    var.precio = precio or 0
    var.costo_unitario = costo_unitario or 0
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return _err(f"/inventario/catalogo/producto/{var.product_id}/editar", f"no se pudo guardar: {e}")
    return RedirectResponse(f"/inventario/catalogo/producto/{var.product_id}/editar", status_code=303)


# ---------- Stock de almacén ----------
@router.get("/almacen", response_class=HTMLResponse)
def almacen(request: Request, error: str = "", sub: str = "mp", db: Session = Depends(get_db), user=Almacen):
    # soporte alias spec almacen_kardex.html -> reutiliza almacen.html si no existe
    telas = db.query(Fabric).order_by(Fabric.codigo).all()
    avios = db.query(Supply).order_by(Supply.codigo).all()
    insumos = db.query(ProductoInsumo).order_by(ProductoInsumo.sku).limit(200).all()
    movs = db.query(StockMovement).order_by(StockMovement.id.desc()).limit(50).all()
    kardex = db.query(MovimientoKardex).order_by(MovimientoKardex.id.desc()).limit(50).all()
    # combina faltantes legacy + spec
    falt = low_stock(db)
    # WIP: prendas en taller no entregadas (Cta 21) + órdenes de producción abiertas
    from app.models.order import Garment, Order
    from app.models.produccion import OrdenProduccion
    _g = db.query(Garment, Order).join(Order, Garment.order_id == Order.id).filter(
        Garment.estado_taller.notin_(["ENTREGADO", "entregado", "CANCELADO", "cancelado"]),
        Order.estado.notin_(["entregado", "cancelado"])).order_by(Garment.id.desc()).limit(100).all()
    wips = [{"g": g, "folio": o.folio if o else "?"} for g, o in _g]
    ops = db.query(OrdenProduccion).filter(
        OrdenProduccion.estado.notin_(["ENTREGADO", "entregado", "CANCELADO", "cancelado"])).order_by(
        OrdenProduccion.id.desc()).limit(100).all()
    # PT: variantes con ficha de producto (Cta 23)
    pts = db.query(ProductVariant).order_by(ProductVariant.sku).limit(200).all()
    prods = {p.id: p for p in db.query(Product).all()}
    # Pedidos / fichas de taller recientes (referencia de egresos a taller)
    pedidos = db.query(Order).order_by(Order.id.desc()).limit(100).all()
    return templates.TemplateResponse(request, "inventario/almacen.html", {
        "user": user, "telas": telas, "avios": avios, "movs": movs,
        "insumos": insumos, "kardex": kardex, "faltantes": falt, "error": error,
        "sub": sub if sub in ("mp", "wip", "pt") else "mp",
        "wips": wips, "ops": ops, "pts": pts, "prods": prods,
        "pedidos": pedidos,
        "folios_ov": {o.id: o.folio for o in pedidos}})

@router.get("/almacen/kardex", response_class=HTMLResponse)
def almacen_kardex(request: Request, db: Session = Depends(get_db), user=Almacen):
    # alias para spec ruta alternativa
    return almacen(request, db, user)


@router.post("/almacen/tela")
def alta_tela(codigo: str = Form(...), nombre: str = Form(...),
              stock_metros: float = Form(0), precio_metro: float = Form(0),
              stock_minimo: float = Form(10),
              composicion: str = Form(""), color: str = Form(""),
              ancho_cm: float = Form(150), proveedor_id: str = Form(""),
              db: Session = Depends(get_db), user=Almacen):
    if db.query(Fabric).filter(Fabric.codigo == codigo.strip()).first():
        return _err("/inventario/almacen", "duplicado: código de tela existente")
    prov = int(proveedor_id) if proveedor_id and proveedor_id.strip() else None
    fab = Fabric(codigo=codigo.strip(), nombre=nombre.strip(),
                  stock_metros=stock_metros, precio_metro=precio_metro,
                  stock_minimo=stock_minimo, composicion=composicion or None,
                  color=color or None, ancho_m=ancho_cm/100 if ancho_cm else 1.5,
                  proveedor_id=prov)
    db.add(fab)
    try:
        db.commit()
        # mirror ProductoInsumo
        if not db.query(ProductoInsumo).filter(ProductoInsumo.sku == fab.codigo).first():
            db.add(ProductoInsumo(sku=fab.codigo, nombre=fab.nombre, categoria="TELA",
                                  composicion=fab.composicion, color=fab.color,
                                  ancho_cm=ancho_cm, unidad_medida="METROS",
                                  costo_unitario=precio_metro, precio_metro=precio_metro,
                                  stock_fisico=stock_metros, stock_reservado=0,
                                  stock_minimo=stock_minimo, proveedor_id=prov))
            db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse("/inventario/almacen", status_code=303)


@router.post("/almacen/avio")
def alta_avio(codigo: str = Form(...), nombre: str = Form(...), stock: float = Form(0),
              db: Session = Depends(get_db), user=Almacen):
    if db.query(Supply).filter(Supply.codigo == codigo.strip()).first():
        return _err("/inventario/almacen", "duplicado: código de avío existente")
    sup = Supply(codigo=codigo.strip(), nombre=nombre.strip(), stock=stock)
    db.add(sup)
    try:
        db.commit()
        if not db.query(ProductoInsumo).filter(ProductoInsumo.sku == sup.codigo).first():
            db.add(ProductoInsumo(sku=sup.codigo, nombre=sup.nombre, categoria="AVIO",
                                  unidad_medida="UNIDADES", costo_unitario=sup.costo_unitario,
                                  stock_fisico=stock, stock_reservado=0,
                                  stock_minimo=sup.stock_minimo))
            db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse("/inventario/almacen", status_code=303)


@router.post("/almacen/movimiento")
def movimiento(item_tipo: str = Form(...), item_id: int = Form(...),
               cantidad: float = Form(...), tipo: str = Form(...), motivo: str = Form(""),
               observacion: str = Form(""),
               db: Session = Depends(get_db), user=Almacen):
    # compatibilidad spec: tipo_movimiento vs tipo, observacion vs motivo
    obs = observacion or motivo
    # normaliza signo según tipo (entrada suma, salida/merma resta, ajuste respeta signo)
    tipo_norm = (tipo or "").lower()
    if tipo_norm in ("entrada", "ingreso", "ingreso_compra", "devolucion"):
        cantidad = abs(cantidad)
    elif tipo_norm in ("salida", "merma", "ajuste_merma", "salida_taller"):
        cantidad = -abs(cantidad)
    if cantidad == 0:
        return RedirectResponse("/inventario/almacen", status_code=303)
    # Legacy Fabric/Supply movement
    try:
        apply_movement(db, item_tipo, item_id, cantidad, tipo, obs, user.id)
    except ValueError:
        pass
    # Mirror spec ProductoInsumo si aplica (si item_tipo fabric/supply mapea a ProductoInsumo)
    try:
        # intenta encontrar producto por codigo del item
        prod = None
        if item_tipo == "fabric":
            fab = db.get(Fabric, item_id)
            if fab:
                prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == fab.codigo).first()
                if not prod:
                    from app.services.inventory import ensure_producto_for_fabric
                    prod = ensure_producto_for_fabric(db, fab)
                # tipo spec mapping
                mapping = {"entrada": "INGRESO_COMPRA", "salida": "SALIDA_TALLER", "merma": "AJUSTE_MERMA", "ajuste": "AJUSTE_MERMA", "devolucion": "DEVOLUCION"}
                spec_tipo = mapping.get(tipo.lower(), "AJUSTE_MERMA" if cantidad < 0 else "INGRESO_COMPRA")
                # actualizar spec stock_fisico para mermas/ajustes
                if spec_tipo == "AJUSTE_MERMA" and cantidad < 0:
                    prod.stock_fisico = round(max(0, prod.stock_fisico + cantidad), 2)
                elif spec_tipo == "INGRESO_COMPRA":
                    prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
                elif spec_tipo == "SALIDA_TALLER" and cantidad < 0:
                    prod.stock_fisico = round(max(0, prod.stock_fisico + cantidad), 2)
                db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento=spec_tipo,
                                        cantidad=abs(cantidad), usuario_id=user.id, observacion=obs))
                db.commit()
        elif item_tipo == "supply":
            sup = db.get(Supply, item_id)
            if sup:
                prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sup.codigo).first()
                if not prod:
                    from app.services.inventory import ensure_producto_for_supply
                    prod = ensure_producto_for_supply(db, sup)
                mapping = {"entrada": "INGRESO_COMPRA", "salida": "SALIDA_TALLER", "merma": "AJUSTE_MERMA", "ajuste": "AJUSTE_MERMA"}
                spec_tipo = mapping.get(tipo.lower(), "AJUSTE_MERMA" if cantidad < 0 else "INGRESO_COMPRA")
                if cantidad < 0:
                    prod.stock_fisico = round(max(0, prod.stock_fisico + cantidad), 2)
                else:
                    prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
                db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento=spec_tipo,
                                        cantidad=abs(cantidad), usuario_id=user.id, observacion=obs))
                db.commit()
    except Exception:
        pass
    return RedirectResponse("/inventario/almacen", status_code=303)


# Kardex spec directo
# Tipos por ámbito de almacén (Cta 24 MP / Cta 23 PT). Los legacy MP se aceptan.
TIPOS_KARDEX_MP = ("INGRESO_COMPRA", "SALIDA_TALLER", "AJUSTE_INVENTARIO",
                   "AJUSTE_MERMA", "DEVOLUCION", "INGRESO", "SALIDA")
TIPOS_KARDEX_PT = {"INGRESO_PRODUCCION": 1, "SALIDA_VENTA": -1,
                   "AJUSTE_MERMA": -1, "MUESTRA": -1}


@router.post("/almacen/kardex")
def kardex_mov(producto_id: int = Form(0), tipo_movimiento: str = Form(...),
               cantidad: float = Form(...), observacion: str = Form(""),
               orden_venta_id: str = Form(""), alcance: str = Form("mp"),
               variant_id: int = Form(0), costo_unitario: str = Form(""),
               db: Session = Depends(get_db), user=Almacen):
    """Kardex por ámbito: mp (insumos Cta 24) o pt (variantes Cta 21).

    Desacoplado: el campo diligenciado manda (Producto/Insumo OR Variante/SKU).
    En PT solo se valida la variante; el producto nunca es exigible si hay SKU.
    """
    if not cantidad or cantidad <= 0:
        return _err("/inventario/almacen", "cantidad debe ser positiva")
    tipo = (tipo_movimiento or "").upper()
    scope = (alcance or "mp").lower()
    var = db.get(ProductVariant, variant_id or 0)
    prod_mp = db.get(ProductoInsumo, producto_id or 0)
    # En PT el producto se ignora (solo variante); con SKU diligenciado se
    # resuelve a PT aunque el alcance no lo indique.
    use_pt = var is not None and (scope == "pt" or prod_mp is None)
    if use_pt:
        if tipo not in TIPOS_KARDEX_PT:
            return _err("/inventario/almacen?sub=pt", f"tipo PT inválido: {tipo_movimiento}")
        signo = TIPOS_KARDEX_PT[tipo]
        # Referencia opcional al pedido / ficha de taller en la salida a venta
        obs_pt = (observacion or "").strip()
        ov_id = int(orden_venta_id) if orden_venta_id and str(orden_venta_id).isdigit() else None
        if ov_id and tipo == "SALIDA_VENTA":
            from app.models.order import Order as _Order
            _o = db.get(_Order, ov_id)
            if _o:
                obs_pt = f"{obs_pt} · Pedido {_o.folio}".strip(" ·")
        try:
            # Costo autocompletado del SKU en ingresos/ajustes positivos.
            if tipo == "INGRESO_PRODUCCION":
                try:
                    cu = float((costo_unitario or "").strip() or 0)
                except (ValueError, AttributeError):
                    cu = 0.0
                if cu > 0:
                    var.costo_unitario = cu
            from app.services.inventory import apply_movement
            apply_movement(db, "variant", var.id, signo * cantidad, tipo.lower(),
                           f"[{tipo}] {obs_pt}".strip(), user.id)
            # Ingreso contable a producción (2111/7111): base del costo de
            # ventas. No bloquea el movimiento físico si falla.
            if tipo == "INGRESO_PRODUCCION":
                try:
                    from app.services import contabilidad as contab
                    db.refresh(var)
                    ming = round(float(var.costo_unitario or 0) * cantidad, 2)
                    if ming > 0:
                        contab.registrar_ingreso_pt(db, ming, f"SKU {var.sku}",
                                                    user.id)
                except Exception:
                    pass
        except ValueError as e:
            return _err("/inventario/almacen?sub=pt", str(e))
        return RedirectResponse("/inventario/almacen?sub=pt", status_code=303)
    if tipo not in TIPOS_KARDEX_MP:
        # normaliza legacy
        m = {"ENTRADA": "INGRESO_COMPRA", "SALIDA": "SALIDA_TALLER", "MERMA": "AJUSTE_MERMA"}
        tipo = m.get(tipo, "AJUSTE_MERMA")
    if prod_mp is None:
        # Sin variante válida: el movimiento MP exige Producto/Insumo.
        return _err("/inventario/almacen", "selecciona un Producto/Insumo o una Variante/SKU")
    producto_id = prod_mp.id
    try:
        from app.services.inventory import registrar_merma, registrar_ingreso_compra
        if tipo in ("AJUSTE_MERMA", "AJUSTE_INVENTARIO"):
            registrar_merma(db, producto_id, cantidad, user.id, observacion, tipo=tipo)
        elif tipo == "INGRESO_COMPRA":
            registrar_ingreso_compra(db, producto_id, cantidad, user.id, observacion)
        elif tipo == "SALIDA_TALLER":
            # salida por consumo taller
            prod = db.get(ProductoInsumo, producto_id)
            if prod.stock_fisico < cantidad:
                raise ValueError("Stock insuficiente")
            prod.stock_fisico = round(prod.stock_fisico - cantidad, 2)
            db.add(MovimientoKardex(producto_id=producto_id, tipo_movimiento=tipo,
                                    cantidad=cantidad, orden_venta_id=int(orden_venta_id) if orden_venta_id and str(orden_venta_id).isdigit() else None,
                                    usuario_id=user.id, observacion=observacion))
            # mirror legacy
            leg = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
            if leg:
                leg.stock_metros = round(max(0, leg.stock_metros - cantidad), 2)
            db.commit()
        else:  # DEVOLUCION / ENTRADA / SALIDA legacy
            prod = db.get(ProductoInsumo, producto_id)
            if tipo == "SALIDA" and prod.stock_fisico < cantidad:
                raise ValueError("Stock insuficiente")
            prod.stock_fisico = round(prod.stock_fisico + (cantidad if tipo != "SALIDA" else -cantidad), 2)
            db.add(MovimientoKardex(producto_id=producto_id, tipo_movimiento=tipo,
                                    cantidad=cantidad,
                                    orden_venta_id=int(orden_venta_id) if orden_venta_id and str(orden_venta_id).isdigit() else None,
                                    usuario_id=user.id, observacion=observacion))
            db.commit()
    except ValueError as e:
        return _err("/inventario/almacen?sub=mp", str(e))
    return RedirectResponse("/inventario/almacen?sub=mp", status_code=303)


# ---------- Compras a proveedores ----------
def _ocs_unificadas(db: Session) -> list[dict]:
    """UNA sola lista para la vista: spec (canónica) + legacy huérfanas.

    Solo presentación — no altera la lógica de compras_kardex. Las legacy con
    gemela spec por folio se omiten (la ficha spec es la canónica).
    """
    proveedores = {s.id: s.nombre for s in po_svc.proveedores_visibles(db)}
    folios_spec = {o.folio for o in db.query(OrdenCompra.folio).all() if o.folio}
    from sqlalchemy import func as _func
    from app.models.inventario import DetalleOrdenCompra as _Det
    from app.models.purchasing import PurchaseLine as _PL
    n_det = dict(db.query(_Det.orden_compra_id, _func.count(_Det.id)).group_by(
        _Det.orden_compra_id).all())
    n_lin = dict(db.query(_PL.purchase_id, _func.count(_PL.id)).group_by(
        _PL.purchase_id).all())
    filas: list[dict] = []
    for o in db.query(OrdenCompra).order_by(OrdenCompra.id.desc()).limit(60).all():
        filas.append({
            "url": f"/inventario/compras/orden/{o.id}",
            "codigo": o.folio or f"OC-{o.id}",
            "proveedor": proveedores.get(o.proveedor_id, "—"),
            "fecha": str(o.fecha_emision or "")[:10],
            "monto": o.monto_total or 0,
            "estado": normalizar_estado_oc(o.estado),
            "factura": o.numero_factura or "",
            "lineas": n_det.get(o.id, 0),
        })
    for po in db.query(PurchaseOrder).order_by(PurchaseOrder.id.desc()).limit(60).all():
        if po.folio in folios_spec:
            continue  # tiene ficha spec canónica
        filas.append({
            "url": f"/inventario/compras/oc/{po.id}",
            "codigo": po.folio,
            "proveedor": proveedores.get(po.supplier_id, "—"),
            "fecha": str(po.created_at or "")[:10],
            "monto": po.total or 0,
            "estado": normalizar_estado_oc(po.estado),
            "factura": "",
            "lineas": n_lin.get(po.id, 0),
        })
    filas.sort(key=lambda f: (f["fecha"], f["codigo"]), reverse=True)
    return filas


def _sin_lineas(fila: dict) -> bool:
    return fila["estado"] == "DRAFT" and (fila["lineas"] or 0) == 0 and not (fila["monto"] or 0)


@router.get("/compras", response_class=HTMLResponse)
def compras(request: Request, error: str = "", ok: str = "", ver_vacias: str = "",
            db: Session = Depends(get_db), user=Almacen):
    # spec listado unify both tables
    ocs_legacy = db.query(PurchaseOrder).order_by(PurchaseOrder.id.desc()).limit(60).all()
    ocs_spec = db.query(OrdenCompra).order_by(OrdenCompra.id.desc()).limit(60).all()
    filas = _ocs_unificadas(db)
    vacias = sum(1 for f in filas if _sin_lineas(f))
    if not ver_vacias:
        filas = [f for f in filas if not _sin_lineas(f)]
    # combine for display? prefer spec but show legacy
    return templates.TemplateResponse(request, "inventario/compras.html", {
        "user": user, "error": error, "ok": ok, "ver_vacias": ver_vacias,
        "vacias": vacias,
        "proveedores": po_svc.proveedores_visibles(db),
        "ocs": ocs_legacy, "ocs_spec": ocs_spec,
        "ocs_unificadas": filas,
        "normalizar": normalizar_estado_oc})

@router.get("/compras/listado", response_class=HTMLResponse)
def compras_listado(request: Request, error: str = "", ok: str = "", ver_vacias: str = "",
                    db: Session = Depends(get_db), user=Almacen):
    ocs_legacy = db.query(PurchaseOrder).order_by(PurchaseOrder.id.desc()).limit(60).all()
    ocs_spec = db.query(OrdenCompra).order_by(OrdenCompra.id.desc()).limit(60).all()
    filas = _ocs_unificadas(db)
    vacias = sum(1 for f in filas if _sin_lineas(f))
    if not ver_vacias:
        filas = [f for f in filas if not _sin_lineas(f)]
    return templates.TemplateResponse(request, "inventario/compras_listado.html", {
        "user": user, "error": error, "ok": ok, "ver_vacias": ver_vacias,
        "vacias": vacias,
        "proveedores": po_svc.proveedores_visibles(db),
        "ocs": ocs_legacy, "ocs_spec": ocs_spec,
        "ocs_unificadas": filas,
        "normalizar": normalizar_estado_oc})


@router.post("/compras/proveedores")
def crear_proveedor(nombre: str = Form(...), ruc: str = Form(""), telefono: str = Form(""),
                    db: Session = Depends(get_db), user=Almacen):
    db.add(Supplier(nombre=nombre.strip(), ruc=ruc or None, telefono=telefono or None))
    db.commit()
    return RedirectResponse("/inventario/compras", status_code=303)


@router.post("/compras/oc")
def crear_oc(supplier_id: int = Form(...), fecha_entrega: str = Form(""), db: Session = Depends(get_db), user=Almacen):
    # legacy
    po = PurchaseOrder(folio=po_svc.next_po_folio(db), supplier_id=supplier_id,
                       usuario_id=user.id)
    db.add(po)
    db.commit()
    # spec mirror (canónico DRAFT; no toca stock ni contabilidad)
    try:
        oc = OrdenCompra(proveedor_id=supplier_id, estado="DRAFT",
                         monto_total=0, folio=po.folio)
        if fecha_entrega:
            try:
                oc.fecha_entrega_esperada = date.fromisoformat(fecha_entrega)
            except ValueError:
                pass
        db.add(oc)
        db.commit()
    except Exception:
        pass
    return RedirectResponse(f"/inventario/compras/oc/{po.id}", status_code=303)


# Draft Builder: la OC solo se crea con al menos una línea válida.
@router.get("/compras/nueva", response_class=HTMLResponse)
def nueva_oc(request: Request, error: str = "", db: Session = Depends(get_db),
             user=Almacen):
    from app.models.inventario import ProductoInsumo
    return templates.TemplateResponse(request, "inventario/oc_nueva.html", {
        "user": user, "error": error,
        "proveedores": po_svc.proveedores_visibles(db),
        "insumos": db.query(ProductoInsumo).order_by(ProductoInsumo.sku).limit(300).all()})


@router.post("/compras/nueva")
async def crear_oc_builder(request: Request, db: Session = Depends(get_db),
                           user=Almacen):
    from app.models.inventario import DetalleOrdenCompra, ProductoInsumo
    form = await request.form()
    try:
        supplier_id = int(form.get("supplier_id") or 0)
    except (ValueError, TypeError):
        supplier_id = 0
    if not db.get(Supplier, supplier_id):
        return _err("/inventario/compras/nueva", "selecciona un proveedor válido")
    pids = form.getlist("producto_id")
    cants = form.getlist("cantidad")
    precios = form.getlist("precio_unitario")
    lineas: list[tuple] = []
    for pid, cant, precio in zip(pids, cants, precios):
        try:
            prod = db.get(ProductoInsumo, int(pid or 0))
            qty = float(cant or 0)
            pu = float(precio or 0)
        except (ValueError, TypeError):
            continue
        if prod and qty > 0 and pu >= 0:
            lineas.append((prod, qty, pu))
    if not lineas:
        # Sin líneas válidas NO se crea la OC (prevención de vacías S/ 0.00).
        return _err("/inventario/compras/nueva",
                    "agrega al menos una línea válida (insumo + cantidad)")
    folio = po_svc.next_po_folio(db)
    oc = OrdenCompra(proveedor_id=supplier_id, estado="DRAFT", monto_total=0,
                     folio=folio)
    db.add(oc)
    db.flush()
    total = 0.0
    for prod, qty, pu in lineas:
        db.add(DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod.id,
                                 cantidad_solicitada=qty, precio_unitario=pu))
        total = round(total + qty * pu, 2)
    oc.monto_total = total
    db.commit()
    try:
        po = PurchaseOrder(folio=folio, supplier_id=supplier_id, usuario_id=user.id)
        db.add(po)
        db.commit()
    except Exception:
        pass
    return RedirectResponse(f"/inventario/compras/orden/{oc.id}", status_code=303)


@router.post("/compras/borradores/limpiar")
def limpiar_borradores(db: Session = Depends(get_db), user=Almacen):
    """Elimina borradores DRAFT vacíos (sin líneas y S/ 0.00) + gemelo legacy."""
    from app.models.inventario import DetalleOrdenCompra
    from app.models.purchasing import PurchaseLine
    n = 0
    for oc in db.query(OrdenCompra).filter(OrdenCompra.estado == "DRAFT").all():
        tiene = db.query(DetalleOrdenCompra).filter(
            DetalleOrdenCompra.orden_compra_id == oc.id).count()
        if tiene == 0 and not (oc.monto_total or 0):
            if oc.folio:
                po = db.query(PurchaseOrder).filter(
                    PurchaseOrder.folio == oc.folio).first()
                if po and db.query(PurchaseLine).filter(
                        PurchaseLine.purchase_id == po.id).count() == 0:
                    db.delete(po)
            db.delete(oc)
            n += 1
    db.commit()
    return RedirectResponse(f"/inventario/compras?ok=limpios_{n}", status_code=303)


# Spec endpoint OrdenCompra
@router.post("/compras/orden")
def crear_orden_compra(proveedor_id: int = Form(...), fecha_entrega_esperada: str = Form(""),
                       db: Session = Depends(get_db), user=Almacen):    # genera folio OC-xxxxx si no existe (canónico DRAFT)
    folio = po_svc.next_po_folio(db)
    oc = OrdenCompra(proveedor_id=proveedor_id, estado="DRAFT", monto_total=0, folio=folio)
    if fecha_entrega_esperada:
        try:
            oc.fecha_entrega_esperada = date.fromisoformat(fecha_entrega_esperada)
        except ValueError:
            pass
    db.add(oc)
    db.commit()
    # legacy mirror
    try:
        po = PurchaseOrder(folio=folio, supplier_id=proveedor_id, usuario_id=user.id)
        db.add(po)
        db.commit()
    except Exception:
        pass
    return RedirectResponse(f"/inventario/compras/orden/{oc.id}", status_code=303)


@router.get("/compras/oc/{pid}", response_class=HTMLResponse)
def ver_oc(pid: int, request: Request, error: str = "", db: Session = Depends(get_db), user=Almacen):
    po = db.get(PurchaseOrder, pid)
    if not po:
        # try spec id?
        oc = db.get(OrdenCompra, pid)
        if oc:
            return templates.TemplateResponse(request, "inventario/orden_compra_form.html" if _has_template("inventario/orden_compra_form.html") else "inventario/oc.html",
                                              _ctx_orden_compra(db, user, oc, error=error, request=request))
        return HTMLResponse("OC no encontrada", status_code=404)
    oc_twin = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first() if po.folio else None
    return templates.TemplateResponse(request, "inventario/oc.html", {
        "user": user, "error": error, "po": po, "prov": db.get(Supplier, po.supplier_id),
        "oc_twin": oc_twin, "normalizar": normalizar_estado_oc,
        "telas": db.query(Fabric).order_by(Fabric.codigo).limit(500).all(),
        "avios": db.query(Supply).order_by(Supply.codigo).limit(500).all(),
        "lineas": db.query(PurchaseLine).filter(PurchaseLine.purchase_id == pid).all()})


@router.get("/compras/orden/{oid}", response_class=HTMLResponse)
def ver_orden_compra(oid: int, request: Request, error: str = "", db: Session = Depends(get_db), user=Almacen):
    oc = db.get(OrdenCompra, oid)
    if not oc:
        return HTMLResponse("Orden no encontrada", status_code=404)
    return templates.TemplateResponse(request, "inventario/orden_compra_form.html" if _has_template("inventario/orden_compra_form.html") else "inventario/oc.html",
                                      _ctx_orden_compra(db, user, oc, error=error, request=request))


def _ctx_orden_compra(db: Session, user, oc: OrdenCompra, error: str = "", request: Request | None = None) -> dict:
    """Contexto enriquecido de la ficha OC: detalles con producto/CPP, kardex
    valorizado, asientos del Diario y CxP si está BILLED."""
    from app.models.finanzas import AsientoContable, CuentaPorPagar, LineaAsientoContable
    dets = db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id == oc.id).all()
    prods = {p.id: p for p in db.query(ProductoInsumo).filter(
        ProductoInsumo.id.in_([d.producto_id for d in dets] or [0])).all()}
    kardex = db.query(MovimientoKardex).filter(
        MovimientoKardex.orden_compra_id == oc.id).order_by(MovimientoKardex.id).all()
    asiento_ids = sorted({k.asiento_id for k in kardex if k.asiento_id})
    asientos = db.query(AsientoContable).filter(AsientoContable.id.in_(asiento_ids or [0])).all() if asiento_ids else []
    cxp = db.query(CuentaPorPagar).filter(CuentaPorPagar.orden_compra_id == oc.id).all()
    landed_total = (oc.landed_flete or 0) + (oc.landed_seguro or 0) + (oc.landed_otros or 0)
    valorizado = round(sum((k.costo_total or 0) for k in kardex), 2)
    ctx = {"user": user, "error": error, "oc": oc, "po": oc,
           "prov": db.get(Supplier, oc.proveedor_id),
           "detalles": dets, "productos": prods, "lineas": [],
           "insumos": db.query(ProductoInsumo).order_by(ProductoInsumo.nombre).limit(500).all(),
           "kardex": kardex, "asientos": asientos, "cxps": cxp,
           "landed_total": landed_total, "valorizado": valorizado,
           "estado_canon": normalizar_estado_oc(oc.estado),
           "normalizar": normalizar_estado_oc}
    if request is not None:
        ctx["request"] = request
    return ctx


def _has_template(name: str) -> bool:
    from pathlib import Path
    return (BASE_DIR / "app" / "templates" / name).exists()


@router.post("/compras/oc/{pid}/lineas")
def add_linea(pid: int, item_tipo: str = Form(...), item_id: int = Form(...),
              descripcion: str = Form(""), cantidad: float = Form(1),
              costo_unitario: float = Form(0),
              producto_id: str = Form(""), cantidad_solicitada: str = Form(""),
              precio_unitario: str = Form(""),
              db: Session = Depends(get_db), user=Almacen):
    # spec alias params
    if producto_id:
        try:
            item_id = int(producto_id)
            item_tipo = "producto"
        except: pass
    if cantidad_solicitada:
        try:
            cantidad = float(cantidad_solicitada)
        except: pass
    if precio_unitario:
        try:
            costo_unitario = float(precio_unitario)
        except: pass
    db.add(PurchaseLine(purchase_id=pid, item_tipo=item_tipo, item_id=item_id,
                        descripcion=descripcion or f"{item_tipo}#{item_id}",
                        cantidad=cantidad, costo_unitario=costo_unitario))
    db.commit()
    po_svc.recalc_po(db, pid)
    # mirror spec DetalleOrdenCompra if corresponding OrdenCompra exists by folio
    try:
        po = db.get(PurchaseOrder, pid)
        if po and po.folio:
            oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first()
            if oc:
                # producto mapping: try to find ProductoInsumo via fabric/supply id
                prod_id = None
                if item_tipo == "fabric":
                    fab = db.get(Fabric, item_id)
                    if fab:
                        from app.services.inventory import ensure_producto_for_fabric
                        prod = ensure_producto_for_fabric(db, fab)
                        prod_id = prod.id
                elif item_tipo == "supply":
                    sup = db.get(Supply, item_id)
                    if sup:
                        from app.services.inventory import ensure_producto_for_supply
                        prod = ensure_producto_for_supply(db, sup)
                        prod_id = prod.id
                elif item_tipo in ("producto", "insumo"):
                    prod_id = item_id
                if prod_id:
                    db.add(DetalleOrdenCompra(orden_compra_id=oc.id, producto_id=prod_id,
                                             cantidad_solicitada=cantidad, precio_unitario=costo_unitario))
                    # recalc monto_total
                    oc.monto_total = round(sum(d.cantidad_solicitada * d.precio_unitario for d in db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id == oc.id).all()) + cantidad * costo_unitario, 2)
                    db.commit()
    except Exception:
        pass
    return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)


@router.post("/compras/orden/{oid}/detalle")
def add_detalle(oid: int, producto_id: int = Form(...), cantidad_solicitada: float = Form(1),
                precio_unitario: float = Form(0), descuento_unitario: float = Form(0),
                db: Session = Depends(get_db), user=Almacen):
    oc = db.get(OrdenCompra, oid)
    if not oc:
        return HTMLResponse("Orden no encontrada", status_code=404)
    if normalizar_estado_oc(oc.estado) not in ("DRAFT", "APPROVED"):
        return _err(f"/inventario/compras/orden/{oid}", "solo DRAFT/APPROVED aceptan líneas nuevas")
    if cantidad_solicitada <= 0:
        return _err(f"/inventario/compras/orden/{oid}", "cantidad debe ser positiva")
    if (descuento_unitario or 0) < 0 or (descuento_unitario or 0) > (precio_unitario or 0):
        return _err(f"/inventario/compras/orden/{oid}", "descuento inválido")
    db.add(DetalleOrdenCompra(orden_compra_id=oid, producto_id=producto_id,
                             cantidad_solicitada=cantidad_solicitada, precio_unitario=precio_unitario,
                             descuento_unitario=descuento_unitario or 0))
    oc.monto_total = round(oc.monto_total + cantidad_solicitada * (precio_unitario - (descuento_unitario or 0)), 2)
    db.commit()
    # mirror legacy
    try:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.folio == oc.folio).first()
        if po:
            # need item_tipo mapping from producto categoria
            prod = db.get(ProductoInsumo, producto_id)
            item_tipo = "fabric" if prod and prod.categoria in ("TELA","FORRO") else "supply"
            # try to reverse find fabric/supply id via sku
            item_id = producto_id
            if prod:
                fab = db.query(Fabric).filter(Fabric.codigo == prod.sku).first()
                if fab:
                    item_id = fab.id
                    item_tipo = "fabric"
                else:
                    sup = db.query(Supply).filter(Supply.codigo == prod.sku).first()
                    if sup:
                        item_id = sup.id
                        item_tipo = "supply"
            db.add(PurchaseLine(purchase_id=po.id, item_tipo=item_tipo, item_id=item_id,
                                descripcion=prod.nombre if prod else f"producto#{producto_id}",
                                cantidad=cantidad_solicitada, costo_unitario=precio_unitario))
            db.commit()
            po_svc.recalc_po(db, po.id)
    except Exception:
        pass
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/orden/{oid}/aprobar")
def aprobar_orden(oid: int, db: Session = Depends(get_db), user=Almacen):
    try:
        ck_svc.aprobar_oc(db, oid)
    except ValueError as e:
        return _err(f"/inventario/compras/orden/{oid}", str(e))
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/orden/{oid}/cancelar")
def cancelar_orden(oid: int, db: Session = Depends(get_db), user=Almacen):
    try:
        ck_svc.cancelar_oc(db, oid)
    except ValueError as e:
        return _err(f"/inventario/compras/orden/{oid}", str(e))
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/orden/{oid}/landed")
def guardar_landed(oid: int, landed_flete: float = Form(0), landed_seguro: float = Form(0),
                   landed_otros: float = Form(0), db: Session = Depends(get_db), user=Almacen):
    oc = db.get(OrdenCompra, oid)
    if not oc:
        return HTMLResponse("Orden no encontrada", status_code=404)
    if normalizar_estado_oc(oc.estado) in ("BILLED", "CANCELLED"):
        return _err(f"/inventario/compras/orden/{oid}", "OC facturada/cancelada: landed bloqueado")
    if min(landed_flete, landed_seguro, landed_otros) < 0:
        return _err(f"/inventario/compras/orden/{oid}", "landed no admite negativos")
    oc.landed_flete, oc.landed_seguro, oc.landed_otros = landed_flete, landed_seguro, landed_otros
    db.commit()
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/orden/{oid}/facturar")
def facturar_orden(oid: int, numero_factura: str = Form(...), fecha_factura: str = Form(""),
                   db: Session = Depends(get_db), user=Almacen):
    try:
        fecha = date.fromisoformat(fecha_factura) if fecha_factura else None
    except ValueError:
        return _err(f"/inventario/compras/orden/{oid}", "fecha de factura inválida")
    try:
        ck_svc.facturar_oc(db, oid, numero_factura, fecha)
    except ValueError as e:
        return _err(f"/inventario/compras/orden/{oid}", str(e))
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/linea/{lid}/recibir")
def recibir(lid: int, cantidad: float = Form(...),
            db: Session = Depends(get_db), user=Almacen):
    line = db.get(PurchaseLine, lid)
    if not line:
        return HTMLResponse("Línea no encontrada", status_code=404)
    pid = line.purchase_id
    # Vía canónica: si hay OC spec hermanada en estado recepcionable, la recepción
    # pasa por Kardex valorizado + CPP + asiento 241/611 en una sola transacción
    # (el servicio ya espeja el legacy). Si no aplica, cae al flujo legacy.
    try:
        po = db.get(PurchaseOrder, pid)
        oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first() if po and po.folio else None
        if oc is not None and normalizar_estado_oc(oc.estado) in ("APPROVED", "PARTIALLY_RECEIVED"):
            det = _detalle_para_linea(db, oc.id, line)
            if det is not None:
                ck_svc.recepcionar_oc(db, oc.id, {det.id: cantidad}, usuario_id=user.id)
                return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)
    except ValueError as e:
        return _err(f"/inventario/compras/oc/{pid}", str(e))
    except Exception:
        pass
    try:
        po_svc.receive_line(db, lid, cantidad, user.id)
        # mirror spec recepción -> update ProductoInsumo stock_fisico
        try:
            po = db.get(PurchaseOrder, pid)
            if po and po.folio:
                oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first()
                if oc:
                    # encuentra detalle correspondiente por producto
                    # mapping fabric/supply -> producto
                    prod = None
                    if line.item_tipo == "fabric":
                        fab = db.get(Fabric, line.item_id)
                        if fab:
                            from app.services.inventory import ensure_producto_for_fabric, registrar_ingreso_compra
                            prod = ensure_producto_for_fabric(db, fab)
                            # spec kardex + stock físico (legacy ya sumó vía receive_line; aquí solo spec)
                            db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="INGRESO_COMPRA",
                                                    cantidad=cantidad, usuario_id=user.id, observacion=f"OC {po.folio}"))
                            prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
                            # actualizar estado spec
                            detalles = db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id == oc.id).all()
                            total_recibida = sum(d.cantidad_recibida for d in detalles)
                            total_solicitada = sum(d.cantidad_solicitada for d in detalles)
                            # busca detalle
                            for d in detalles:
                                if d.producto_id == prod.id:
                                    d.cantidad_recibida = round(d.cantidad_recibida + cantidad, 2)
                                    break
                            if total_recibida + cantidad >= total_solicitada - 1e-9 and total_solicitada>0:
                                oc.estado = "COMPLETADA" if all(d.cantidad_recibida >= d.cantidad_solicitada -1e-9 for d in db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id==oc.id).all()) else "RECIBIDA_PARCIAL"
                            else:
                                oc.estado = "RECIBIDA_PARCIAL"
                            # sync legacy estado to spec
                            if oc.estado == "COMPLETADA":
                                oc.estado = "COMPLETADA"
                            db.commit()
                    elif line.item_tipo == "supply":
                        sup = db.get(Supply, line.item_id)
                        if sup:
                            from app.services.inventory import ensure_producto_for_supply
                            prod = ensure_producto_for_supply(db, sup)
                            db.add(MovimientoKardex(producto_id=prod.id, tipo_movimiento="INGRESO_COMPRA",
                                                    cantidad=cantidad, usuario_id=user.id, observacion=f"OC {po.folio}"))
                            prod.stock_fisico = round(prod.stock_fisico + cantidad, 2)
                            detalles = db.query(DetalleOrdenCompra).filter(DetalleOrdenCompra.orden_compra_id == oc.id).all()
                            for d in detalles:
                                if d.producto_id == prod.id:
                                    d.cantidad_recibida = round(d.cantidad_recibida + cantidad, 2)
                                    break
                            db.commit()
        except Exception as e:
            print(f"mirror spec error {e}")
    except ValueError:
        pass
    return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)


@router.post("/compras/orden/{oid}/recibir")
def recibir_orden(oid: int, producto_id: int = Form(...), cantidad: float = Form(...),
                  db: Session = Depends(get_db), user=Almacen):
    """Recepción canónica: Kardex ENTRADA valorizado + CPP + asiento DEBE 2411 /
    HABER 6111 en una sola transacción ACID (servicio compras_kardex)."""
    oc = db.get(OrdenCompra, oid)
    if not oc:
        return HTMLResponse("Orden no encontrada", status_code=404)
    det = db.query(DetalleOrdenCompra).filter(
        DetalleOrdenCompra.orden_compra_id == oid,
        DetalleOrdenCompra.producto_id == producto_id).first()
    if not det:
        return _err(f"/inventario/compras/orden/{oid}", "detalle no pertenece a la OC")
    try:
        ck_svc.recepcionar_oc(db, oid, {det.id: cantidad}, usuario_id=user.id)
    except ValueError as e:
        return _err(f"/inventario/compras/orden/{oid}", str(e))
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


def _detalle_para_linea(db: Session, oc_id: int, line: PurchaseLine) -> DetalleOrdenCompra | None:
    """Resuelve el DetalleOrdenCompra gemelo de una PurchaseLine legacy (vía SKU)."""
    sku = None
    if line.item_tipo == "fabric":
        fab = db.get(Fabric, line.item_id)
        sku = fab.codigo if fab else None
    elif line.item_tipo == "supply":
        sup = db.get(Supply, line.item_id)
        sku = sup.codigo if sup else None
    if not sku:
        return None
    prod = db.query(ProductoInsumo).filter(ProductoInsumo.sku == sku).first()
    if not prod:
        return None
    return db.query(DetalleOrdenCompra).filter(
        DetalleOrdenCompra.orden_compra_id == oc_id,
        DetalleOrdenCompra.producto_id == prod.id).first()


@router.post("/compras/orden/{oid}/estado")
def cambiar_estado(oid: int, estado: str = Form(...), db: Session = Depends(get_db), user=Almacen):
    """Cambio de estado por máquina canónica: APPROVED/CANCELLED se ejecutan;
    PARTIALLY_RECEIVED/RECEIVED exigen la acción Recibir (kardex+asiento) y
    BILLED exige Facturar con comprobante (provisión 601+4011/421)."""
    oc = db.get(OrdenCompra, oid)
    if not oc:
        return HTMLResponse("Orden no encontrada", status_code=404)
    destino = normalizar_estado_oc((estado or "").strip())
    if destino not in ("DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "BILLED", "CANCELLED"):
        return _err(f"/inventario/compras/orden/{oid}", f"estado inválido: {estado}")
    try:
        if destino == "APPROVED":
            ck_svc.aprobar_oc(db, oid)
        elif destino == "CANCELLED":
            ck_svc.cancelar_oc(db, oid)
        elif destino in ("PARTIALLY_RECEIVED", "RECEIVED"):
            return _err(f"/inventario/compras/orden/{oid}",
                        "usa la acción Recibir: la recepción genera Kardex + asiento 241/611")
        elif destino == "BILLED":
            return _err(f"/inventario/compras/orden/{oid}",
                        "usa la acción Facturar con nº de comprobante (provisión 601+4011/421)")
        else:  # DRAFT
            return _err(f"/inventario/compras/orden/{oid}", "no se puede volver a DRAFT")
    except ValueError as e:
        return _err(f"/inventario/compras/orden/{oid}", str(e))
    return RedirectResponse(f"/inventario/compras/orden/{oid}", status_code=303)


@router.post("/compras/oc/{pid}/estado")
def cambiar_estado_legacy(pid: int, estado: str = Form(...), db: Session = Depends(get_db), user=Almacen):
    po = db.get(PurchaseOrder, pid)
    if not po:
        return HTMLResponse("OC no encontrada", status_code=404)
    # Si hay gemela spec, la máquina canónica manda (y espeja el legacy).
    try:
        oc = db.query(OrdenCompra).filter(OrdenCompra.folio == po.folio).first() if po.folio else None
        if oc is not None:
            destino = normalizar_estado_oc((estado or "").strip())
            if destino not in ("DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "RECEIVED", "BILLED", "CANCELLED"):
                return _err(f"/inventario/compras/oc/{pid}", f"estado inválido: {estado}")
            if destino == "APPROVED":
                ck_svc.aprobar_oc(db, oc.id)
            elif destino == "CANCELLED":
                ck_svc.cancelar_oc(db, oc.id)
            elif destino in ("PARTIALLY_RECEIVED", "RECEIVED"):
                return _err(f"/inventario/compras/oc/{pid}",
                            "usa la acción Recibir: la recepción genera Kardex + asiento 241/611")
            elif destino == "BILLED":
                return _err(f"/inventario/compras/oc/{pid}",
                            "usa la acción Facturar en la ficha spec (provisión 601+4011/421)")
            else:
                return _err(f"/inventario/compras/oc/{pid}", "no se puede volver a DRAFT")
            return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)
    except ValueError as e:
        return _err(f"/inventario/compras/oc/{pid}", str(e))
    # OC legacy huérfana (sin gemela): cambio directo acotado.
    estado_l = estado.lower()
    if estado_l not in ("borrador", "enviada", "recibida_parcial", "recibida", "cancelada", "completada"):
        return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)
    if estado_l == "completada":
        estado_l = "recibida"
    po.estado = estado_l
    db.commit()
    return RedirectResponse(f"/inventario/compras/oc/{pid}", status_code=303)

