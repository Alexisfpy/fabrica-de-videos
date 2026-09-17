from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import models
from .database import Base, engine
from .routers import misc, pipeline, projects

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project
from app.services.mock_ai import provision_asset

Base.metadata.create_all(bind=engine)

# Crear las carpetas de salida locales si no existen todavía
RENDER_DIR = Path("renders")
RENDER_DIR.mkdir(exist_ok=True)
(RENDER_DIR / "assets").mkdir(exist_ok=True)

app = FastAPI(
    title="Fábrica de Vídeos API",
    description="Backend del generador de vídeos automatizados con IA.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción, restringir al dominio del frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monta la carpeta renders para servir los vídeos y assets en http://localhost:8000/renders/...
app.mount("/renders", StaticFiles(directory="renders"), name="renders")

app.include_router(projects.router)
app.include_router(pipeline.router)
app.include_router(misc.router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Backend en ejecución"}

router = APIRouter()

@router.post("/api/projects/{project_id}/shots/{shot_id}/regenerate")
def regenerate_single_shot(project_id: str, shot_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    storyboard = project.storyboard or []
    target_shot = next((s for s in storyboard if s.get("id") == shot_id), None)
    if not target_shot:
        raise HTTPException(status_code=404, detail="Plano no encontrado en el storyboard")

    style = getattr(project, "visual_style", None)
    entities = getattr(project, "entities", None)

    # Forzar nueva generación en Cloudflare y sobreescribir el archivo en disco
    updated_asset = provision_asset(target_shot, project.id, style, entities)

    # Actualizar el estado del asset dentro del storyboard
    target_shot["asset"] = updated_asset
    project.storyboard = list(storyboard)
    db.commit()

    return {"status": "ok", "shot": target_shot, "asset": updated_asset}