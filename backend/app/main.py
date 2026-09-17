from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import misc, pipeline, projects

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project
from app.services.mock_ai import provision_asset

from app.models import Asset

import shutil
from fastapi import UploadFile, File

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

# Definición de la ruta de assets para evitar NameError
ASSETS_DIR = RENDER_DIR / "assets"

@app.post("/api/projects/{project_id}/shots/{shot_id}/regenerate")
def regenerate_single_shot(project_id: str, shot_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    storyboard = project.storyboard or []
    target_shot = next((s for s in storyboard if s.get("id") == shot_id), None)
    if not target_shot:
        raise HTTPException(status_code=404, detail="Plano no encontrado")

    # 1. Eliminar la imagen previa en disco para forzar la llamada a Cloudflare
    old_file = ASSETS_DIR / f"{project.id}_{shot_id}.png"
    old_file.unlink(missing_ok=True)

    # 2. Generar nueva imagen
    style = getattr(project, "visual_style", None)
    entities = getattr(project, "entities", None)
    updated_asset = provision_asset(target_shot, project.id, style, entities)

    # 3. Actualizar la tabla Asset de SQLAlchemy que lee el frontend
    db_asset = db.query(Asset).filter(Asset.storyboard_item_id == shot_id).first()
    if db_asset:
        db_asset.status = "ready"
        db_asset.error_message = None
        db_asset.url = f"http://localhost:8000/renders/assets/{project.id}_{shot_id}.png"

    project.storyboard = list(storyboard)
    db.commit()

    return {"status": "ok", "asset": updated_asset}

@app.post("/api/projects/{project_id}/shots/{shot_id}/upload")
async def upload_single_shot(
    project_id: str, 
    shot_id: str, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    storyboard = project.storyboard or []
    target_shot = next((s for s in storyboard if s.get("id") == shot_id), None)
    if not target_shot:
        raise HTTPException(status_code=404, detail="Plano no encontrado")

    # Sobrescribir directamente el archivo .png del asset
    dest_path = ASSETS_DIR / f"{project.id}_{shot_id}.png"
    with dest_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Actualizar el registro en la base de datos
    db_asset = db.query(Asset).filter(Asset.storyboard_item_id == shot_id).first()
    if db_asset:
        db_asset.status = "ready"
        db_asset.error_message = None
        db_asset.url = f"http://localhost:8000/renders/assets/{project.id}_{shot_id}.png"

    project.storyboard = list(storyboard)
    db.commit()

    return {
        "status": "ok", 
        "url": f"http://localhost:8000/renders/assets/{project.id}_{shot_id}.png"
    }