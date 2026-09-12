from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import CREDIT_COSTS
from ..database import get_db
from ..deps import get_or_create_demo_user
from ..services.mock_ai import DEFAULT_VOICES

router = APIRouter(prefix="/api/v1", tags=["misc"])


@router.get("/voices", response_model=list[schemas.VoiceOut])
def list_voices(language: str | None = None, tone: str | None = None, db: Session = Depends(get_db)):
    if db.query(models.Voice).count() == 0:
        for v in DEFAULT_VOICES:
            db.add(models.Voice(**v, sample_url=f"https://cdn.example.com/voices/{v['provider_voice_id']}.mp3"))
        db.commit()

    query = db.query(models.Voice)
    if language:
        query = query.filter(models.Voice.language == language)
    if tone:
        query = query.filter(models.Voice.tone == tone)
    return query.all()


@router.post("/credits/check", response_model=schemas.CreditCheckOut)
def check_credits(payload: schemas.CreditCheckRequest, db: Session = Depends(get_db)):
    user = get_or_create_demo_user(db)
    cost_key = {
        "script": "generate_script",
        "storyboard": "generate_storyboard",
        "provision": "provision_per_asset",
        "render": "render",
    }.get(payload.action, "render")
    estimated_cost = CREDIT_COSTS.get(cost_key, 0)
    return schemas.CreditCheckOut(
        sufficient=user.credits >= estimated_cost,
        balance=user.credits,
        estimated_cost=estimated_cost,
    )
