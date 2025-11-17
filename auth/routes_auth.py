# ==========================================================
# 🔐 RACKNOVA — Módulo de Autenticación
# ==========================================================

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel, Field, Session, select
import hashlib

# Importar el engine global desde database.py
from database import engine, get_session

# ==========================================================
# 👤 MODELO DE USUARIO
# ==========================================================
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    usuario: str
    contrasena: str
    rol: str = "user"

# ==========================================================
# 🚀 CREACIÓN DEL ROUTER
# ==========================================================
router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ==========================================================
# 🔑 LOGIN — Validar usuario y contraseña
# ==========================================================
@router.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")

    with Session(engine) as session:
        user = session.exec(select(User).where(User.usuario == username)).first()

        if not user:
            return JSONResponse(
                content={"success": False, "message": "Usuario no encontrado ❌"},
                status_code=404
            )

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        if password_hash != user.contrasena:
            return JSONResponse(
                content={"success": False, "message": "Contraseña incorrecta ❌"},
                status_code=401
            )

        return JSONResponse(
            content={
                "success": True,
                "message": f"Bienvenido, {user.usuario} ✅",
                "rol": user.rol
            },
            status_code=200
        )
