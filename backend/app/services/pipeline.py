"""
Orquestación del pipeline.

En el diseño técnico de producción, cada una de estas funciones es un
worker de Celery que se ejecuta en un proceso aparte y se comunica por
Redis. Para que el proyecto se pueda levantar y probar sin infraestructura
adicional, aquí se ejecutan como BackgroundTasks de FastAPI dentro del
mismo proceso, pero mantienen la misma forma (job en la tabla `jobs`,
mismos campos que se actualizan) para que migrar a Celery más adelante
sea sobre todo un cambio de "quién llama a la función", no de qué hace.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from ..config import CREDIT_COSTS
from ..database import SessionLocal
from . import mock_ai


def _charge_credits(db: Session, project: models.Project, reason: str, amount: int):
    user = db.query(models.User).filter(models.User.id == project.user_id).first()
    user.credits -= amount
    db.add(models.CreditTransaction(
        user_id=project.user_id, project_id=project.id, amount=-amount, reason=reason,
    ))
    db.add(user)


def _make_job(db: Session, project_id: str, job_type: str) -> models.Job:
    job = models.Job(project_id=project_id, job_type=job_type, status="running",
                      started_at=datetime.utcnow(), attempts=1)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _finish_job(db: Session, job: models.Job, result: dict | None = None, error: str | None = None):
    job.status = "failed" if error else "done"
    job.result = result
    job.finished_at = datetime.utcnow()
    db.add(job)
    db.commit()


def run_analyze_reference(project_id: str):
    db = SessionLocal()
    try:
        project = db.query(models.Project).get(project_id)
        if not project:
            return
        job = _make_job(db, project_id, "analyze_reference")
        project.status = "analyzing"
        db.add(project)
        db.commit()
        try:
            analysis = mock_ai.analyze_reference(project.reference_url or "")
            project.reference_analysis = analysis
            project.status = "draft"
            _charge_credits(db, project, "reference_analysis", CREDIT_COSTS["analyze_reference"])
            db.add(project)
            db.commit()
            _finish_job(db, job, result=analysis)
        except Exception as exc:  # pragma: no cover - defensivo
            project.status = "failed"
            project.error_message = str(exc)
            db.add(project)
            db.commit()
            _finish_job(db, job, error=str(exc))
    finally:
        db.close()


def run_generate_script(project_id: str):
    db = SessionLocal()
    try:
        project = db.query(models.Project).get(project_id)
        if not project:
            return
        job = _make_job(db, project_id, "generate_script")
        project.status = "scripting"
        db.add(project)
        db.commit()
        try:
            script = mock_ai.generate_script(
                project.title, project.description, project.target_duration_seconds,
                project.reference_analysis,
            )
            project.script = script
            project.status = "draft"
            _charge_credits(db, project, "script_generation", CREDIT_COSTS["generate_script"])
            db.add(project)
            db.commit()
            _finish_job(db, job, result={"blocks": len(script)})
        except Exception as exc:  # pragma: no cover
            project.status = "failed"
            project.error_message = str(exc)
            db.add(project)
            db.commit()
            _finish_job(db, job, error=str(exc))
    finally:
        db.close()


def run_generate_storyboard(project_id: str):
    db = SessionLocal()
    try:
        project = db.query(models.Project).get(project_id)
        if not project or not project.script:
            return
        job = _make_job(db, project_id, "generate_storyboard")
        project.status = "storyboarding"
        db.add(project)
        db.commit()
        try:
            storyboard = mock_ai.generate_storyboard(project.script)
            project.storyboard = storyboard

            # Define el concepto visual del proyecto (estilo + entidades) UNA sola vez,
            # justo después de tener guion + storyboard, para que esté listo antes de
            # generar ninguna imagen. Si el usuario ya lo había editado a mano, se respeta.
            if not project.visual_style_guide:
                project.visual_style_guide = mock_ai.analizar_estilo_proyecto(
                    project.title, project.description
                )

            project.status = "draft"
            _charge_credits(db, project, "storyboard_generation", CREDIT_COSTS["generate_storyboard"])
            db.add(project)
            db.commit()
            _finish_job(db, job, result={"shots": len(storyboard)})
        except Exception as exc:  # pragma: no cover
            project.status = "failed"
            project.error_message = str(exc)
            db.add(project)
            db.commit()
            _finish_job(db, job, error=str(exc))
    finally:
        db.close()


def run_provision(project_id: str):
    db = SessionLocal()
    try:
        project = db.query(models.Project).get(project_id)
        if not project or not project.storyboard:
            return
        job = _make_job(db, project_id, "provision")
        project.status = "provisioning"
        db.add(project)
        db.commit()

        # Salvaguarda: si por lo que sea el proyecto no tiene concepto visual
        # definido todavía (proyectos antiguos, o si se saltó el paso), se genera
        # aquí para no caer nunca en el estilo fijo anterior.
        if not project.visual_style_guide:
            project.visual_style_guide = mock_ai.analizar_estilo_proyecto(
                project.title, project.description
            )
            db.add(project)
            db.commit()

        estilo_global = project.visual_style_guide.get("visual_style")
        entidades = project.visual_style_guide.get("entities", {})

        existing_ids = {a.storyboard_item_id for a in db.query(models.Asset)
                        .filter(models.Asset.project_id == project_id).all()}

        for item in project.storyboard:
            if item["id"] in existing_ids:
                continue  # ya resuelto (permite reintentos parciales)
            result = mock_ai.provision_asset(
                item, project_id=project_id, estilo_global=estilo_global, entidades=entidades
            )
            asset = models.Asset(
                project_id=project_id,
                storyboard_item_id=item["id"],
                type=result["type"],
                source=item["source"],
                status=result["status"],
                url=result["url"],
                duration_seconds=result["duration_seconds"],
                provider=result["provider"],
                error_message=result["error_message"],
            )
            db.add(asset)
            if result["status"] == "ready":
                _charge_credits(db, project, "asset_generation", CREDIT_COSTS["provision_per_asset"])
            db.commit()

        project.status = "draft"
        db.add(project)
        db.commit()
        _finish_job(db, job, result={"items": len(project.storyboard)})
    finally:
        db.close()


def retry_asset(asset_id: str):
    """Reintenta un único plano fallido (acción 'Reabrir' en la UI)."""
    db = SessionLocal()
    try:
        asset = db.query(models.Asset).get(asset_id)
        if not asset:
            return
        project = db.query(models.Project).get(asset.project_id)
        item = next((s for s in (project.storyboard or []) if s["id"] == asset.storyboard_item_id), None)
        if not item:
            return
        style_guide = project.visual_style_guide or {}
        result = mock_ai.provision_asset(
            item,
            project_id=project.id,
            estilo_global=style_guide.get("visual_style"),
            entidades=style_guide.get("entities", {}),
        )
        asset.status = result["status"]
        asset.url = result["url"]
        asset.provider = result["provider"]
        asset.duration_seconds = result["duration_seconds"]
        asset.error_message = result["error_message"]
        db.add(asset)
        if result["status"] == "ready":
            _charge_credits(db, project, "asset_generation_retry", CREDIT_COSTS["provision_per_asset"])
        db.commit()
    finally:
        db.close()


def run_render(project_id: str):
    db = SessionLocal()
    try:
        project = db.query(models.Project).get(project_id)
        if not project:
            return
        job = _make_job(db, project_id, "render")
        project.status = "rendering"
        db.add(project)
        db.commit()
        try:
            result = mock_ai.render_video(project)
            total_duration = sum(a.duration_seconds or 0 for a in project.assets if a.status == "ready")
            project.final_video_url = result["final_video_url"]
            project.actual_duration_seconds = round(total_duration, 1)
            project.status = "completed"
            _charge_credits(db, project, "render", CREDIT_COSTS["render"])
            db.add(project)
            db.commit()
            _finish_job(db, job, result=result)
        except Exception as exc:  # pragma: no cover
            project.status = "failed"
            project.error_message = str(exc)
            db.add(project)
            db.commit()
            _finish_job(db, job, error=str(exc))
    finally:
        db.close()
