"""
Para esta demo se usa un único usuario fijo en vez de un flujo de login
completo (JWT/OAuth) — así puedes ejecutar la app sin configurar
autenticación para probar el pipeline. El diseño técnico (sección 7)
especifica JWT de corta duración + refresh token + OAuth2 con Google para
producción; añadirlo no cambia el resto de los routers, solo esta función.
"""
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal

DEMO_USER_EMAIL = "demo@fabrica-de-videos.local"


def get_or_create_demo_user(db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.email == DEMO_USER_EMAIL).first()
    if not user:
        user = models.User(email=DEMO_USER_EMAIL, credits=1000)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
