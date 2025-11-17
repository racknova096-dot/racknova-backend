# ==========================================================
# 📦 DATABASE CONFIG — RackNova (Render + Railway)
# ==========================================================

from sqlmodel import create_engine, Session
import os

# Obtener DATABASE_URL desde variables de Render
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("❌ ERROR: No se cargó DATABASE_URL en Render")

# Crear el engine global que usará toda la app
engine = create_engine(
    DATABASE_URL,
    echo=True,
    pool_pre_ping=True,
    pool_recycle=280
)

# Sesión para FastAPI
def get_session():
    with Session(engine) as session:
        yield session
# ==========================================================