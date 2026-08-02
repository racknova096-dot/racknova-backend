# ==========================================================
# RACKNOVA API — INVENTARIO + CATÁLOGO + LOTES FEFO + IA
# Compatible con MySQL/Railway y PostgreSQL/Supabase
# Seguridad JWT + Roles: admin / operator / viewer
# ==========================================================

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Query, status
from fastapi.security import OAuth2PasswordBearer
from typing import Annotated, Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import SQLModel, Field, Session, select
from zoneinfo import ZoneInfo
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, date, timedelta
import sys
import os
import json
import urllib.request
import urllib.error

from pos_module import registrar_modulo_pos
from ia_copilot import procesar_consulta_ia

try:
    from database import engine, get_session
    print("✅ Database module imported successfully")
except Exception as e:
    print(f"❌ ERROR importing database: {e}")
    sys.exit(1)


# ==========================================================
# CONFIGURACIÓN GENERAL
# ==========================================================

MEXICO_TZ = ZoneInfo("America/Mexico_City")

SECRET_KEY = os.getenv("SECRET_KEY", "racknova-dev-secret-cambiar-en-render")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def mexico_now():
    return datetime.now(MEXICO_TZ).replace(tzinfo=None)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = mexico_now() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def normalizar_texto(value: Optional[str]) -> str:
    return (value or "").strip()


def normalizar_stock_minimo(stock_minimo: Optional[int]) -> int:
    if stock_minimo is not None and stock_minimo > 0:
        return stock_minimo
    return 10


def normalizar_stock_alto(
    stock_minimo: Optional[int],
    stock_alto: Optional[int],
) -> int:
    minimo = normalizar_stock_minimo(stock_minimo)

    if stock_alto is not None and stock_alto > minimo:
        return stock_alto

    return minimo * 3


# ==========================================================
# APP
# ==========================================================

app = FastAPI(title="RackNova API")

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

SessionDep = Annotated[Session, Depends(get_session)]


# ==========================================================
# MODELOS
# ==========================================================
class IAMensajeHistorial(BaseModel):
    rol: Literal["usuario", "asistente"]
    contenido: str = PydanticField(min_length=1)
    
class Producto(SQLModel, table=True):
    id_producto: Optional[int] = Field(default=None, primary_key=True)

    sku: str
    nombre: str
    descripcion: Optional[str] = None
    codigo_barras: Optional[str] = Field(default=None, index=True)
    cantidad: int = 0

    rack: str
    nivel: str
    slot: str

    costo_proveedor: float = 0
    precio_venta_sugerido: float = 0

    # Compatibilidad con frontend/reportes:
    # aquí guardamos la caducidad más próxima vigente.
    caducidad: Optional[date] = None

    stock_minimo: int = 10
    stock_alto: int = 30

    fecha_registro: datetime = Field(default_factory=mexico_now)
    ultima_actualizacion: datetime = Field(default_factory=mexico_now)


class ProductoCatalogo(SQLModel, table=True):
    __tablename__ = "producto_catalogo"

    id_catalogo: Optional[int] = Field(default=None, primary_key=True)

    # El catálogo histórico SOLO guarda identidad fija.
    sku: str = Field(index=True)
    nombre: str = Field(index=True)
    descripcion: Optional[str] = None

    fecha_creacion: datetime = Field(default_factory=mexico_now)
    ultima_actualizacion: datetime = Field(default_factory=mexico_now)


class ProductoLote(SQLModel, table=True):
    __tablename__ = "producto_lote"

    id_lote: Optional[int] = Field(default=None, primary_key=True)

    sku: str = Field(index=True)
    nombre: str

    cantidad_inicial: int
    cantidad_actual: int

    costo_unitario: float = 0
    caducidad: Optional[date] = None

    fecha_ingreso: datetime = Field(default_factory=mexico_now)


class Movimiento(SQLModel, table=True):
    id_mov: Optional[int] = Field(default=None, primary_key=True)

    accion: str
    sku: str
    producto: str
    cantidad: int
    ubicacion: str

    usuario: str = "Sistema"
    fecha: datetime = Field(default_factory=mexico_now)

    costo_proveedor: float = 0
    precio_venta: float = 0
    ingreso_total: float = 0
    costo_total: float = 0
    ganancia: float = 0


class Usuario(SQLModel, table=True):
    __tablename__ = "usuario"

    id_usuario: Optional[int] = Field(default=None, primary_key=True)

    usuario: str = Field(index=True)
    nombre: Optional[str] = None
    rol: str = "operator"

    password_hash: str
    activo: bool = True

    fecha_creacion: datetime = Field(default_factory=mexico_now)
    ultima_actualizacion: datetime = Field(default_factory=mexico_now)
    ultimo_acceso: Optional[datetime] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    usuario: str
    contrasena: str
    rol: str = "operator"
    nombre: Optional[str] = None


class UpdateUserRequest(BaseModel):
    usuario: Optional[str] = None
    contrasena: Optional[str] = None
    rol: Optional[str] = None
    nombre: Optional[str] = None
    activo: Optional[bool] = None


class SalidaProducto(SQLModel):
    cantidad_vendida: int
    precio_venta: float
    costo_proveedor: float = 0
    ingreso_total: float = 0
    costo_total: float = 0
    ganancia: float = 0


class IAMensajeHistorial(BaseModel):
    rol: Literal["usuario", "asistente"]
    contenido: str


class IARequest(BaseModel):
    pregunta: str
    ruta_actual: Optional[str] = None
    pagina_actual: Optional[str] = None
    historial: List[IAMensajeHistorial] = PydanticField(
        default_factory=list,
        max_length=3,
    )


# ==========================================================
# SEGURIDAD / ROLES
# ==========================================================

def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> Usuario:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o sesión expirada.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        if not username:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    usuario = session.exec(
        select(Usuario).where(Usuario.usuario == username)
    ).first()

    if not usuario:
        raise credentials_exception

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo.",
        )

    return usuario


CurrentUserDep = Annotated[Usuario, Depends(get_current_user)]


def require_roles(*allowed_roles: str):
    def role_checker(current_user: CurrentUserDep) -> Usuario:
        if current_user.rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta acción.",
            )

        return current_user

    return role_checker


AdminUserDep = Annotated[Usuario, Depends(require_roles("admin"))]

OperatorUserDep = Annotated[
    Usuario,
    Depends(require_roles("admin", "operator")),
]

ReadUserDep = Annotated[
    Usuario,
    Depends(require_roles("admin", "operator", "viewer")),
]


# ==========================================================
# COMPATIBILIDAD MYSQL / POSTGRESQL
# ==========================================================

def es_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def es_mysql() -> bool:
    return engine.dialect.name == "mysql"


