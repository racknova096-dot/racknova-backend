# ==========================================================
# 🚀 RACKNOVA API (LOCAL) — SISTEMA DE INVENTARIO CON FASTAPI
# Desarrollado por Carlos Zavala
# ==========================================================

# ---------- IMPORTS ----------
# Aquí importamos todas las librerías necesarias para que el servidor funcione.
# FastAPI es el framework que nos permite crear la API.
# SQLModel combina la simplicidad de Pydantic (para validar datos)
# con la potencia de SQLAlchemy (para trabajar con bases de datos relacionales).
# También importamos datetime para manejar fechas y tiempos.

from fastapi import FastAPI, HTTPException, Depends  # FastAPI para crear el servidor, manejar errores y dependencias.
from typing import Annotated, Optional, List         # Tipos de datos avanzados (útil para las anotaciones de variables y validación automática).
from sqlmodel import SQLModel, Field, Session, select
from datetime import datetime                        # Para registrar fechas de creación y actualización.
from sqlalchemy import text                          # Permite ejecutar consultas SQL personalizadas (raw SQL).
from fastapi.middleware.cors import CORSMiddleware
from typing import Annotated, Optional, List

#from auth.routes_auth import router as auth_router




# 🔥 IMPORTA EL ENGINE Y LA SESIÓN DESDE database.py
from database import engine, get_session


# ==========================================================
# 🔧 CONFIGURACIÓN BASE DE LA APLICACIÓN
# ==========================================================

# Aquí creamos una instancia de la aplicación FastAPI.
# Este objeto "app" es el núcleo de nuestro servidor:
# - Contiene las rutas (endpoints)
# - Maneja las solicitudes HTTP
# - Administra los eventos de inicio y cierre del servidor

app = FastAPI(title="RackNova API (Local por ahoraa:) ) 🚀")


# =====================================================
# 🛰️ CONFIGURACIÓN CORS (Cross-Origin Resource Sharing)
# =====================================================
# CORS es un mecanismo de seguridad implementado por los navegadores
# para restringir o permitir las solicitudes HTTP que se hacen desde
# un dominio diferente al del servidor donde está alojada la API.
#
# Por ejemplo:
# Si tu frontend (formulario HTML o React) se ejecuta desde
# http://127.0.0.1:5500 (Live Server)
# y tu backend (FastAPI) corre en http://127.0.0.1:8000,
# el navegador, por seguridad, bloquea las peticiones directas.
# 
# El middleware CORS le indica al navegador qué orígenes, métodos
# y encabezados están permitidos, eliminando esas restricciones.
#
# 🔧 Explicación de los parámetros:
# - allow_origins: Define qué dominios pueden acceder a la API.
#   ["*"] permite a todos los orígenes (útil para pruebas locales).
# - allow_credentials: Permite enviar cookies o tokens de autenticación.
# - allow_methods: Lista los métodos HTTP permitidos (GET, POST, PUT, DELETE, etc.).
# - allow_headers: Especifica qué encabezados se aceptan en las peticiones.
#
# ⚠️ Nota:
# En desarrollo puedes usar ["*"], pero en producción se recomienda
# permitir solo tu dominio oficial, por ejemplo:
# allow_origins=["https://racknova.com"]
# =====================================================

# ---------- CORS CONFIG ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite peticiones desde cualquier origen (útil para pruebas locales)
    # Ejemplo si usas Live Server → ["http://127.0.0.1:5500"]
    allow_credentials=True,
    allow_methods=["*"],  # Permite todos los métodos (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Permite todos los encabezados (por ejemplo Content-Type)
)

# ----------------------------------------------------------
# ⚙️ CONFIGURACIÓN DE CONEXIÓN A LA BASE DE DATOS
# ----------------------------------------------------------
# Usamos XAMPP como servidor local de MySQL, por lo que nuestra base se encuentra en "localhost".
# La URL de conexión sigue el formato:
# "mysql+pymysql://USUARIO:CONTRASEÑA@HOST:PUERTO/NOMBRE_BASE"
# En este caso:
#  - Usuario: root
#  - Contraseña: (vacía)
#  - Host: localhost
#  - Puerto: 3306
#  - Base de datos: racknova_local



# ----------------------------------------------------------
# 🧱 CREACIÓN DEL MOTOR DE CONEXIÓN (ENGINE)
# ----------------------------------------------------------
# El "engine" es el componente que establece la comunicación directa entre SQLModel y MySQL.
# Configuramos algunos parámetros importantes:
# - echo=True: muestra en consola todas las consultas SQL ejecutadas (útil para depuración).
# - pool_pre_ping=True: mantiene viva la conexión evitando que se cierre por inactividad.
# - pool_recycle=280: reinicia las conexiones cada cierto tiempo para evitar errores por tiempo muerto.



