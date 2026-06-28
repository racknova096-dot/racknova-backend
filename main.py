# ==========================================================
# 🚀 RACKNOVA API (LOCAL) — SISTEMA DE INVENTARIO CON FASTAPI
# Desarrollado por Carlos Zavala
# ==========================================================

# ---------- IMPORTS ----------
from pydantic import BaseModel
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
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
    costo_proveedor: float = 0
    fecha_registro: datetime = Field(default_factory=datetime.utcnow)
    ultima_actualizacion: datetime = Field(default_factory=datetime.utcnow)

class LoginRequest(BaseModel):
    username: str
    password: str
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

    # Campos financieros nuevos
    costo_proveedor: float = 0
    precio_venta: float = 0
    ingreso_total: float = 0
    costo_total: float = 0
    ganancia: float = 0

class SalidaProducto(SQLModel):
    cantidad_vendida: int
    precio_venta: float
    costo_proveedor: float = 0
    ingreso_total: float = 0
    costo_total: float = 0
    ganancia: float = 0
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
        db_producto.costo_proveedor = updated.costo_proveedor
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
@app.post("/productos/sku/{sku}/salida")
def registrar_salida_producto(sku: str, salida: SalidaProducto, session: SessionDep):
    try:
        db_producto = session.query(Producto).filter(Producto.sku == sku).first()

        if not db_producto:
            raise HTTPException(
                status_code=404,
                detail=f"Producto con SKU {sku} no encontrado"
            )

        if salida.cantidad_vendida <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad vendida debe ser mayor a 0"
            )

        if salida.cantidad_vendida > db_producto.cantidad:
            raise HTTPException(
                status_code=400,
                detail="No puedes vender más cantidad de la existente"
            )

        if salida.cantidad_vendida == db_producto.cantidad:
            session.delete(db_producto)
        else:
            db_producto.cantidad -= salida.cantidad_vendida
            db_producto.ultima_actualizacion = datetime.utcnow()
            session.add(db_producto)

        session.commit()

        return {
            "mensaje": "Salida registrada correctamente",
            "sku": sku,
            "cantidad_vendida": salida.cantidad_vendida,
            "precio_venta": salida.precio_venta,
            "costo_proveedor": salida.costo_proveedor,
            "ingreso_total": salida.ingreso_total,
            "costo_total": salida.costo_total,
            "ganancia": salida.ganancia,
        }

    except Exception as e:
        print(f"❌ Error registrando salida financiera: {e}")
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

@app.get("/finanzas/resumen")
def resumen_financiero(session: SessionDep):
    try:
        movimientos = session.exec(select(Movimiento)).all()

        ingresos = sum(m.ingreso_total or 0 for m in movimientos)
        costos = sum(m.costo_total or 0 for m in movimientos)
        ganancia = sum(m.ganancia or 0 for m in movimientos)

        return {
            "ingresos": ingresos,
            "costos": costos,
            "ganancia": ganancia
        }

    except Exception as e:
        print(f"❌ Error obteniendo resumen financiero: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/finanzas/grafica")
def grafica_financiera(session: SessionDep):
    try:
        movimientos = session.exec(select(Movimiento)).all()

        datos = {}

        for m in movimientos:
            fecha_key = m.fecha.strftime("%Y-%m-%d")

            if fecha_key not in datos:
                datos[fecha_key] = {
                    "fecha": fecha_key,
                    "ingresos": 0,
                    "costos": 0,
                    "ganancia": 0
                }

            datos[fecha_key]["ingresos"] += m.ingreso_total or 0
            datos[fecha_key]["costos"] += m.costo_total or 0
            datos[fecha_key]["ganancia"] += m.ganancia or 0

        return list(datos.values())

    except Exception as e:
        print(f"❌ Error obteniendo gráfica financiera: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/auth/login")
def login(data: LoginRequest):
    if data.username == "admin@racknova.com" and data.password == "admin123":
        return {
            "access_token": "racknova-demo-token",
            "token_type": "bearer",
            "user": {
                "email": data.username,
                "username": data.username,
                "name": "Administrador RackNova",
                "role": "admin"
            }
        }

    raise HTTPException(status_code=401, detail="Credenciales incorrectas")

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