def obtener_columnas(session: Session, tabla: str) -> List[str]:
    try:
        if es_postgres():
            result = session.exec(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :tabla
                    ORDER BY ordinal_position;
                    """
                ).bindparams(tabla=tabla)
            ).all()

            return [str(row[0]).lower() for row in result]

        result = session.exec(text(f"DESCRIBE {tabla};")).all()
        return [str(row[0]).lower() for row in result]

    except Exception as e:
        print(f"⚠️ No se pudieron leer columnas de {tabla}: {e}")
        return []


def agregar_columna_si_falta(
    session: Session,
    tabla: str,
    columna: str,
    definicion_sql: str,
):
    columnas = obtener_columnas(session, tabla)

    if columna.lower() not in columnas:
        try:
            print(f"Agregando columna {tabla}.{columna}...")

            session.exec(
                text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion_sql};")
            )

            session.commit()
            print(f"✅ Columna {tabla}.{columna} agregada")

        except Exception as e:
            session.rollback()
            print(f"⚠️ No se pudo agregar {tabla}.{columna}: {e}")


def modificar_columna_si_existe(
    session: Session,
    tabla: str,
    columna: str,
    definicion_sql: str,
):
    columnas = obtener_columnas(session, tabla)

    if columna.lower() not in columnas:
        return

    try:
        print(f"Ajustando columna heredada {tabla}.{columna}...")

        if es_postgres():
            print(
                f"ℹ️ PostgreSQL detectado. "
                f"Se omite MODIFY COLUMN en {tabla}.{columna}."
            )
            return

        session.exec(
            text(f"ALTER TABLE {tabla} MODIFY COLUMN {columna} {definicion_sql};")
        )

        session.commit()
        print(f"✅ Columna {tabla}.{columna} ajustada")

    except Exception as e:
        session.rollback()
        print(f"⚠️ No se pudo ajustar {tabla}.{columna}: {e}")


def ejecutar_migraciones_ligeras():
    with Session(engine) as session:
        agregar_columna_si_falta(
            session,
            "producto",
            "descripcion",
            "TEXT NULL",
        )

        agregar_columna_si_falta(
            session,
            "producto",
            "codigo_barras",
            "VARCHAR(128) NULL",
        )

        agregar_columna_si_falta(
            session,
            "producto",
            "precio_venta_sugerido",
            "FLOAT DEFAULT 0",
        )

        agregar_columna_si_falta(
            session,
            "producto",
            "caducidad",
            "DATE NULL",
        )

        agregar_columna_si_falta(
            session,
            "producto",
            "stock_minimo",
            "INT NOT NULL DEFAULT 10",
        )

        agregar_columna_si_falta(
            session,
            "producto",
            "stock_alto",
            "INT NOT NULL DEFAULT 30",
        )

        agregar_columna_si_falta(
            session,
            "movimiento",
            "costo_proveedor",
            "FLOAT DEFAULT 0",
        )

        agregar_columna_si_falta(
            session,
            "movimiento",
            "precio_venta",
            "FLOAT DEFAULT 0",
        )

        agregar_columna_si_falta(
            session,
            "movimiento",
            "ingreso_total",
            "FLOAT DEFAULT 0",
        )

        agregar_columna_si_falta(
            session,
            "movimiento",
            "costo_total",
            "FLOAT DEFAULT 0",
        )

        agregar_columna_si_falta(
            session,
            "movimiento",
            "ganancia",
            "FLOAT DEFAULT 0",
        )

        # ======================================================
        # Limpieza de columnas heredadas del catálogo anterior.
        # Esto solo aplica si vienes de una base MySQL vieja.
        # En Supabase/PostgreSQL se omite el MODIFY COLUMN.
        # ======================================================

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "ultimo_costo_proveedor",
            "FLOAT DEFAULT 0",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "costo_promedio",
            "FLOAT DEFAULT 0",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "precio_venta_sugerido",
            "FLOAT DEFAULT 0",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "caducidad",
            "DATE NULL",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "stock_minimo",
            "INT DEFAULT 10",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "stock_alto",
            "INT DEFAULT 30",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "total_ingresado",
            "INT DEFAULT 0",
        )

        modificar_columna_si_existe(
            session,
            "producto_catalogo",
            "total_vendido",
            "INT DEFAULT 0",
        )


def crear_admin_inicial():
    with Session(engine) as session:
        usuarios = session.exec(select(Usuario)).all()

        if usuarios:
            return

        admin = Usuario(
            usuario="admin@racknova.com",
            nombre="Administrador RackNova",
            rol="admin",
            password_hash=hash_password("admin123"),
            activo=True,
            fecha_creacion=mexico_now(),
            ultima_actualizacion=mexico_now(),
        )

        session.add(admin)
        session.commit()

        print("✅ Usuario administrador inicial creado: admin@racknova.com")


# ==========================================================
# STARTUP
# ==========================================================

@app.on_event("startup")
def on_startup():
    try:
        print("Creating database tables...")
        print(f"Motor SQL detectado: {engine.dialect.name}")

        SQLModel.metadata.create_all(engine)

        ejecutar_migraciones_ligeras()
        crear_admin_inicial()

        print("✅ Database tables created/updated successfully")

    except Exception as e:
        print(f"❌ ERROR creating/updating tables: {e}")
        sys.exit(1)


# ==========================================================
# UTILIDADES CATÁLOGO / PRODUCTO / LOTES
# ==========================================================

def buscar_catalogo_por_sku_o_nombre(
    session: Session,
    sku: str,
    nombre: str,
) -> Optional[ProductoCatalogo]:
    sku_limpio = normalizar_texto(sku)
    nombre_limpio = normalizar_texto(nombre)

    if sku_limpio:
        catalogo = session.exec(
            select(ProductoCatalogo).where(ProductoCatalogo.sku == sku_limpio)
        ).first()

        if catalogo:
            return catalogo

    if nombre_limpio:
        catalogo = session.exec(
            select(ProductoCatalogo).where(ProductoCatalogo.nombre == nombre_limpio)
        ).first()

        if catalogo:
            return catalogo

    return None


def crear_catalogo_si_no_existe(
    session: Session,
    sku: str,
    nombre: str,
    descripcion: Optional[str],
) -> ProductoCatalogo:
    catalogo = buscar_catalogo_por_sku_o_nombre(session, sku, nombre)

    if catalogo:
        return catalogo

    catalogo = ProductoCatalogo(
        sku=normalizar_texto(sku),
        nombre=normalizar_texto(nombre),
        descripcion=normalizar_texto(descripcion) or None,
        fecha_creacion=mexico_now(),
        ultima_actualizacion=mexico_now(),
    )

    session.add(catalogo)

    return catalogo


def buscar_producto_por_sku_o_nombre(
    session: Session,
    sku: str,
    nombre: str,
) -> Optional[Producto]:
    sku_limpio = normalizar_texto(sku)
    nombre_limpio = normalizar_texto(nombre)

    if sku_limpio:
        producto = session.exec(
            select(Producto).where(Producto.sku == sku_limpio)
        ).first()

        if producto:
            return producto

    if nombre_limpio:
        producto = session.exec(
            select(Producto).where(Producto.nombre == nombre_limpio)
        ).first()

        if producto:
            return producto

    return None


def buscar_producto_por_ubicacion(
    session: Session,
    rack: str,
    nivel: str,
    slot: str,
) -> Optional[Producto]:
    return session.exec(
        select(Producto).where(
            (Producto.rack == rack)
            & (Producto.nivel == nivel)
            & (Producto.slot == slot)
        )
    ).first()


def calcular_costo_promedio_producto(
    cantidad_actual: int,
    costo_actual: float,
    cantidad_nueva: int,
    costo_nuevo: float,
) -> float:
    cantidad_actual = cantidad_actual or 0
    cantidad_nueva = cantidad_nueva or 0
    costo_actual = costo_actual or 0
    costo_nuevo = costo_nuevo or 0

    total = cantidad_actual + cantidad_nueva

    if total <= 0:
        return 0

    return round(
        ((cantidad_actual * costo_actual) + (cantidad_nueva * costo_nuevo)) / total,
        2,
    )


def crear_lote(
    session: Session,
    producto: Producto,
    cantidad: int,
    costo_unitario: float,
    caducidad: Optional[date],
) -> ProductoLote:
    lote = ProductoLote(
        sku=producto.sku,
        nombre=producto.nombre,
        cantidad_inicial=cantidad,
        cantidad_actual=cantidad,
        costo_unitario=costo_unitario or 0,
        caducidad=caducidad,
        fecha_ingreso=mexico_now(),
    )

    session.add(lote)

    return lote


def obtener_lotes_activos(session: Session, sku: str) -> List[ProductoLote]:
    lotes = session.exec(
        select(ProductoLote).where(
            (ProductoLote.sku == sku)
            & (ProductoLote.cantidad_actual > 0)
        )
    ).all()

    return sorted(
        lotes,
        key=lambda lote: (
            lote.caducidad is None,
            lote.caducidad or date.max,
            lote.fecha_ingreso,
            lote.id_lote or 0,
        ),
    )


def obtener_caducidad_mas_proxima(session: Session, sku: str) -> Optional[date]:
    lotes = obtener_lotes_activos(session, sku)

    for lote in lotes:
        if lote.caducidad:
            return lote.caducidad

    return None


def descontar_lotes_fefo(
    session: Session,
    sku: str,
    cantidad: int,
) -> List[Dict[str, Any]]:
    restante = cantidad
    detalle = []

    for lote in obtener_lotes_activos(session, sku):
        if restante <= 0:
            break

        descontar = min(lote.cantidad_actual, restante)

        lote.cantidad_actual -= descontar
        restante -= descontar

        session.add(lote)

        detalle.append(
            {
                "id_lote": lote.id_lote,
                "cantidad_descontada": descontar,
                "caducidad": str(lote.caducidad) if lote.caducidad else None,
            }
        )

    if restante > 0:
        raise HTTPException(
            status_code=400,
            detail="No hay suficiente cantidad disponible en lotes para esta salida.",
        )

    return detalle


# ==========================================================
# PUNTO DE VENTA — MÓDULO OPCIONAL
# ==========================================================
registrar_modulo_pos(
    app=app,
    get_session=get_session,
    require_roles=require_roles,
    Producto=Producto,
    ProductoLote=ProductoLote,
    Movimiento=Movimiento,
    mexico_now=mexico_now,
    descontar_lotes_fefo=descontar_lotes_fefo,
    obtener_caducidad_mas_proxima=obtener_caducidad_mas_proxima,
)

# ==========================================================
# UTILIDADES IA — RACKNOVA
# ==========================================================


def numero_seguro(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def entero_seguro(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def dinero(value: Any) -> float:
    return round(numero_seguro(value), 2)


def fecha_texto(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None

    return value.isoformat(
        sep=" ",
        timespec="seconds",
    )


def dias_desde(value: Optional[datetime]) -> Optional[int]:
    if not value:
        return None

    return max(
        (
            mexico_now().date()
            - value.date()
        ).days,
        0,
    )


def dias_para_caducar(
    value: Optional[date],
) -> Optional[int]:
    if not value:
        return None

    return (
        value - mexico_now().date()
    ).days


def construir_resumen_inventario(
    productos: List[Producto],
    movimientos: List[Movimiento],
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Calcula todas las cifras utilizando Python.

    DeepSeek no calcula nuevamente los totales.
    Solamente interpreta los resultados y propone acciones.
    """

    ventas: Dict[str, Dict[str, Any]] = {}
    entradas: Dict[str, Dict[str, Any]] = {}
    ediciones: Dict[str, int] = {}

    inconsistencias_financieras: List[
        Dict[str, Any]
    ] = []

    total_ingresos = 0.0
    total_costos = 0.0
    total_vendido = 0
    total_ingresado = 0
    inversion_historica = 0.0
    movimientos_venta = 0
    movimientos_ingreso = 0

    ahora = mexico_now()

    inicio_30 = ahora - timedelta(days=30)
    inicio_60 = ahora - timedelta(days=60)

    unidades_30 = 0
    unidades_30_previas = 0

    ingresos_30 = 0.0
    ingresos_30_previos = 0.0

    movimientos_ordenados = sorted(
        movimientos,
        key=lambda item: item.fecha,
    )

    for mov in movimientos_ordenados:
        sku = str(
            mov.sku or ""
        ).strip()

        accion = str(
            mov.accion or ""
        ).strip().lower()

        if not sku:
            continue

        cantidad = max(
            entero_seguro(mov.cantidad),
            0,
        )

        ingreso = dinero(
            mov.ingreso_total
        )

        costo = dinero(
            mov.costo_total
        )

        ganancia_calculada = dinero(
            ingreso - costo
        )

        ganancia_guardada = dinero(
            mov.ganancia
        )

        if accion == "egreso":
            movimientos_venta += 1
            total_vendido += cantidad
            total_ingresos += ingreso
            total_costos += costo

            if mov.fecha >= inicio_30:
                unidades_30 += cantidad
                ingresos_30 += ingreso

            elif mov.fecha >= inicio_60:
                unidades_30_previas += cantidad
                ingresos_30_previos += ingreso

            item = ventas.setdefault(
                sku,
                {
                    "cantidad": 0,
                    "movimientos": 0,
                    "ingresos": 0.0,
                    "costos": 0.0,
                    "ganancia": 0.0,
                    "primera_venta": None,
                    "ultima_venta": None,
                },
            )

            item["cantidad"] += cantidad
            item["movimientos"] += 1

            item["ingresos"] = dinero(
                item["ingresos"]
                + ingreso
            )

            item["costos"] = dinero(
                item["costos"]
                + costo
            )

            item["ganancia"] = dinero(
                item["ganancia"]
                + ganancia_calculada
            )

            if item["primera_venta"] is None:
                item["primera_venta"] = mov.fecha

            item["ultima_venta"] = mov.fecha

            if abs(
                ganancia_guardada
                - ganancia_calculada
            ) > 0.02:
                inconsistencias_financieras.append(
                    {
                        "id_movimiento": mov.id_mov,
                        "sku": sku,
                        "tipo": (
                            "ganancia_guardada_no_coincide"
                        ),
                        "guardada": ganancia_guardada,
                        "calculada": ganancia_calculada,
                    }
                )

            precio_unitario = dinero(
                mov.precio_venta
            )

            if (
                cantidad > 0
                and precio_unitario > 0
            ):
                ingreso_esperado = dinero(
                    cantidad * precio_unitario
                )

                if abs(
                    ingreso - ingreso_esperado
                ) > 0.02:
                    inconsistencias_financieras.append(
                        {
                            "id_movimiento": mov.id_mov,
                            "sku": sku,
                            "tipo": (
                                "ingreso_total_no_coincide"
                            ),
                            "guardado": ingreso,
                            "calculado": ingreso_esperado,
                        }
                    )

        elif accion == "ingreso":
            movimientos_ingreso += 1
            total_ingresado += cantidad
            inversion_historica += costo

            item = entradas.setdefault(
                sku,
                {
                    "cantidad": 0,
                    "movimientos": 0,
                    "costo": 0.0,
                    "ultima_entrada": None,
                },
            )

            item["cantidad"] += cantidad
            item["movimientos"] += 1

            item["costo"] = dinero(
                item["costo"]
                + costo
            )

            item["ultima_entrada"] = mov.fecha

        elif accion in {
            "edición",
            "edicion",
        }:
            ediciones[sku] = (
                ediciones.get(sku, 0)
                + 1
            )

    total_ingresos = dinero(
        total_ingresos
    )

    total_costos = dinero(
        total_costos
    )

    ganancia_historica = dinero(
        total_ingresos
        - total_costos
    )

    inversion_historica = dinero(
        inversion_historica
    )

    margen_historico = (
        round(
            (
                ganancia_historica
                / total_ingresos
            ) * 100,
            2,
        )
        if total_ingresos > 0
        else 0.0
    )

    porcentaje_recuperado = (
        round(
            (
                total_ingresos
                / inversion_historica
            ) * 100,
            2,
        )
        if inversion_historica > 0
        else 0.0
    )

    roi = (
        round(
            (
                ganancia_historica
                / inversion_historica
            ) * 100,
            2,
        )
        if inversion_historica > 0
        else 0.0
    )

    variacion_unidades = None

    if unidades_30_previas > 0:
        variacion_unidades = round(
            (
                (
                    unidades_30
                    - unidades_30_previas
                )
                / unidades_30_previas
            ) * 100,
            2,
        )

    variacion_ingresos = None

    if ingresos_30_previos > 0:
        variacion_ingresos = round(
            (
                (
                    ingresos_30
                    - ingresos_30_previos
                )
                / ingresos_30_previos
            ) * 100,
            2,
        )

    productos_resumen: List[
        Dict[str, Any]
    ] = []

    diagnosticos_stock: List[
        Dict[str, Any]
    ] = []

    sin_historial_entrada = 0
    sin_costo = 0
    sin_precio = 0

    unidades_stock = 0

    valor_inventario_costo = 0.0
    valor_inventario_venta = 0.0

    for producto in productos:
        venta = ventas.get(
            producto.sku,
            {},
        )

        entrada = entradas.get(
            producto.sku,
            {},
        )

        stock = max(
            entero_seguro(
                producto.cantidad
            ),
            0,
        )

        stock_minimo = max(
            entero_seguro(
                producto.stock_minimo,
                10,
            ),
            0,
        )

        stock_alto = max(
            entero_seguro(
                producto.stock_alto,
                stock_minimo * 3,
            ),
            stock_minimo,
        )

        costo_unitario = dinero(
            producto.costo_proveedor
        )

        precio_sugerido = dinero(
            producto.precio_venta_sugerido
        )

        valor_costo = dinero(
            stock * costo_unitario
        )

        valor_venta = dinero(
            stock * precio_sugerido
        )

        unidades_stock += stock
        valor_inventario_costo += valor_costo
        valor_inventario_venta += valor_venta

        if costo_unitario <= 0:
            sin_costo += 1

        if precio_sugerido <= 0:
            sin_precio += 1

        cantidad_vendida = entero_seguro(
            venta.get("cantidad")
        )

        cantidad_ingresada = entero_seguro(
            entrada.get("cantidad")
        )

        ingresos_producto = dinero(
            venta.get("ingresos")
        )

        costos_producto = dinero(
            venta.get("costos")
        )

        ganancia_producto = dinero(
            ingresos_producto
            - costos_producto
        )

        margen_producto = (
            round(
                (
                    ganancia_producto
                    / ingresos_producto
                ) * 100,
                2,
            )
            if ingresos_producto > 0
            else 0.0
        )

        historial_entrada = (
            cantidad_ingresada > 0
        )

        if not historial_entrada:
            sin_historial_entrada += 1

        porcentaje_vendido = None

        if historial_entrada:
            porcentaje_vendido = round(
                (
                    cantidad_vendida
                    / cantidad_ingresada
                ) * 100,
                2,
            )

            stock_teorico = (
                cantidad_ingresada
                - cantidad_vendida
            )

            diferencia = (
                stock - stock_teorico
            )

            if diferencia != 0:
                diagnosticos_stock.append(
                    {
                        "sku": producto.sku,
                        "producto": producto.nombre,
                        "stock_actual": stock,
                        "stock_segun_ingresos_menos_egresos": (
                            stock_teorico
                        ),
                        "diferencia": diferencia,
                        "ediciones_registradas": (
                            ediciones.get(
                                producto.sku,
                                0,
                            )
                        ),
                        "nota": (
                            "Diagnóstico, no error confirmado. "
                            "Puede existir stock inicial, una "
                            "edición o historial incompleto."
                        ),
                    }
                )

        if stock <= stock_minimo:
            estado_stock = "bajo"

        elif stock >= stock_alto:
            estado_stock = "alto"

        else:
            estado_stock = "normal"

        ultima_venta = venta.get(
            "ultima_venta"
        )

        dias_sin_venta = dias_desde(
            ultima_venta
        )

        dias_sin_ventas_desde_registro = (
            dias_desde(
                producto.fecha_registro
            )
            if not ultima_venta
            else None
        )

        lotes_activos = 0
        unidades_lotes = 0
        caducidad_lote_proxima = None

        if session:
            lotes = obtener_lotes_activos(
                session,
                producto.sku,
            )

            lotes_activos = len(lotes)

            unidades_lotes = sum(
                max(
                    entero_seguro(
                        lote.cantidad_actual
                    ),
                    0,
                )
                for lote in lotes
            )

            fechas_lote = [
                lote.caducidad
                for lote in lotes
                if lote.caducidad is not None
            ]

            if fechas_lote:
                caducidad_lote_proxima = min(
                    fechas_lote
                )

        productos_resumen.append(
            {
                "sku": producto.sku,
                "nombre": producto.nombre,
                "ubicacion": (
                    f"{producto.rack}-"
                    f"{producto.nivel}-"
                    f"{producto.slot}"
                ),
                "stock_actual": stock,
                "stock_minimo": stock_minimo,
                "stock_alto": stock_alto,
                "estado_stock": estado_stock,
                "costo_unitario": costo_unitario,
                "precio_venta_sugerido": (
                    precio_sugerido
                ),
                "valor_stock_costo": valor_costo,
                "valor_stock_venta": valor_venta,
                "cantidad_vendida_registrada": (
                    cantidad_vendida
                ),
                "movimientos_venta_registrados": (
                    entero_seguro(
                        venta.get("movimientos")
                    )
                ),
                "cantidad_ingresada_registrada": (
                    cantidad_ingresada
                    if historial_entrada
                    else None
                ),
                "porcentaje_vendido_sobre_ingresos_registrados": (
                    porcentaje_vendido
                ),
                "confiabilidad_rotacion": (
                    "alta"
                    if historial_entrada
                    else "limitada"
                ),
                "ingresos_ventas": ingresos_producto,
                "costos_vendidos": costos_producto,
                "ganancia_calculada": (
                    ganancia_producto
                ),
                "margen_porcentaje": (
                    margen_producto
                ),
                "ultima_venta": fecha_texto(
                    ultima_venta
                ),
                "dias_sin_venta_registrada": (
                    dias_sin_venta
                ),
                "dias_desde_registro_sin_ventas": (
                    dias_sin_ventas_desde_registro
                ),
                "fecha_registro": fecha_texto(
                    producto.fecha_registro
                ),
                "caducidad": (
                    str(producto.caducidad)
                    if producto.caducidad
                    else None
                ),
                "dias_para_caducar": (
                    dias_para_caducar(
                        producto.caducidad
                    )
                ),
                "lotes_activos": lotes_activos,
                "unidades_en_lotes": (
                    unidades_lotes
                ),
                "caducidad_lote_mas_proxima": (
                    str(caducidad_lote_proxima)
                    if caducidad_lote_proxima
                    else None
                ),
            }
        )

    sin_ventas_todos = [
        producto
        for producto in productos_resumen
        if (
            producto["stock_actual"] > 0
            and producto[
                "cantidad_vendida_registrada"
            ] == 0
        )
    ]

    rankings = {
        "mas_vendidos": sorted(
            productos_resumen,
            key=lambda producto: (
                producto[
                    "cantidad_vendida_registrada"
                ],
                producto["ingresos_ventas"],
            ),
            reverse=True,
        )[:10],

        "sin_ventas": sorted(
            sin_ventas_todos,
            key=lambda producto: (
                producto[
                    "dias_desde_registro_sin_ventas"
                ] or 0,
                producto["valor_stock_costo"],
            ),
            reverse=True,
        )[:15],

        "baja_rotacion": sorted(
            [
                producto
                for producto in productos_resumen
                if (
                    producto["stock_actual"] > 0
                    and producto[
                        "cantidad_vendida_registrada"
                    ] > 0
                    and (
                        producto[
                            "cantidad_vendida_registrada"
                        ] <= 2
                        or (
                            producto[
                                "dias_sin_venta_registrada"
                            ] is not None
                            and producto[
                                "dias_sin_venta_registrada"
                            ] >= 30
                        )
                    )
                )
            ],
            key=lambda producto: (
                -(
                    producto[
                        "dias_sin_venta_registrada"
                    ] or 0
                ),
                producto[
                    "cantidad_vendida_registrada"
                ],
            ),
        )[:15],

        "stock_bajo": sorted(
            [
                producto
                for producto in productos_resumen
                if producto[
                    "estado_stock"
                ] == "bajo"
            ],
            key=lambda producto: (
                producto["stock_actual"]
            ),
        )[:15],

        "stock_alto": sorted(
            [
                producto
                for producto in productos_resumen
                if producto[
                    "estado_stock"
                ] == "alto"
            ],
            key=lambda producto: (
                producto["valor_stock_costo"]
            ),
            reverse=True,
        )[:15],

        "vencidos": sorted(
            [
                producto
                for producto in productos_resumen
                if (
                    producto[
                        "dias_para_caducar"
                    ] is not None
                    and producto[
                        "dias_para_caducar"
                    ] < 0
                )
            ],
            key=lambda producto: (
                producto["dias_para_caducar"]
            ),
        )[:15],

        "por_caducar": sorted(
            [
                producto
                for producto in productos_resumen
                if (
                    producto[
                        "dias_para_caducar"
                    ] is not None
                    and 0
                    <= producto[
                        "dias_para_caducar"
                    ]
                    <= 30
                )
            ],
            key=lambda producto: (
                producto["dias_para_caducar"]
            ),
        )[:15],

        "mas_rentables": sorted(
            [
                producto
                for producto in productos_resumen
                if producto[
                    "ingresos_ventas"
                ] > 0
            ],
            key=lambda producto: (
                producto[
                    "ganancia_calculada"
                ]
            ),
            reverse=True,
        )[:10],

        "margen_bajo": sorted(
            [
                producto
                for producto in productos_resumen
                if (
                    producto[
                        "ingresos_ventas"
                    ] > 0
                    and producto[
                        "margen_porcentaje"
                    ] < 15
                )
            ],
            key=lambda producto: (
                producto[
                    "margen_porcentaje"
                ]
            ),
        )[:10],
    }

    porcentaje_sin_historial = (
        (
            sin_historial_entrada
            / len(productos)
        ) * 100
        if productos
        else 0
    )

    if inconsistencias_financieras:
        nivel_confiabilidad = "limitada"

    elif porcentaje_sin_historial > 30:
        nivel_confiabilidad = "limitada"

    elif (
        sin_historial_entrada
        or diagnosticos_stock
        or sin_costo
    ):
        nivel_confiabilidad = "media"

    else:
        nivel_confiabilidad = "alta"

    return {
        "fecha_analisis": fecha_texto(
            ahora
        ),

        "metricas_calculadas_backend": {
            "total_productos": len(productos),

            "unidades_stock_actual": (
                unidades_stock
            ),

            "valor_inventario_a_costo": dinero(
                valor_inventario_costo
            ),

            "valor_inventario_a_precio_sugerido": (
                dinero(
                    valor_inventario_venta
                )
            ),

            "movimientos_totales": len(
                movimientos
            ),

            "movimientos_venta_registrados": (
                movimientos_venta
            ),

            "unidades_vendidas_registradas": (
                total_vendido
            ),

            "ingresos_totales_ventas": (
                total_ingresos
            ),

            "costos_totales_vendidos": (
                total_costos
            ),

            "ganancia_historica_calculada": (
                ganancia_historica
            ),

            "margen_historico_porcentaje": (
                margen_historico
            ),

            "inversion_historica_inventario": (
                inversion_historica
            ),

            "capital_recuperado": (
                total_ingresos
            ),

            "pendiente_por_recuperar": dinero(
                max(
                    inversion_historica
                    - total_ingresos,
                    0,
                )
            ),

            "porcentaje_recuperado": (
                porcentaje_recuperado
            ),

            "roi_inventario_porcentaje": roi,

            "unidades_ingresadas_registradas": (
                total_ingresado
            ),

            "movimientos_ingreso_registrados": (
                movimientos_ingreso
            ),

            "productos_sin_ventas_registradas": (
                len(sin_ventas_todos)
            ),

            "capital_en_productos_sin_ventas": (
                dinero(
                    sum(
                        producto[
                            "valor_stock_costo"
                        ]
                        for producto
                        in sin_ventas_todos
                    )
                )
            ),
        },

        "comparacion_30_dias": {
            "unidades_ultimos_30_dias": (
                unidades_30
            ),

            "unidades_30_dias_anteriores": (
                unidades_30_previas
            ),

            "variacion_unidades_porcentaje": (
                variacion_unidades
            ),

            "ingresos_ultimos_30_dias": (
                dinero(ingresos_30)
            ),

            "ingresos_30_dias_anteriores": (
                dinero(ingresos_30_previos)
            ),

            "variacion_ingresos_porcentaje": (
                variacion_ingresos
            ),

            "nota": (
                "La variación es null cuando "
                "el periodo anterior no tiene "
                "una base distinta de cero."
            ),
        },

        "calidad_datos": {
            "nivel_confiabilidad": (
                nivel_confiabilidad
            ),

            "productos_sin_historial_entrada": (
                sin_historial_entrada
            ),

            "productos_sin_costo": sin_costo,

            "productos_sin_precio": (
                sin_precio
            ),

            "diagnosticos_stock": len(
                diagnosticos_stock
            ),

            "inconsistencias_financieras": (
                len(
                    inconsistencias_financieras
                )
            ),

            "nota": (
                "Las ganancias se recalcularon "
                "como ingresos menos costos. "
                "Los diagnósticos de stock no "
                "son errores confirmados."
            ),
        },

        "alertas_calidad": {
            "diagnosticos_stock": (
                diagnosticos_stock[:10]
            ),

            "inconsistencias_financieras": (
                inconsistencias_financieras[
                    :10
                ]
            ),
        },

        "criterios": {
            "sin_ventas": (
                "Stock mayor que cero y ninguna "
                "unidad vendida en los movimientos "
                "registrados."
            ),

            "baja_rotacion": (
                "Tiene ventas registradas, pero "
                "vendió dos unidades o menos, o "
                "lleva 30 días o más sin venta "
                "registrada."
            ),

            "stock_bajo": (
                "Stock actual menor o igual al "
                "mínimo."
            ),

            "stock_alto": (
                "Stock actual mayor o igual al "
                "stock alto."
            ),
        },

        "rankings": rankings,

        "productos": productos_resumen,
    }


def seleccionar_contexto_ia(
    pregunta: str,
    resumen: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Envía únicamente el contexto relacionado con la pregunta.
    Esto reduce tokens y evita saturar al modelo.
    """

    texto = pregunta.lower()
    rankings = resumen["rankings"]

    contexto: Dict[str, Any] = {
        "fecha_analisis": (
            resumen["fecha_analisis"]
        ),

        "metricas_calculadas_backend": (
            resumen[
                "metricas_calculadas_backend"
            ]
        ),

        "comparacion_30_dias": (
            resumen["comparacion_30_dias"]
        ),

        "calidad_datos": (
            resumen["calidad_datos"]
        ),

        "criterios": resumen["criterios"],
    }

    palabras_ventas = [
        "venta",
        "vendo",
        "vender",
        "vendido",
        "rotación",
        "rotacion",
        "demanda",
        "promoción",
        "promocion",
    ]

    if any(
        palabra in texto
        for palabra in palabras_ventas
    ):
        contexto["ventas"] = {
            "mas_vendidos": (
                rankings["mas_vendidos"]
            ),

            "sin_ventas": (
                rankings["sin_ventas"]
            ),

            "baja_rotacion": (
                rankings["baja_rotacion"]
            ),
        }

    palabras_ubicacion = [
        "ubicación",
        "ubicacion",
        "mover",
        "reubicar",
        "rack",
        "slot",
        "posición",
        "posicion",
    ]

    if any(
        palabra in texto
        for palabra in palabras_ubicacion
    ):
        contexto["ubicacion"] = {
            "mas_vendidos": (
                rankings["mas_vendidos"]
            ),

            "sin_ventas": (
                rankings["sin_ventas"]
            ),

            "nota": (
                "La ubicación puede probarse, "
                "pero los datos no demuestran "
                "por sí solos causalidad."
            ),
        }

    palabras_stock = [
        "stock",
        "inventario",
        "resurtir",
        "restock",
        "comprar",
        "compra",
        "reorden",
        "desabasto",
        "exceso",
    ]

    if any(
        palabra in texto
        for palabra in palabras_stock
    ):
        contexto["stock"] = {
            "stock_bajo": (
                rankings["stock_bajo"]
            ),

            "stock_alto": (
                rankings["stock_alto"]
            ),
        }

    palabras_finanzas = [
        "ganancia",
        "margen",
        "rentable",
        "rentabilidad",
        "dinero",
        "ingreso",
        "costo",
        "capital",
        "inversión",
        "inversion",
        "finanzas",
        "roi",
    ]

    if any(
        palabra in texto
        for palabra in palabras_finanzas
    ):
        contexto["finanzas"] = {
            "mas_rentables": (
                rankings["mas_rentables"]
            ),

            "margen_bajo": (
                rankings["margen_bajo"]
            ),
        }

    palabras_caducidad = [
        "caduca",
        "caducidad",
        "vencido",
        "vencimiento",
        "descuento",
        "merma",
        "lote",
    ]

    if any(
        palabra in texto
        for palabra in palabras_caducidad
    ):
        contexto["caducidad"] = {
            "vencidos": (
                rankings["vencidos"]
            ),

            "por_caducar": (
                rankings["por_caducar"]
            ),
        }

    coincidencias = []

    for producto in resumen["productos"]:
        sku = str(
            producto["sku"] or ""
        ).lower()

        nombre = str(
            producto["nombre"] or ""
        ).lower()

        palabras_nombre = [
            palabra
            for palabra
            in nombre.replace(
                "-",
                " ",
            ).split()
            if len(palabra) >= 4
        ]

        if (
            (
                sku
                and sku in texto
            )
            or (
                nombre
                and nombre in texto
            )
            or any(
                palabra in texto
                for palabra in palabras_nombre
            )
        ):
            coincidencias.append(
                producto
            )

    if coincidencias:
        contexto[
            "productos_consultados"
        ] = coincidencias[:10]

    if len(contexto) == 5:
        contexto["resumen_general"] = {
            "mas_vendidos": (
                rankings[
                    "mas_vendidos"
                ][:5]
            ),

            "sin_ventas": (
                rankings[
                    "sin_ventas"
                ][:5]
            ),

            "stock_bajo": (
                rankings[
                    "stock_bajo"
                ][:5]
            ),

            "por_caducar": (
                rankings[
                    "por_caducar"
                ][:5]
            ),

            "mas_rentables": (
                rankings[
                    "mas_rentables"
                ][:5]
            ),
        }

    if (
        resumen["calidad_datos"][
            "nivel_confiabilidad"
        ]
        != "alta"
    ):
        contexto["alertas_calidad"] = (
            resumen["alertas_calidad"]
        )

    return contexto


def generar_respuesta_fallback(
    pregunta: str,
    resumen: Dict[str, Any],
) -> str:
    metricas = resumen[
        "metricas_calculadas_backend"
    ]

    calidad = resumen[
        "calidad_datos"
    ]

    sin_ventas = resumen[
        "rankings"
    ]["sin_ventas"][:5]

    stock_bajo = resumen[
        "rankings"
    ]["stock_bajo"][:5]

    partes = [
        (
            "RackNova IA utilizó el motor "
            "interno de análisis."
        ),
        "",
        (
            f"Hay "
            f"{metricas['total_productos']} "
            f"productos y "
            f"{metricas['unidades_stock_actual']} "
            f"unidades en stock. Los ingresos "
            f"históricos registrados son "
            f"${metricas['ingresos_totales_ventas']:.2f}; "
            f"la ganancia calculada es "
            f"${metricas['ganancia_historica_calculada']:.2f}."
        ),
    ]

    if (
        calidad["nivel_confiabilidad"]
        != "alta"
    ):
        partes.extend(
            [
                "",
                (
                    "Advertencia: el historial tiene "
                    "confiabilidad "
                    f"{calidad['nivel_confiabilidad']}. "
                    "Las cifras se refieren a los "
                    "movimientos registrados."
                ),
            ]
        )

    if sin_ventas:
        partes.extend(
            [
                "",
                (
                    "Productos sin ventas "
                    "registradas:"
                ),
            ]
        )

        for producto in sin_ventas:
            partes.append(
                f"- {producto['nombre']} "
                f"({producto['sku']}), "
                f"stock "
                f"{producto['stock_actual']}, "
                f"ubicación "
                f"{producto['ubicacion']}."
            )

        partes.append(
            "Prueba una sola acción durante "
            "7 a 14 días y compara los "
            "resultados. La causa exacta no "
            "puede afirmarse sin datos de "
            "precio de mercado, promoción "
            "y demanda."
        )

    if stock_bajo:
        partes.extend(
            [
                "",
                "Stock bajo:",
            ]
        )

        for producto in stock_bajo:
            partes.append(
                f"- {producto['nombre']}: "
                f"{producto['stock_actual']} "
                f"unidades; mínimo "
                f"{producto['stock_minimo']}."
            )

    return "\n".join(partes)


def solicitar_deepseek(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    user_id: str,
) -> Dict[str, Any]:
    payload = {
        "model": model,

        "messages": messages,

        "thinking": {
            "type": "disabled",
        },

        "temperature": 0.1,

        "max_tokens": max_tokens,

        "stream": False,

        "user_id": user_id,
    }

    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",

        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8"),

        headers={
            "Content-Type": (
                "application/json"
            ),

            "Authorization": (
                f"Bearer {api_key}"
            ),
        },

        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60,
        ) as response:
            data = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except urllib.error.HTTPError as error:
        body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"DeepSeek respondió HTTP "
            f"{error.code}: {body}"
        ) from error

    except urllib.error.URLError as error:
        raise RuntimeError(
            "No fue posible conectar con "
            f"DeepSeek: {error.reason}"
        ) from error

    choices = data.get(
        "choices"
    ) or []

    if not choices:
        return {
            "content": "",
            "finish_reason": "empty",
            "usage": (
                data.get("usage")
                or {}
            ),
        }

    choice = choices[0]

    return {
        "content": (
            choice
            .get("message", {})
            .get("content", "")
            .strip()
        ),

        "finish_reason": (
            choice.get(
                "finish_reason"
            )
        ),

        "usage": (
            data.get("usage")
            or {}
        ),
    }


def sumar_tokens(
    total: Dict[str, int],
    usage: Dict[str, Any],
) -> None:
    campos = [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ]

    for campo in campos:
        total[campo] += entero_seguro(
            usage.get(campo)
        )


def llamar_deepseek(
    pregunta: str,
    resumen: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    api_key = os.getenv(
        "DEEPSEEK_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "Falta configurar "
            "DEEPSEEK_API_KEY."
        )

    model = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-v4-flash",
    )

    user_id_limpio = "".join(
        caracter
        if (
            caracter.isalnum()
            or caracter in "-_"
        )
        else "_"
        for caracter in user_id
    )[:512]

    contexto = seleccionar_contexto_ia(
        pregunta,
        resumen,
    )

    system_prompt = """
Eres RackNova IA, el asistente de la plataforma RackNova.

Ayudas al usuario a operar la plataforma y a comprender su inventario,
ventas, movimientos, caducidades y ubicaciones.

Reglas de respuesta:

1. Recuerda dale al cliente siempre con amabilidad
2. Si la respuesta puede darse en una o dos oraciones, no agregues explicaciones adicionales.
3. No incluyas recomendaciones que el usuario no solicitó, salvo que exista
   un riesgo importante o un error de operación.
4. No uses listas ni viñetas salvo que el usuario solicite pasos, opciones
   o una comparación.
5. Para instrucciones, indica primero la página y después la acción exacta.
6. No inventes botones, páginas, datos, productos ni resultados.
7. Usa únicamente información proporcionada por las herramientas de RackNova.
8. Si faltan datos, dilo claramente y solicita solo el dato necesario.
9. Cuando haya muchos resultados, muestra los más relevantes y menciona
   cuántos resultados adicionales existen.
10. Mantén un tono directo, profesional, fácil de entender y amigable, que se sienta personalizado.
11. No mas viñetas nunca
12.Usa el historial únicamente para comprender referencias de la pregunta
actual, como “ese producto”, “el segundo” o “explícalo mejor”.
13.No repitas información anterior si no es necesaria.
14.Si la pregunta actual puede responderse por sí sola, ignora el historial.
Mantén la respuesta breve y directa.
""".strip()

    user_prompt = f"""
PREGUNTA

{pregunta}

DATOS DE RACKNOVA

{json.dumps(
    contexto,
    ensure_ascii=False,
    default=str,
    separators=(",", ":"),
)}

Usa únicamente estos datos para mencionar cifras.

Cuando una causa no pueda probarse, identifica claramente
la hipótesis y explica cómo comprobarla.
""".strip()

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    uso = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    primera = solicitar_deepseek(
        api_key=api_key,
        model=model,
        messages=messages,
        max_tokens=1100,
        user_id=user_id_limpio,
    )

    sumar_tokens(
        uso,
        primera["usage"],
    )

    contenido = primera["content"]

    finish_reason = primera[
        "finish_reason"
    ]

    continuaciones = 0

    # Solo genera una continuación cuando
    # DeepSeek confirma que la respuesta se cortó.
    if (
        contenido
        and finish_reason == "length"
    ):
        continuaciones = 1

        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": contenido,
                },
                {
                    "role": "user",
                    "content": (
                        "Continúa exactamente desde "
                        "donde terminaste. No repitas "
                        "datos ni encabezados. Termina "
                        "las recomendaciones y la "
                        "conclusión en un máximo de "
                        "180 palabras."
                    ),
                },
            ]
        )

        segunda = solicitar_deepseek(
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=500,
            user_id=user_id_limpio,
        )

        sumar_tokens(
            uso,
            segunda["usage"],
        )

        if segunda["content"]:
            contenido = (
                f"{contenido}\n\n"
                f"{segunda['content']}"
            )

        finish_reason = segunda[
            "finish_reason"
        ]

    if not contenido:
        return {
            "respuesta": (
                generar_respuesta_fallback(
                    pregunta,
                    resumen,
                )
            ),

            "fuente": (
                "motor_interno_fallback"
            ),

            "completa": True,

            "continuaciones": 0,

            "finish_reason": "fallback",

            "uso_tokens": uso,
        }

    return {
        "respuesta": contenido.strip(),

        "fuente": "deepseek",

        "completa": (
            finish_reason != "length"
        ),

        "continuaciones": (
            continuaciones
        ),

        "finish_reason": (
            finish_reason
        ),

        "uso_tokens": uso,
    }

# ==========================================================
# ENDPOINTS BASE
# ==========================================================

@app.get("/")
def home():
    return {"mensaje": "Servidor FastAPI-RackNova corriendo correctamente ✅"}


@app.get("/check_db")
def check_db(session: SessionDep):
    try:
        if es_postgres():
            result = session.exec(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name;
                    """
                )
            ).all()

            return {
                "conexion": "exitosa",
                "database": "postgresql",
                "tablas": [r[0] for r in result],
            }

        result = session.exec(text("SHOW TABLES;")).all()

        return {
            "conexion": "exitosa",
            "database": "mysql",
            "tablas": [r[0] for r in result],
        }

    except Exception as e:
        print(f"❌ Database check error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ==========================================================
# CATÁLOGO HISTÓRICO
# SOLO SKU, NOMBRE Y DESCRIPCIÓN
# ==========================================================

@app.get("/catalogo/productos", response_model=List[ProductoCatalogo])
def listar_catalogo(session: SessionDep, current_user: OperatorUserDep):
    return session.exec(select(ProductoCatalogo)).all()


@app.get("/catalogo/productos/buscar", response_model=List[ProductoCatalogo])
def buscar_catalogo(
    session: SessionDep,
    current_user: OperatorUserDep,
    query: str = Query(..., min_length=1),
):
    q = f"%{query.strip()}%"

    statement = select(ProductoCatalogo).where(
        (ProductoCatalogo.sku.like(q))
        | (ProductoCatalogo.nombre.like(q))
    )

    return session.exec(statement).all()


@app.post("/catalogo/productos", response_model=ProductoCatalogo)
def crear_catalogo(
    producto: ProductoCatalogo,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    catalogo = crear_catalogo_si_no_existe(
        session=session,
        sku=producto.sku,
        nombre=producto.nombre,
        descripcion=producto.descripcion,
    )

    session.commit()
    session.refresh(catalogo)

    return catalogo


@app.put("/catalogo/productos/{sku}", response_model=ProductoCatalogo)
def actualizar_catalogo(
    sku: str,
    producto: ProductoCatalogo,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    catalogo = session.exec(
        select(ProductoCatalogo).where(ProductoCatalogo.sku == sku)
    ).first()

    if not catalogo:
        raise HTTPException(status_code=404, detail="Producto de catálogo no encontrado")

    nuevo_nombre = normalizar_texto(producto.nombre)
    nueva_descripcion = normalizar_texto(producto.descripcion) or None

    if not nuevo_nombre:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    catalogo.nombre = nuevo_nombre
    catalogo.descripcion = nueva_descripcion
    catalogo.ultima_actualizacion = mexico_now()

    productos = session.exec(
        select(Producto).where(Producto.sku == catalogo.sku)
    ).all()

    for item in productos:
        item.nombre = catalogo.nombre
        item.descripcion = catalogo.descripcion
        item.ultima_actualizacion = mexico_now()
        session.add(item)

    lotes = session.exec(
        select(ProductoLote).where(ProductoLote.sku == catalogo.sku)
    ).all()

    for lote in lotes:
        lote.nombre = catalogo.nombre
        session.add(lote)

    session.add(catalogo)
    session.commit()
    session.refresh(catalogo)

    return catalogo


@app.delete("/catalogo/productos/{sku}")
def eliminar_catalogo(
    sku: str,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    catalogo = session.exec(
        select(ProductoCatalogo).where(ProductoCatalogo.sku == sku)
    ).first()

    if not catalogo:
        raise HTTPException(status_code=404, detail="Producto de catálogo no encontrado")

    producto_activo = session.exec(
        select(Producto).where(Producto.sku == sku)
    ).first()

    if producto_activo:
        raise HTTPException(
            status_code=400,
            detail="No puedes eliminar del catálogo un producto que existe actualmente en inventario.",
        )

    session.delete(catalogo)
    session.commit()

    return {"mensaje": f"Catálogo {sku} eliminado correctamente"}


# ==========================================================
# LOTES
# ==========================================================

@app.get("/productos/{sku}/lotes", response_model=List[ProductoLote])
def listar_lotes_producto(
    sku: str,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    return obtener_lotes_activos(session, sku)


@app.get("/lotes", response_model=List[ProductoLote])
def listar_lotes(session: SessionDep, current_user: ReadUserDep):
    lotes = session.exec(select(ProductoLote)).all()

    return sorted(
        lotes,
        key=lambda lote: (
            lote.sku,
            lote.caducidad is None,
            lote.caducidad or date.max,
            lote.fecha_ingreso,
        ),
    )

# ==========================================================
# IA
# ==========================================================
@app.post("/ia/inventario")
def analizar_inventario_con_ia(
    data: IARequest,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    pregunta_limpia = data.pregunta.strip()

    if not pregunta_limpia:
        raise HTTPException(
            status_code=400,
            detail="La pregunta no puede estar vacía.",
        )

    try:
        return procesar_consulta_ia(
            pregunta=pregunta_limpia,
            ruta_actual=data.ruta_actual,
            pagina_actual=data.pagina_actual,
            historial=data.historial,
            session=session,
            current_user=current_user,
            Producto=Producto,
            Movimiento=Movimiento,
            solicitar_deepseek=solicitar_deepseek,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        print("❌ Error en RackNova IA v2:", str(error))

        raise HTTPException(
            status_code=500,
            detail="No se pudo procesar la consulta de RackNova IA.",
        ) from error
# ==========================================================
# PRODUCTOS
# ==========================================================

@app.post("/productos", response_model=Producto)
def crear_producto(
    producto: Producto,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    try:
        producto.sku = normalizar_texto(producto.sku)
        producto.nombre = normalizar_texto(producto.nombre)
        producto.descripcion = normalizar_texto(producto.descripcion) or None
        producto.codigo_barras = normalizar_texto(producto.codigo_barras) or None

        producto.rack = normalizar_texto(producto.rack)
        producto.nivel = normalizar_texto(producto.nivel)
        producto.slot = normalizar_texto(producto.slot)

        producto.cantidad = producto.cantidad if producto.cantidad and producto.cantidad > 0 else 0
        producto.costo_proveedor = producto.costo_proveedor or 0
        producto.precio_venta_sugerido = producto.precio_venta_sugerido or 0

        producto.stock_minimo = normalizar_stock_minimo(producto.stock_minimo)
        producto.stock_alto = normalizar_stock_alto(
            producto.stock_minimo,
            producto.stock_alto,
        )

        if not producto.sku or not producto.nombre:
            raise HTTPException(
                status_code=400,
                detail="SKU y nombre son obligatorios.",
            )

        if producto.cantidad <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad debe ser mayor a 0.",
            )

        catalogo = buscar_catalogo_por_sku_o_nombre(
            session,
            producto.sku,
            producto.nombre,
        )

        if catalogo:
            producto.sku = catalogo.sku
            producto.nombre = catalogo.nombre
            producto.descripcion = catalogo.descripcion
        else:
            catalogo = crear_catalogo_si_no_existe(
                session=session,
                sku=producto.sku,
                nombre=producto.nombre,
                descripcion=producto.descripcion,
            )

        producto_existente = buscar_producto_por_sku_o_nombre(
            session,
            producto.sku,
            producto.nombre,
        )

        # ======================================================
        # RESTOCK
        # ======================================================
        if producto_existente:
            cantidad_anterior = producto_existente.cantidad or 0
            cantidad_nueva = producto.cantidad or 0

            nuevo_costo_promedio = calcular_costo_promedio_producto(
                cantidad_actual=cantidad_anterior,
                costo_actual=producto_existente.costo_proveedor or 0,
                cantidad_nueva=cantidad_nueva,
                costo_nuevo=producto.costo_proveedor or 0,
            )

            producto_existente.cantidad = cantidad_anterior + cantidad_nueva
            producto_existente.costo_proveedor = nuevo_costo_promedio
            producto_existente.precio_venta_sugerido = producto.precio_venta_sugerido

            producto_existente.stock_minimo = producto.stock_minimo
            producto_existente.stock_alto = producto.stock_alto

            producto_existente.sku = catalogo.sku
            producto_existente.nombre = catalogo.nombre
            producto_existente.descripcion = catalogo.descripcion
            producto_existente.codigo_barras = (
                producto.codigo_barras or producto_existente.codigo_barras
            )
            producto_existente.ultima_actualizacion = mexico_now()

            crear_lote(
                session=session,
                producto=producto_existente,
                cantidad=cantidad_nueva,
                costo_unitario=producto.costo_proveedor,
                caducidad=producto.caducidad,
            )

            producto_existente.caducidad = (
                obtener_caducidad_mas_proxima(session, producto_existente.sku)
                or producto.caducidad
            )

            session.add(producto_existente)

            catalogo.ultima_actualizacion = mexico_now()
            session.add(catalogo)

            session.commit()
            session.refresh(producto_existente)

            return producto_existente

        # ======================================================
        # PRODUCTO NUEVO
        # ======================================================
        if not producto.rack or not producto.nivel or not producto.slot:
            raise HTTPException(
                status_code=400,
                detail="Rack, nivel y slot son obligatorios para un producto nuevo.",
            )

        producto_en_slot = buscar_producto_por_ubicacion(
            session,
            producto.rack,
            producto.nivel,
            producto.slot,
        )

        if producto_en_slot:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El slot {producto.rack}-{producto.nivel}-{producto.slot} "
                    "ya contiene un producto."
                ),
            )

        producto.sku = catalogo.sku
        producto.nombre = catalogo.nombre
        producto.descripcion = catalogo.descripcion
        producto.fecha_registro = mexico_now()
        producto.ultima_actualizacion = mexico_now()

        session.add(producto)
        session.flush()

        crear_lote(
            session=session,
            producto=producto,
            cantidad=producto.cantidad,
            costo_unitario=producto.costo_proveedor,
            caducidad=producto.caducidad,
        )

        catalogo.ultima_actualizacion = mexico_now()
        session.add(catalogo)

        session.commit()
        session.refresh(producto)

        return producto

    except HTTPException:
        raise

    except Exception as e:
        print(f"❌ Create/restock product error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/productos", response_model=List[Producto])
def listar_productos(session: SessionDep, current_user: ReadUserDep):
    return session.exec(select(Producto)).all()


@app.put("/productos/{sku}", response_model=Producto)
def update_producto(
    sku: str,
    updated: Producto,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    try:
        db_producto = session.exec(
            select(Producto).where(Producto.sku == sku)
        ).first()

        if not db_producto:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        catalogo = buscar_catalogo_por_sku_o_nombre(
            session,
            db_producto.sku,
            db_producto.nombre,
        )

        if catalogo:
            db_producto.sku = catalogo.sku
            db_producto.nombre = catalogo.nombre
            db_producto.descripcion = catalogo.descripcion

        db_producto.cantidad = updated.cantidad
        db_producto.costo_proveedor = updated.costo_proveedor or 0
        db_producto.precio_venta_sugerido = updated.precio_venta_sugerido or 0
        db_producto.codigo_barras = (
            normalizar_texto(updated.codigo_barras)
            or db_producto.codigo_barras
        )

        db_producto.stock_minimo = normalizar_stock_minimo(updated.stock_minimo)
        db_producto.stock_alto = normalizar_stock_alto(
            db_producto.stock_minimo,
            updated.stock_alto,
        )

        db_producto.caducidad = (
            obtener_caducidad_mas_proxima(session, db_producto.sku)
            or updated.caducidad
        )

        if updated.rack and updated.nivel and updated.slot:
            db_producto.rack = updated.rack
            db_producto.nivel = updated.nivel
            db_producto.slot = updated.slot

        db_producto.ultima_actualizacion = mexico_now()

        session.add(db_producto)
        session.commit()
        session.refresh(db_producto)

        return db_producto

    except HTTPException:
        raise

    except Exception as e:
        print(f"❌ Update product error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/productos/sku/{sku}")
def eliminar_producto_por_sku(
    sku: str,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    try:
        db_producto = session.exec(
            select(Producto).where(Producto.sku == sku)
        ).first()

        if not db_producto:
            raise HTTPException(
                status_code=404,
                detail=f"Producto con SKU {sku} no encontrado",
            )

        session.delete(db_producto)
        session.commit()

        return {"mensaje": f"✅ Producto con SKU {sku} eliminado correctamente"}

    except HTTPException:
        raise

    except Exception as e:
        print(f"❌ Delete product error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/productos/sku/{sku}/salida")
def registrar_salida_producto(
    sku: str,
    salida: SalidaProducto,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    try:
        db_producto = session.exec(
            select(Producto).where(Producto.sku == sku)
        ).first()

        if not db_producto:
            raise HTTPException(
                status_code=404,
                detail=f"Producto con SKU {sku} no encontrado",
            )

        if salida.cantidad_vendida <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad vendida debe ser mayor a 0",
            )

        if salida.cantidad_vendida > db_producto.cantidad:
            raise HTTPException(
                status_code=400,
                detail="No puedes vender más cantidad de la existente",
            )

        detalle_lotes = descontar_lotes_fefo(
            session=session,
            sku=db_producto.sku,
            cantidad=salida.cantidad_vendida,
        )

        db_producto.ultima_actualizacion = mexico_now()

        if salida.cantidad_vendida == db_producto.cantidad:
            session.delete(db_producto)
        else:
            db_producto.cantidad -= salida.cantidad_vendida
            db_producto.caducidad = obtener_caducidad_mas_proxima(
                session,
                db_producto.sku,
            )
            session.add(db_producto)

        session.commit()

        return {
            "mensaje": "Salida registrada correctamente usando FEFO",
            "sku": sku,
            "cantidad_vendida": salida.cantidad_vendida,
            "precio_venta": salida.precio_venta,
            "costo_proveedor": salida.costo_proveedor,
            "ingreso_total": salida.ingreso_total,
            "costo_total": salida.costo_total,
            "ganancia": salida.ganancia,
            "lotes_descontados": detalle_lotes,
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"❌ Error registrando salida financiera: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# MOVIMIENTOS
# ==========================================================

@app.post("/movimientos", response_model=Movimiento)
def crear_movimiento(
    mov: Movimiento,
    session: SessionDep,
    current_user: OperatorUserDep,
):
    mov.fecha = mexico_now()
    mov.usuario = current_user.nombre or current_user.usuario

    session.add(mov)
    session.commit()
    session.refresh(mov)

    return mov


@app.get("/movimientos", response_model=List[Movimiento])
def listar_movimientos(session: SessionDep, current_user: ReadUserDep):
    return session.exec(select(Movimiento)).all()


@app.delete("/movimientos/{id_mov}")
def eliminar_movimiento(
    id_mov: int,
    session: SessionDep,
    current_user: AdminUserDep,
):
    mov = session.get(Movimiento, id_mov)

    if not mov:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")

    session.delete(mov)
    session.commit()

    return {"mensaje": "Movimiento eliminado"}


# ==========================================================
# FINANZAS
# ==========================================================

@app.get("/finanzas/resumen")
def resumen_financiero(session: SessionDep, current_user: AdminUserDep):
    movimientos = session.exec(select(Movimiento)).all()

    ventas = [m for m in movimientos if m.accion == "Egreso"]

    ingresos = sum(m.ingreso_total or 0 for m in ventas)
    costos = sum(m.costo_total or 0 for m in ventas)
    ganancia = ingresos - costos

    return {
        "ingresos": ingresos,
        "costos": costos,
        "ganancia": ganancia,
    }


@app.get("/finanzas/grafica")
def grafica_financiera(session: SessionDep, current_user: AdminUserDep):
    movimientos = session.exec(select(Movimiento)).all()

    datos = {}

    for m in movimientos:
        fecha_key = m.fecha.strftime("%Y-%m-%d")

        if fecha_key not in datos:
            datos[fecha_key] = {
                "fecha": fecha_key,
                "ingresos": 0,
                "costos": 0,
                "ganancia": 0,
            }

        if m.accion == "Egreso":
            datos[fecha_key]["ingresos"] += m.ingreso_total or 0
            datos[fecha_key]["costos"] += m.costo_total or 0
            datos[fecha_key]["ganancia"] = (
                datos[fecha_key]["ingresos"] - datos[fecha_key]["costos"]
            )

    return list(datos.values())


# ==========================================================
# LOGIN / USUARIOS
# ==========================================================

@app.post("/auth/login")
def login(data: LoginRequest, session: SessionDep):
    usuario = session.exec(
        select(Usuario).where(Usuario.usuario == data.username)
    ).first()

    if not usuario:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    if not usuario.activo:
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if not verify_password(data.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    usuario.ultimo_acceso = mexico_now()
    usuario.ultima_actualizacion = mexico_now()

    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    token = create_access_token(
        {
            "sub": usuario.usuario,
            "id_usuario": usuario.id_usuario,
            "rol": usuario.rol,
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id_usuario": usuario.id_usuario,
            "email": usuario.usuario,
            "username": usuario.usuario,
            "name": usuario.nombre or usuario.usuario,
            "role": usuario.rol,
            "activo": usuario.activo,
        },
    }


@app.get("/auth/users")
def listar_usuarios(session: SessionDep, current_user: AdminUserDep):
    usuarios = session.exec(select(Usuario)).all()

    return [
        {
            "id_usuario": usuario.id_usuario,
            "usuario": usuario.usuario,
            "nombre": usuario.nombre,
            "rol": usuario.rol,
            "activo": usuario.activo,
            "fecha_creacion": usuario.fecha_creacion,
            "ultima_actualizacion": usuario.ultima_actualizacion,
            "ultimo_acceso": usuario.ultimo_acceso,
        }
        for usuario in usuarios
    ]


@app.post("/auth/create_user")
def create_user(
    data: CreateUserRequest,
    session: SessionDep,
    current_user: AdminUserDep,
):
    usuario_limpio = normalizar_texto(data.usuario)
    contrasena_limpia = normalizar_texto(data.contrasena)
    rol_limpio = normalizar_texto(data.rol) or "operator"
    nombre_limpio = normalizar_texto(data.nombre) or None

    if not usuario_limpio or not contrasena_limpia:
        raise HTTPException(
            status_code=400,
            detail="Usuario y contraseña son obligatorios",
        )

    if rol_limpio not in ["admin", "operator", "viewer"]:
        raise HTTPException(
            status_code=400,
            detail="Rol inválido. Usa admin, operator o viewer.",
        )

    existente = session.exec(
        select(Usuario).where(Usuario.usuario == usuario_limpio)
    ).first()

    if existente:
        raise HTTPException(
            status_code=400,
            detail="Ya existe un usuario con ese correo o nombre de acceso.",
        )

    nuevo_usuario = Usuario(
        usuario=usuario_limpio,
        nombre=nombre_limpio,
        rol=rol_limpio,
        password_hash=hash_password(contrasena_limpia),
        activo=True,
        fecha_creacion=mexico_now(),
        ultima_actualizacion=mexico_now(),
    )

    session.add(nuevo_usuario)
    session.commit()
    session.refresh(nuevo_usuario)

    return {
        "mensaje": "Usuario creado correctamente",
        "usuario": {
            "id_usuario": nuevo_usuario.id_usuario,
            "usuario": nuevo_usuario.usuario,
            "nombre": nuevo_usuario.nombre,
            "rol": nuevo_usuario.rol,
            "activo": nuevo_usuario.activo,
            "fecha_creacion": nuevo_usuario.fecha_creacion,
            "ultima_actualizacion": nuevo_usuario.ultima_actualizacion,
            "ultimo_acceso": nuevo_usuario.ultimo_acceso,
        },
    }


def contar_admins_activos(session: Session) -> int:
    admins = session.exec(
        select(Usuario).where(
            (Usuario.rol == "admin")
            & (Usuario.activo == True)
        )
    ).all()

    return len(admins)


@app.put("/auth/users/{id_usuario}")
def actualizar_usuario(
    id_usuario: int,
    data: UpdateUserRequest,
    session: SessionDep,
    current_user: AdminUserDep,
):
    usuario = session.get(Usuario, id_usuario)

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario_nuevo = normalizar_texto(data.usuario) if data.usuario is not None else None
    nombre_nuevo = normalizar_texto(data.nombre) if data.nombre is not None else None
    rol_nuevo = normalizar_texto(data.rol) if data.rol is not None else None
    contrasena_nueva = normalizar_texto(data.contrasena) if data.contrasena is not None else None

    if usuario_nuevo:
        existente = session.exec(
            select(Usuario).where(Usuario.usuario == usuario_nuevo)
        ).first()

        if existente and existente.id_usuario != usuario.id_usuario:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otro usuario con ese correo o nombre de acceso.",
            )

        usuario.usuario = usuario_nuevo

    if data.nombre is not None:
        usuario.nombre = nombre_nuevo or None

    if rol_nuevo:
        if rol_nuevo not in ["admin", "operator", "viewer"]:
            raise HTTPException(
                status_code=400,
                detail="Rol inválido. Usa admin, operator o viewer.",
            )

        if usuario.rol == "admin" and rol_nuevo != "admin" and usuario.activo:
            if contar_admins_activos(session) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="No puedes quitar el rol del último administrador activo.",
                )

        usuario.rol = rol_nuevo

    if contrasena_nueva:
        usuario.password_hash = hash_password(contrasena_nueva)

    if data.activo is not None:
        if usuario.rol == "admin" and usuario.activo and data.activo is False:
            if contar_admins_activos(session) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="No puedes desactivar el último administrador activo.",
                )

        usuario.activo = data.activo

    usuario.ultima_actualizacion = mexico_now()

    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    return {
        "mensaje": "Usuario actualizado correctamente",
        "usuario": {
            "id_usuario": usuario.id_usuario,
            "usuario": usuario.usuario,
            "nombre": usuario.nombre,
            "rol": usuario.rol,
            "activo": usuario.activo,
            "fecha_creacion": usuario.fecha_creacion,
            "ultima_actualizacion": usuario.ultima_actualizacion,
            "ultimo_acceso": usuario.ultimo_acceso,
        },
    }


@app.delete("/auth/users/{id_usuario}")
def desactivar_usuario(
    id_usuario: int,
    session: SessionDep,
    current_user: AdminUserDep,
):
    usuario = session.get(Usuario, id_usuario)

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if usuario.rol == "admin" and usuario.activo:
        if contar_admins_activos(session) <= 1:
            raise HTTPException(
                status_code=400,
                detail="No puedes desactivar el último administrador activo.",
            )

    usuario.activo = False
    usuario.ultima_actualizacion = mexico_now()

    session.add(usuario)
    session.commit()
    session.refresh(usuario)

    return {
        "mensaje": "Usuario desactivado correctamente",
        "usuario": {
            "id_usuario": usuario.id_usuario,
            "usuario": usuario.usuario,
            "nombre": usuario.nombre,
            "rol": usuario.rol,
            "activo": usuario.activo,
        },
    }


# ==========================================================
# ADMIN
# ==========================================================

@app.delete("/admin/clear-all")
def limpiar_toda_la_base(
    confirm: str,
    session: SessionDep,
    current_user: AdminUserDep,
):
    if confirm != "BORRAR_TODO_RACKNOVA":
        raise HTTPException(
            status_code=400,
            detail="Confirmación inválida.",
        )

    try:
        if es_postgres():
            session.exec(
                text(
                    """
                    TRUNCATE TABLE
                        pos_devolucion_detalle,
                        pos_devolucion,
                        pos_venta_movimiento,
                        pos_venta_lote,
                        pos_venta_control,
                        pos_movimiento_efectivo,
                        pos_sesion_caja,
                        venta_pos_pago,
                        venta_pos_detalle,
                        venta_pos,
                        producto_lote,
                        movimiento,
                        producto
                    RESTART IDENTITY CASCADE;
                    """
                )
            )
        else:
            session.exec(text("DELETE FROM pos_devolucion_detalle;"))
            session.exec(text("DELETE FROM pos_devolucion;"))
            session.exec(text("DELETE FROM pos_venta_movimiento;"))
            session.exec(text("DELETE FROM pos_venta_lote;"))
            session.exec(text("DELETE FROM pos_venta_control;"))
            session.exec(text("DELETE FROM pos_movimiento_efectivo;"))
            session.exec(text("DELETE FROM pos_sesion_caja;"))
            session.exec(text("DELETE FROM venta_pos_pago;"))
            session.exec(text("DELETE FROM venta_pos_detalle;"))
            session.exec(text("DELETE FROM venta_pos;"))
            session.exec(text("DELETE FROM producto_lote;"))
            session.exec(text("DELETE FROM movimiento;"))
            session.exec(text("DELETE FROM producto;"))

        session.commit()

        return {
            "mensaje": "Base limpiada correctamente. Se eliminaron productos, lotes y movimientos.",
        }

    except Exception as e:
        session.rollback()
        print(f"❌ Error limpiando base: {e}")

        raise HTTPException(
            status_code=500,
            detail=f"No se pudo limpiar la base: {str(e)}",
        )
