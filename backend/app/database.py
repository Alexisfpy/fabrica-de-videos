"""
Configuración de la base de datos.

Usa SQLite por defecto para poder levantar el proyecto sin dependencias
externas. En producción, cambia DATABASE_URL a una cadena de conexión de
PostgreSQL (ver diseño técnico, sección 2) — el resto del código no cambia
porque se apoya en SQLAlchemy.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fabrica_de_videos.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
