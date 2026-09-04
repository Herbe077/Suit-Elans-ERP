from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import BASE_DIR
from app.core.database import get_db
from app.models.user import User

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    import logging
    try:
        user = db.query(User).filter(User.email == username.lower().strip()).first()
    except Exception as e:
        # BD no disponible o sin migrar (típico primer arranque en producción)
        logging.getLogger("suitelans").exception("login: error de base de datos")
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Servicio no disponible: base de datos sin inicializar. "
                      "Reintenta en un minuto o contacta al administrador."},
            status_code=503,
        )
    try:
        ok = bool(user and user.is_active and user.hashed_password
                  and security.verify_password(password, user.hashed_password))
    except Exception:
        ok = False
    if not ok:
        # Re-render con error 401 (HTMX-friendly)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Credenciales inválidas"}, status_code=401
        )
    from app.core.config import settings as _settings
    try:
        token = security.create_access_token(sub=user.email, role=user.role)
    except Exception:
        import logging
        logging.getLogger("suitelans").exception("login: no se pudo firmar el token")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Error interno al iniciar sesión."},
            status_code=500,
        )
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        key=security.COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=bool(_settings.COOKIE_SECURE),
        max_age=60 * 60 * 8,
        path="/",
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie(key=security.COOKIE_NAME, path="/")
    return resp
