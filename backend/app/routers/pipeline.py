import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import pipeline as pipeline_service
from .projects import _get_project_or_404

router = APIRouter(prefix="/api/v1/projects", tags=["pipeline"])


@router.post("/{project_id}/analyze-reference", response_model=schemas.JobAccepted, status_code=202)
def analyze_reference(project_id: str, payload: schemas.AnalyzeReferenceRequest,
                       background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    project.reference_url = payload.reference_url
    db.add(project)
    db.commit()
    background_tasks.add_task(pipeline_service.run_analyze_reference, project_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.post("/{project_id}/generate-script", response_model=schemas.JobAccepted, status_code=202)
def generate_script(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _get_project_or_404(db, project_id)
    background_tasks.add_task(pipeline_service.run_generate_script, project_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.put("/{project_id}/script", response_model=schemas.ProjectOut)
def update_script(project_id: str, payload: schemas.ScriptUpdate, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    project.script = [block.model_dump() for block in payload.script]
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/generate-storyboard", response_model=schemas.JobAccepted, status_code=202)
def generate_storyboard(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    if not project.script:
        raise HTTPException(status_code=409, detail="El proyecto todavía no tiene guion generado")
    background_tasks.add_task(pipeline_service.run_generate_storyboard, project_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.put("/{project_id}/storyboard", response_model=schemas.ProjectOut)
def update_storyboard(project_id: str, payload: schemas.StoryboardUpdate, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    project.storyboard = [item.model_dump() for item in payload.storyboard]
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.put("/{project_id}/style-guide", response_model=schemas.ProjectOut)
def update_style_guide(project_id: str, payload: schemas.VisualStyleGuideUpdate, db: Session = Depends(get_db)):
    """
    Permite corregir a mano el estilo artístico y/o la guía de personajes detectados
    automáticamente, antes (o después) de generar las imágenes con IA. Útil cuando el
    tema es ambiguo o el cliente quiere forzar un estilo concreto para todo el vídeo.
    """
    project = _get_project_or_404(db, project_id)
    project.visual_style_guide = payload.model_dump()
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/analyze-style", response_model=schemas.ProjectOut)
def analyze_style(project_id: str, db: Session = Depends(get_db)):
    """Regenera el concepto visual (estilo + entidades) a partir del título/sinopsis actuales."""
    project = _get_project_or_404(db, project_id)
    from ..services import mock_ai
    project.visual_style_guide = mock_ai.analizar_estilo_proyecto(project.title, project.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.put("/{project_id}/voice", response_model=schemas.ProjectOut)
def set_voice(project_id: str, payload: schemas.VoiceSelection, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    project.voice_selection = payload.model_dump()
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/provision", response_model=schemas.JobAccepted, status_code=202)
def provision(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    if not project.storyboard:
        raise HTTPException(status_code=409, detail="El proyecto todavía no tiene plan visual")
    background_tasks.add_task(pipeline_service.run_provision, project_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.post("/assets/{asset_id}/retry", response_model=schemas.JobAccepted, status_code=202)
def retry_asset(asset_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    asset = db.query(models.Asset).filter(models.Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Plano no encontrado")
    background_tasks.add_task(pipeline_service.retry_asset, asset_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.post("/{project_id}/render", response_model=schemas.JobAccepted, status_code=202)
def render(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    assets = db.query(models.Asset).filter(models.Asset.project_id == project_id).all()
    if not assets:
        raise HTTPException(status_code=409, detail="assets_not_ready")
    pending = [a for a in assets if a.status == "pending"]
    if pending:
        raise HTTPException(status_code=409, detail=f"assets_not_ready: {len(pending)} pendientes")
    background_tasks.add_task(pipeline_service.run_render, project_id)
    return schemas.JobAccepted(job_id=str(uuid.uuid4()))


@router.get("/{project_id}/status", response_model=schemas.ProjectStatusOut)
def get_status(project_id: str, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    assets = db.query(models.Asset).filter(models.Asset.project_id == project_id).all()
    return schemas.ProjectStatusOut(
        status=project.status,
        assets_ready=sum(1 for a in assets if a.status == "ready"),
        assets_total=len(project.storyboard) if project.storyboard else 0,
        assets_error=sum(1 for a in assets if a.status == "error"),
        final_video_url=project.final_video_url,
        error_message=project.error_message,
    )
