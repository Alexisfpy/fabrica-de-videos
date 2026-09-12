from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_or_create_demo_user

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    user = get_or_create_demo_user(db)
    project = models.Project(
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        format=payload.format,
        target_duration_seconds=payload.target_duration_seconds,
        reference_url=payload.reference_url,
        graphics_style=payload.graphics_style,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[schemas.ProjectSummaryOut])
def list_projects(db: Session = Depends(get_db)):
    user = get_or_create_demo_user(db)
    return (
        db.query(models.Project)
        .filter(models.Project.user_id == user.id)
        .order_by(models.Project.created_at.desc())
        .all()
    )


def _get_project_or_404(db: Session, project_id: str) -> models.Project:
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return project


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    return _get_project_or_404(db, project_id)


@router.put("/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: str, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = _get_project_or_404(db, project_id)
    db.delete(project)
    db.commit()
    return None


@router.get("/{project_id}/assets", response_model=list[schemas.AssetOut])
def list_assets(project_id: str, db: Session = Depends(get_db)):
    _get_project_or_404(db, project_id)
    return db.query(models.Asset).filter(models.Asset.project_id == project_id).all()
