# ==========================================================
# 🚀 RACKNOVA API (LOCAL) — SISTEMA DE INVENTARIO CON FASTAPI
# Desarrollado por Carlos Zavala
# ==========================================================

# ---------- IMPORTS ----------
from fastapi import FastAPI, HTTPException, Depends
from typing import Annotated, Optional, List
from sqlmodel import SQLModel, Field, Session, select
from datetime import datetime
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
import sys

# 🔥 IMPORTA EL ENGINE Y LA SESIÓN DESDE database.py
try:
    from database import engine, get_session
    print("✅ Database module imported successfully")
except Exception as e:
    print(f"❌ ERROR importing database: {e}")
    sys.exit(1)


# ==========================================================
# 🔧 CONFIGURACIÓN BASE DE LA APLICACIÓN
# ==========================================================

app = FastAPI(title="RackNova API 🚀")

# ---------- CORS CONFIG ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Aquí definimos un tipo de dependencia para inyectar sesiones fácilmente en las rutas.
SessionDep = Annotated[Session, Depends(get_session)]


# ==========================================================
# 🧾 MODELO DE DATOS — TABLA PRODUCTO
# ==========================================================

class Producto(SQLModel, table=True):
    id_producto: Optional[int] = Field(default=None, primary_key=True)
    sku: str
    nombre: str
    cantidad: int = 0
    descripcion: Optional[str] = None
    rack: str
    nivel: str
    slot: str
    fecha_registro: datetime = Field(default_factory=datetime.utcnow)
    ultima_actualizacion: datetime = Field(default_factory=datetime.utcnow)

# ==========================================================
# 📝 MODELO DE MOVIMIENTOS — Tabla 'movimientos'
# ==========================================================

class Movimiento(SQLModel, table=True):
    id_mov: Optional[int] = Field(default=None, primary_key=True)
    accion: str
    sku: str
    producto: str
    cantidad: int
    ubicacion: str
    usuario: str = "Sistema"
    fecha: datetime = Field(default_factory=datetime.utcnow)


# ==========================================================
# 🏗️ EVENTOS DE INICIALIZACIÓN
# ==========================================================

@app.on_event("startup")
def on_startup():
    try:
        print("🔄 Creating database tables...")
        SQLModel.metadata.create_all(engine)
        print("✅ Database tables created successfully")
    except Exception as e:
        print(f"❌ ERROR creating tables: {e}")
        sys.exit(1)


# ==========================================================
# 🌐 ENDPOINTS PRINCIPALES (RUTAS HTTP)
# ==========================================================

@app.get("/")
def home():
    return {"mensaje": "Servidor FastAPI-RackNova corriendo correctamente ✅"}


@app.get("/check_db")
def check_db(session: SessionDep):
    try:
        result = session.exec(text("SHOW TABLES;")).all()
        tablas = [r[0] for r in result]
        return {"conexion": "exitosa", "tablas": tablas}
    except Exception as e:
        print(f"❌ Database check error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/productos")
def crear_producto(producto: Producto, session: SessionDep):
    try:
        existe_sku = session.exec(
            select(Producto).where(Producto.sku == producto.sku)
        ).first()

        if existe_sku:
            raise HTTPException(
                status_code=400,
                detail=f"El SKU '{producto.sku}' ya existe."
            )

        existe_slot = session.exec(
            select(Producto).where(
                (Producto.rack == producto.rack) &
                (Producto.nivel == producto.nivel) &
                (Producto.slot == producto.slot)
            )
        ).first()

        if existe_slot:
            raise HTTPException(
                status_code=400,
                detail=f"El slot {producto.rack}-{producto.nivel}-{producto.slot} ya contiene un producto."
            )

        session.add(producto)
        session.commit()
        session.refresh(producto)
        return producto
    except Exception as e:
        print(f"❌ Create product error: {e}")
        raise


@app.get("/productos", response_model=List[Producto])
def listar_productos(session: SessionDep):
    try:
        return session.exec(select(Producto)).all()
    except Exception as e:
        print(f"❌ List products error: {e}")
        raise


@app.put("/productos/{sku}", response_model=Producto)
def update_producto(sku: str, updated: Producto, session: SessionDep):
    try:
        db_producto = session.query(Producto).filter(Producto.sku == sku).first()
        if not db_producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        db_producto.nombre = updated.nombre
        db_producto.cantidad = updated.cantidad
        db_producto.descripcion = updated.descripcion
        db_producto.ultima_actualizacion = datetime.now()

        session.commit()
        session.refresh(db_producto)
        return db_producto
    except Exception as e:
        print(f"❌ Update product error: {e}")
        raise


@app.delete("/productos/sku/{sku}")
def eliminar_producto_por_sku(sku: str, session: SessionDep):
    try:
        db_producto = session.query(Producto).filter(Producto.sku == sku).first()

        if not db_producto:
            raise HTTPException(status_code=404, detail=f"Producto con SKU {sku} no encontrado")

        session.delete(db_producto)
        session.commit()

        return {"mensaje": f"✅ Producto con SKU {sku} eliminado correctamente"}
    except Exception as e:
        print(f"❌ Delete product error: {e}")
        raise


@app.post("/movimientos", response_model=Movimiento)
def crear_movimiento(mov: Movimiento, session: SessionDep):
    try:
        session.add(mov)
        session.commit()
        session.refresh(mov)
        return mov
    except Exception as e:
        print(f"❌ Create movement error: {e}")
        raise


@app.get("/movimientos", response_model=List[Movimiento])
def listar_movimientos(session: SessionDep):
    try:
        return session.exec(select(Movimiento)).all()
    except Exception as e:
        print(f"❌ List movements error: {e}")
        raise


@app.delete("/movimientos/{id_mov}")
def eliminar_movimiento(id_mov: int, session: SessionDep):
    try:
        mov = session.get(Movimiento, id_mov)
        if not mov:
            raise HTTPException(status_code=404, detail="Movimiento no encontrado")

        session.delete(mov)
        session.commit()
        return {"mensaje": "Movimiento eliminado"}
    except Exception as e:
        print(f"❌ Delete movement error: {e}")
        raise


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
