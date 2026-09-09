"""Dependencias: sesión DB, usuario actual (cookie o Bearer), RBAC."""
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core import security
from app.core.database import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _user_from_token(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    payload = security.decode_token(token)
    if not payload or not payload.get("sub"):
        return None
    user = db.query(User).filter(User.email == payload["sub"]).first()
    return user if user and user.is_active else None


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias="suitelans_token"),
    bearer: str | None = Depends(oauth2_scheme),
) -> User | None:
    # Prioridad: cookie HTTPOnly (navegador) > Authorization Bearer (API/HTMX móvil)
    token = cookie_token or bearer
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    return _user_from_token(token, db)


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
    return user

# Roles canónicos del ERP (6): ADMIN, VENTA, SASTRE-MAESTRO, SASTRE-ASISTENTE, ALMACEN, FINANZAS.
# Cada rol canónico mapea a sus variantes legacy para RBAC.
# Legacy: sastre/taller/operario -> SASTRE-ASISTENTE; gerente/contador -> FINANZAS.
_ROLE_ALIASES = {
    "admin": {"ADMIN", "admin", "administrador", "ADMINISTRADOR"},
    "ventas": {"VENTA", "venta", "VENTAS", "ventas", "cajero", "CAJERO", "comercial", "COMERCIAL", "vendedor", "VENDEDOR", "recepcion", "RECEPCION"},
    "venta": {"VENTA", "venta", "VENTAS", "ventas", "cajero", "CAJERO", "comercial", "COMERCIAL"},
    "sastre": {"SASTRE-MAESTRO", "sastre-maestro", "SASTRE-ASISTENTE", "sastre-asistente", "sastre", "SASTRE", "sastre-maestros", "maestro_sastre", "MAESTRO_SASTRE", "maestro-sastre", "sastre_asistente", "SASTRE-ASISTENTE"},
    "sastre-maestro": {"SASTRE-MAESTRO", "sastre-maestro", "sastre-maestros", "SASTRE-MAESTROS", "maestro_sastre", "MAESTRO_SASTRE", "maestro-sastre", "MAESTRO-SASTRE"},
    "sastre-asistente": {"SASTRE-ASISTENTE", "sastre-asistente", "SASTRE-ASISTENTE", "sastre_asistente", "sastre", "SASTRE", "taller", "TALLER", "operario_taller", "OPERARIO_TALLER", "operario-taller", "OPERARIO-TALLER"},
    "taller": {"SASTRE-ASISTENTE", "sastre-asistente", "taller", "TALLER", "operario_taller", "OPERARIO_TALLER", "operario-taller", "OPERARIO-TALLER"},
    "almacen": {"ALMACEN", "almacen", "almacenero", "ALMACENERO"},
    "finanzas": {"FINANZAS", "finanzas", "gerente", "GERENTE", "contador", "CONTADOR"},
    "gerente": {"FINANZAS", "finanzas", "gerente", "GERENTE", "contador", "CONTADOR"},
    "contador": {"FINANZAS", "finanzas", "gerente", "GERENTE", "contador", "CONTADOR"},
}

def _role_match(user_role: str, required_role: str) -> bool:
    if not user_role or not required_role:
        return False
    ur = user_role.strip().lower().replace("_", "-")
    rr = required_role.strip().lower().replace("_", "-")
    if ur == "admin":
        return True
    if ur == rr:
        return True
    # busca grupo del requerido
    for key, group_roles in _ROLE_ALIASES.items():
        key_norm = key.lower().replace("_", "-")
        if rr == key_norm:
            group_lower = {x.lower().replace("_", "-") for x in group_roles}
            if ur in group_lower:
                return True
    return False

def require_roles(*roles: str):
    """Uso: Depends(require_roles('admin', 'sastre')). Admin siempre pasa. Soporta nuevos roles con guion y mayúsculas."""

    def checker(user: User = Depends(get_current_user)) -> User:
        # admin case-insensitive siempre pasa
        if (user.role or "").lower() == "admin":
            return user
        for req in roles:
            if _role_match(user.role, req):
                return user
        # también acepta ADMIN exacto
        if user.role in roles or user.role.lower() in [r.lower() for r in roles]:
            return user
        raise HTTPException(status_code=403, detail="Sin permiso para este módulo")

    return checker
