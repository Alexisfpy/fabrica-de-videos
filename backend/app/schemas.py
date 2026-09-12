from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict


# ---------- Proyectos ----------

class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field("", max_length=20000)
    format: str = Field("horizontal_16_9", pattern="^(horizontal_16_9|shortform_9_16|longform_9_16)$")
    target_duration_seconds: int = Field(420, ge=30, le=1200)
    reference_url: Optional[str] = None
    graphics_style: str = Field("neon", pattern="^(neon|ambar|bloque|cine)$")


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=20000)
    format: Optional[str] = None
    target_duration_seconds: Optional[int] = Field(None, ge=30, le=1200)
    graphics_style: Optional[str] = None


class ScriptBlock(BaseModel):
    id: str
    order: int
    text: str
    estimated_seconds: float


class StoryboardItem(BaseModel):
    id: str
    order: int
    description: str
    shot_type: str  # graphic | stock | ai_generated
    source: str  # library | stock | ai_generated
    duration_seconds: float


class ScriptUpdate(BaseModel):
    script: list[ScriptBlock]


class StoryboardUpdate(BaseModel):
    storyboard: list[StoryboardItem]


class VisualStyleGuide(BaseModel):
    """Concepto visual global del proyecto, calculado una vez y reutilizado en cada plano."""
    visual_style: str = Field(..., min_length=1, max_length=500)
    entities: dict[str, str] = Field(default_factory=dict)


class VisualStyleGuideUpdate(VisualStyleGuide):
    """Permite corregir a mano el estilo/las entidades detectadas antes de generar imágenes."""
    pass


class VoiceSelection(BaseModel):
    voice_id: str
    speed: float = 1.0
    language: str = "es"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    description: str
    format: str
    target_duration_seconds: int
    actual_duration_seconds: Optional[float]
    status: str
    reference_url: Optional[str]
    reference_analysis: Optional[Any]
    script: Optional[Any]
    voice_selection: Optional[Any]
    storyboard: Optional[Any]
    visual_style_guide: Optional[Any]
    graphics_style: str
    final_video_url: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class ProjectSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    format: str
    status: str
    target_duration_seconds: int
    actual_duration_seconds: Optional[float]
    created_at: datetime


# ---------- Pipeline ----------

class AnalyzeReferenceRequest(BaseModel):
    reference_url: str


class JobAccepted(BaseModel):
    job_id: str
    status: str = "queued"


class ProjectStatusOut(BaseModel):
    status: str
    assets_ready: int
    assets_total: int
    assets_error: int
    final_video_url: Optional[str] = None
    error_message: Optional[str] = None


# ---------- Assets ----------

class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    storyboard_item_id: str
    type: str
    source: str
    status: str
    url: Optional[str]
    duration_seconds: Optional[float]
    provider: Optional[str]
    error_message: Optional[str]


# ---------- Voces ----------

class VoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    name: str
    gender: Optional[str]
    language: Optional[str]
    tone: Optional[str]
    sample_url: Optional[str]


# ---------- Créditos ----------

class CreditCheckRequest(BaseModel):
    action: str  # script | storyboard | provision | render


class CreditCheckOut(BaseModel):
    sufficient: bool
    balance: int
    estimated_cost: int
