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
    user = db.query(User).filter(User.email == username.lower().strip()).first()
    if not user or not user.is_active or not security.verify_password(password, user.hashed_password):
        # Re-render con error 401 (HTMX-friendly)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Credenciales inválidas"}, status_code=401
        )
    token = security.create_access_token(sub=user.email, role=user.role)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        key=security.COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # True en prod con HTTPS (COOKIE_SECURE)
        max_age=60 * 60 * 8,
        path="/",
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie(key=security.COOKIE_NAME, path="/")
    return resp