# ----------------------------------------------------------
# 🧩 SESIONES DE BASE DE DATOS
# ----------------------------------------------------------
# Las "sesiones" son objetos que representan una conexión activa con la base de datos.
# A través de ellas se pueden realizar operaciones como:
# INSERTAR, CONSULTAR, ACTUALIZAR o ELIMINAR registros.
# FastAPI usa "dependencias" para crear y cerrar sesiones automáticamente.

  

# Aquí definimos un tipo de dependencia para inyectar sesiones fácilmente en las rutas.
SessionDep = Annotated[Session, Depends(get_session)]


# ==========================================================
# 🧾 MODELO DE DATOS — TABLA PRODUCTO
# ==========================================================
# En SQLModel, cada clase que hereda de SQLModel con "table=True" se convierte en una tabla real en MySQL.
# Esta clase define las columnas que tendrá la tabla "producto" y sus tipos de datos.

class Producto(SQLModel, table=True):
    # Clave primaria: identificador único del producto
    id_producto: Optional[int] = Field(default=None, primary_key=True)
    # Código SKU (identificador de inventario)
    sku: str
    # Nombre del producto
    nombre: str
    # Cantidad disponible en inventario
    cantidad: int = 0
    # Descripción opcional
    descripcion: Optional[str] = None
    # Ubicación física del producto en el rack
    rack: str
    nivel: str
    slot: str
    # Fechas automáticas
    fecha_registro: datetime = Field(default_factory=datetime.utcnow)       # se asigna al crear
    ultima_actualizacion: datetime = Field(default_factory=datetime.utcnow) # se actualiza al editar
# ==========================================================
# 📝 MODELO DE MOVIMIENTOS — Tabla 'movimientos'
# ==========================================================

class Movimiento(SQLModel, table=True):
    id_mov: Optional[int] = Field(default=None, primary_key=True)

    # Tipo de acción: ingreso, egreso, edición
    accion: str

    # Datos del producto
    sku: str
    producto: str
    cantidad: int

    # Ubicación física
    ubicacion: str  # Ej: "A-1-3"

    # Usuario que realizó el movimiento
    usuario: str = "Sistema"

    # Fecha y hora del movimiento
    fecha: datetime = Field(default_factory=datetime.utcnow)


# ==========================================================
# 🏗️ EVENTOS DE INICIALIZACIÓN
# ==========================================================
# FastAPI permite ejecutar funciones automáticamente cuando la app inicia.
# Aquí usamos el evento "startup" para crear la tabla "producto" en MySQL si aún no existe.

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)  # Crea las tablas según los modelos definidos arriba.


# ==========================================================
# 🌐 ENDPOINTS PRINCIPALES (RUTAS HTTP)
# ==========================================================
# A partir de aquí, definimos todas las rutas (endpoints) que permiten interactuar con la API.
# Cada endpoint tiene un método HTTP (GET, POST, PUT, DELETE)
# y una URL asociada.

# ---------------------------------------------------------F-
# 🏠 ENDPOINT RAÍZ — Verifica que el servidor esté activo
# ----------------------------------------------------------
@app.get("/")
def home():
    return {"mensaje": "Servidor FastAPI-RackNova corriendo correctamente ✅"}


# ----------------------------------------------------------
# 🧮 ENDPOINT DE VERIFICACIÓN DE CONEXIÓN A LA BASE
# ----------------------------------------------------------
# Esta ruta ejecuta una consulta directa a MySQL para listar las tablas existentes.
# Es útil para confirmar que FastAPI realmente está conectado al motor MySQL correcto.

@app.get("/check_db")
def check_db(session: SessionDep):
    result = session.exec(text("SHOW TABLES;")).all()  # Ejecuta una consulta SQL pura
    tablas = [r[0] for r in result]                   # Extrae los nombres de las tablas
    return {"conexion": "exitosa", "tablas": tablas}  # Devuelve la lista de tablas en formato JSON


# ----------------------------------------------------------
# ➕ CREAR PRODUCTO (POST)
# ----------------------------------------------------------
# Este endpoint permite registrar un nuevo producto en la base de datos.
# Recibe un objeto JSON con los campos del modelo Producto.
# FastAPI convierte automáticamente el JSON en un objeto Python de tipo Producto.

