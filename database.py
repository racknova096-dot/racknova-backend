# ==========================================================
# DATABASE CONFIG — RackNova
# Compatible con Railway MySQL y Supabase PostgreSQL
# ==========================================================

from sqlmodel import create_engine, Session
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("❌ ERROR: No se cargó DATABASE_URL en Render")

print(f"✅ DATABASE_URL detectada: {DATABASE_URL[:50]}...")


def normalize_database_url(url: str) -> str:
    """
    Normaliza la URL para SQLAlchemy:
    - Railway/MySQL: mysql:// → mysql+pymysql://
    - Supabase/Postgres: postgres:// o postgresql:// → postgresql+psycopg2://
    - Agrega sslmode=require para Supabase si no viene en la URL.
    """

    if url.startswith("mysql://"):
        url = url.replace("mysql://", "mysql+pymysql://", 1)
        print("✅ URL convertida a mysql+pymysql")

    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        print("✅ URL convertida a postgresql+psycopg2")

    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        print("✅ URL convertida a postgresql+psycopg2")

    if url.startswith("postgresql+psycopg2://") and "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}sslmode=require"
        print("✅ SSL activado para PostgreSQL/Supabase")

    return url


DATABASE_URL = normalize_database_url(DATABASE_URL)

try:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=5,
        max_overflow=10,
    )

    print("✅ Engine creado exitosamente")

except Exception as e:
    print(f"❌ ERROR al crear engine: {e}")
    raise


def get_session():
    with Session(engine) as session:
        yield session
