# ==========================================================
# 🔐 RACKNOVA — Módulo de Autenticación (JSON)
# ==========================================================

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel, Field, Session, select, create_engine
import hashlib

# ==========================================================
# 🧱 CONEXIÓN A BASE DE DATOS
# ==========================================================
DATABASE_URL = "mysql+pymysql://root:@localhost:3306/racknova_local"
engine = create_engine(DATABASE_URL, echo=True)

# ==========================================================
# 👤 MODELO DE USUARIO
# ==========================================================
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)  # 🔑 Clave primaria
    usuario: str
    contrasena: str
    rol: str = "user"  # Valor por defecto

# ==========================================================
# 🚀 CREACIÓN DEL ROUTER
# ==========================================================
router = APIRouter(prefix="/auth", tags=["Autenticación"])

# Crear la tabla si no existe
@router.on_event("startup")
def startup_event():
    SQLModel.metadata.create_all(engine)

# ==========================================================
# 🔑 LOGIN — Validar usuario y contraseña (JSON)
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

        print("🔐 Hash ingresado:", password_hash)
        print("🔒 Hash guardado:", user.contrasena)

        if password_hash != user.contrasena:
            return JSONResponse(
                content={"success": False, "message": "Contraseña incorrecta ❌"},
                status_code=401
            )

        print("✅ Login correcto:", user.usuario)
        return JSONResponse(
            content={
                "success": True,
                "message": f"Bienvenido, {user.usuario} ✅",
                "rol": user.rol
            },
            status_code=200
        )
# ==========================================================