@app.post("/productos")
def crear_producto(producto: Producto, session: SessionDep):
    # ❌ 1. Revisar si ya existe un producto con ese SKU
    existe_sku = session.exec(
        select(Producto).where(Producto.sku == producto.sku)
    ).first()

    if existe_sku:
        raise HTTPException(
            status_code=400,
            detail=f"El SKU '{producto.sku}' ya existe."
        )

    # ❌ 2. Revisar si el mismo slot ya tiene producto
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

    # ✅ SI TODO OK → Guardar
    session.add(producto)
    session.commit()
    session.refresh(producto)
    return producto

# ----------------------------------------------------------
# 📋 LISTAR PRODUCTOS (GET)
# ----------------------------------------------------------
# Este endpoint devuelve todos los productos registrados en la tabla.
# Usa SQLModel.select() para obtener todas las filas del modelo Producto.

@app.get("/productos", response_model=List[Producto])
def listar_productos(session: SessionDep):
    return session.exec(select(Producto)).all()


#modificacion inicia
# ----------------------------------------------------------
# ✏️ ACTUALIZAR PRODUCTO POR SKU (PUT)
# ----------------------------------------------------------
# Permite modificar un producto existente identificándolo por su SKU.
# Si el SKU no existe, se lanza un error HTTP 404.
# Si existe, actualiza los campos y guarda los cambios en la base de datos.

@app.put("/productos/{sku}", response_model=Producto)
def update_producto(sku: str, updated: Producto, session: SessionDep):
    # Busca el producto por SKU
    db_producto = session.query(Producto).filter(Producto.sku == sku).first()
    if not db_producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    # Actualiza los campos editables
    db_producto.nombre = updated.nombre
    db_producto.cantidad = updated.cantidad
    db_producto.descripcion = updated.descripcion
    db_producto.ultima_actualizacion = datetime.now()

    session.commit()
    session.refresh(db_producto)
    return db_producto
#modificacion cierra

# ----------------------------------------------------------
# ❌ ELIMINAR PRODUCTO (DELETE)
# ----------------------------------------------------------
# Este endpoint permite eliminar un producto existente de la base de datos
# utilizando su ID como parámetro en la URL.
# 
# Ejemplo de uso:
#   DELETE http://127.0.0.1:8000/productos/3
# Esto eliminaría el producto con id_producto = 3
#
# Flujo interno:
# 1️⃣ FastAPI recibe el número de ID desde la URL.
# 2️⃣ Busca ese producto en la tabla 'producto' mediante la sesión activa.
# 3️⃣ Si no lo encuentra, devuelve un error 404 (Producto no encontrado).
# 4️⃣ Si lo encuentra, lo elimina definitivamente de la base.
# 5️⃣ Confirma los cambios con un COMMIT y devuelve un mensaje de éxito.

@app.delete("/productos/sku/{sku}")
def eliminar_producto_por_sku(sku: str, session: SessionDep):
    # Buscar producto por su SKU
    db_producto = session.query(Producto).filter(Producto.sku == sku).first()

    if not db_producto:
        raise HTTPException(status_code=404, detail=f"Producto con SKU {sku} no encontrado")

    session.delete(db_producto)
    session.commit()

    return {"mensaje": f"✅ Producto con SKU {sku} eliminado correctamente"}


# ==========================================================
# ➕ REGISTRAR MOVIMIENTO (POST)
# ==========================================================

@app.post("/movimientos", response_model=Movimiento)
def crear_movimiento(mov: Movimiento, session: SessionDep):
    session.add(mov)
    session.commit()
    session.refresh(mov)
    return mov
# ==========================================================
# 📋 OBTENER TODOS LOS MOVIMIENTOS (GET)
# ==========================================================

@app.get("/movimientos", response_model=List[Movimiento])
def listar_movimientos(session: SessionDep):
    return session.exec(select(Movimiento)).all()


@app.delete("/movimientos/{id_mov}")
def eliminar_movimiento(id_mov: int, session: SessionDep):
    mov = session.get(Movimiento, id_mov)
    if not mov:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")

    session.delete(mov)
    session.commit()
    return {"mensaje": "Movimiento eliminado"}




# ==========================================================
# 🔑 INTEGRAR MÓDULO DE AUTENTICACIÓN (COMENTADO POR AHORA)
# ==========================================================
# from auth.routes_auth import router as auth_router
# app.include_router(auth_router)

# ⬇️ SOLO SI RENDER LO NECESITA
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
