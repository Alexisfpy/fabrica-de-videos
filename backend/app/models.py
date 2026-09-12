import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship

from .database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False)
    credits = Column(Integer, default=1000, nullable=False)  # saldo de arranque generoso para demo
    plan = Column(String, default="free", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    format = Column(String(30), default="horizontal_16_9")
    target_duration_seconds = Column(Integer, default=420)
    actual_duration_seconds = Column(Float, nullable=True)
    status = Column(String(30), default="draft")
    reference_url = Column(Text, nullable=True)
    reference_analysis = Column(JSON, nullable=True)
    script = Column(JSON, nullable=True)
    voice_selection = Column(JSON, nullable=True)
    storyboard = Column(JSON, nullable=True)
    # Concepto visual global del proyecto: {"visual_style": "...", "entities": {...}}.
    # Se define UNA vez (ver services/mock_ai.analizar_estilo_proyecto) y se reutiliza
    # en cada plano para que el estilo y los personajes sean coherentes en todo el vídeo.
    visual_style_guide = Column(JSON, nullable=True)
    graphics_style = Column(String(30), default="neon")
    final_video_url = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="projects")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    storyboard_item_id = Column(String, nullable=False)
    type = Column(String(20), nullable=False)  # image | video | audio | subtitle
    source = Column(String(20), nullable=False)  # library | stock | ai_generated | upload
    status = Column(String(20), default="pending")  # pending | ready | error
    url = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    provider = Column(String(50), nullable=True)
    asset_metadata = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="assets")


class Voice(Base):
    __tablename__ = "voices"

    id = Column(String, primary_key=True, default=gen_uuid)
    provider = Column(String(30), nullable=False)
    provider_voice_id = Column(String(100), nullable=False)
    name = Column(String(100), nullable=False)
    gender = Column(String(10), nullable=True)
    language = Column(String(10), nullable=True)
    tone = Column(String(30), nullable=True)
    sample_url = Column(Text, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    job_type = Column(String(30), nullable=False)
    status = Column(String(20), default="queued")  # queued | running | done | failed
    attempts = Column(Integer, default=0)
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="jobs")


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    reason = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
