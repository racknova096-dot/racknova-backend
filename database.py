# ==========================================================
# 📦 DATABASE CONFIG — RackNova (Render + Railway)
# ==========================================================

from sqlmodel import create_engine, Session
import os
import time

# Obtener DATABASE_URL desde variables de Render
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("❌ ERROR: No se cargó DATABASE_URL en Render")

print(f"📡 DATABASE_URL detectada: {DATABASE_URL[:50]}...")

# Asegurar que usa pymysql como driver
if DATABASE_URL.startswith("mysql://"):
    DATABASE_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://", 1)
    print(f"✅ URL convertida a pymysql")

# Crear el engine global que usará toda la app
try:
    engine = create_engine(
        DATABASE_URL,
        echo=True,
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=10,
        max_overflow=20
    )
    print("✅ Engine creado exitosamente")
except Exception as e:
    print(f"❌ ERROR al crear engine: {e}")
    raise

# Sesión para FastAPI
def get_session():
    with Session(engine) as session:
        yield session
# ==========================================================
