from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import models
from .database import Base, engine
from .routers import misc, pipeline, projects

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