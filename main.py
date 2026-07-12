# ==========================================================
# RACKNOVA API — INVENTARIO + CATÁLOGO + LOTES FEFO + IA
# Compatible con MySQL/Railway y PostgreSQL/Supabase
# ==========================================================

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Query
from typing import Annotated, Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Session, select
from datetime import datetime, date
from zoneinfo import ZoneInfo
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

import sys
import os
import json
import urllib.request
import urllib.error

try:
    from database import engine, get_session

    print("✅ Database module imported successfully")
except Exception as e:
    print(f"❌ ERROR importing database: {e}")
    sys.exit(1)


MEXICO_TZ = ZoneInfo("America/Mexico_City")


def mexico_now():
    return datetime.now(MEXICO_TZ).replace(tzinfo=None)


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
# CONFIGURACIÓN BASE
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

class Producto(SQLModel, table=True):
    id_producto: Optional[int] = Field(default=None, primary_key=True)

    sku: str
    nombre: str
    descripcion: Optional[str] = None

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


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    usuario: str
    contrasena: str
    rol: str = "operator"


class SalidaProducto(SQLModel):
    cantidad_vendida: int
    precio_venta: float

    costo_proveedor: float = 0
    ingreso_total: float = 0
    costo_total: float = 0
    ganancia: float = 0


class IARequest(BaseModel):
    pregunta: str


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
            # En Supabase/PostgreSQL normalmente no necesitaremos ajustar
            # columnas heredadas porque la base arranca limpia.
            # Evitamos usar MODIFY COLUMN porque es sintaxis exclusiva de MySQL.
            print(
                f"ℹ️ PostgreSQL detectado. Se omite MODIFY COLUMN en {tabla}.{columna}."
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
        ((cantidad_actual * costo_actual) + (cantidad_nueva * costo_nuevo))
        / total,
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
# UTILIDADES IA
# ==========================================================

def calcular_dias_caducidad(caducidad_value: Optional[date]):
    if not caducidad_value:
        return None

    return (caducidad_value - mexico_now().date()).days


def construir_resumen_inventario(
    productos: List[Producto],
    movimientos: List[Movimiento],
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    ventas_por_sku: Dict[str, Dict[str, Any]] = {}
    ingresos_por_sku: Dict[str, Dict[str, Any]] = {}

    for mov in movimientos:
        sku = mov.sku

        if mov.accion == "Egreso":
            if sku not in ventas_por_sku:
                ventas_por_sku[sku] = {
                    "sku": sku,
                    "producto": mov.producto,
                    "cantidad_vendida": 0,
                    "ingreso_total": 0,
                    "costo_total": 0,
                    "ganancia_total": 0,
                    "ultima_venta": None,
                }

            ventas_por_sku[sku]["cantidad_vendida"] += mov.cantidad or 0
            ventas_por_sku[sku]["ingreso_total"] += mov.ingreso_total or 0
            ventas_por_sku[sku]["costo_total"] += mov.costo_total or 0
            ventas_por_sku[sku]["ganancia_total"] += mov.ganancia or 0

            if (
                ventas_por_sku[sku]["ultima_venta"] is None
                or mov.fecha > ventas_por_sku[sku]["ultima_venta"]
            ):
                ventas_por_sku[sku]["ultima_venta"] = mov.fecha

        if mov.accion == "Ingreso":
            if sku not in ingresos_por_sku:
                ingresos_por_sku[sku] = {
                    "sku": sku,
                    "producto": mov.producto,
                    "cantidad_ingresada": 0,
                    "costo_total_ingresado": 0,
                    "ultima_entrada": None,
                }

            ingresos_por_sku[sku]["cantidad_ingresada"] += mov.cantidad or 0
            ingresos_por_sku[sku]["costo_total_ingresado"] += mov.costo_total or 0

            if (
                ingresos_por_sku[sku]["ultima_entrada"] is None
                or mov.fecha > ingresos_por_sku[sku]["ultima_entrada"]
            ):
                ingresos_por_sku[sku]["ultima_entrada"] = mov.fecha

    productos_resumen = []

    for producto in productos:
        venta = ventas_por_sku.get(producto.sku, {})

        cantidad_vendida = venta.get("cantidad_vendida", 0)
        ingreso_total = venta.get("ingreso_total", 0)
        ganancia_total = venta.get("ganancia_total", 0)

        margen = 0
        if ingreso_total > 0:
            margen = (ganancia_total / ingreso_total) * 100

        cantidad_ingresada_estimada = producto.cantidad + cantidad_vendida

        porcentaje_vendido = 0
        if cantidad_ingresada_estimada > 0:
            porcentaje_vendido = (
                cantidad_vendida / cantidad_ingresada_estimada
            ) * 100

        dias_caducidad = calcular_dias_caducidad(producto.caducidad)

        lotes_resumen = []

        if session:
            for lote in obtener_lotes_activos(session, producto.sku):
                lotes_resumen.append(
                    {
                        "id_lote": lote.id_lote,
                        "cantidad_actual": lote.cantidad_actual,
                        "caducidad": str(lote.caducidad)
                        if lote.caducidad
                        else None,
                        "dias_para_caducar": calcular_dias_caducidad(
                            lote.caducidad
                        ),
                    }
                )

        productos_resumen.append(
            {
                "sku": producto.sku,
                "nombre": producto.nombre,
                "stock_actual": producto.cantidad,
                "stock_minimo": producto.stock_minimo,
                "stock_alto": producto.stock_alto,
                "ubicacion": f"{producto.rack}-{producto.nivel}-{producto.slot}",
                "costo_proveedor": producto.costo_proveedor,
                "precio_venta_sugerido": producto.precio_venta_sugerido,
                "caducidad_mas_proxima": str(producto.caducidad)
                if producto.caducidad
                else None,
                "dias_para_caducar": dias_caducidad,
                "lotes_activos": lotes_resumen,
                "cantidad_vendida": cantidad_vendida,
                "cantidad_ingresada_estimada": cantidad_ingresada_estimada,
                "porcentaje_vendido_inventario": round(porcentaje_vendido, 2),
                "ingreso_total": ingreso_total,
                "ganancia_total": ganancia_total,
                "margen_porcentaje": round(margen, 2),
                "ultima_venta": str(venta.get("ultima_venta"))
                if venta.get("ultima_venta")
                else None,
            }
        )

    movimientos_recientes = sorted(
        movimientos,
        key=lambda m: m.fecha,
        reverse=True,
    )[:40]

    return {
        "fecha_analisis": str(mexico_now()),
        "total_productos": len(productos),
        "total_movimientos": len(movimientos),
        "productos": productos_resumen[:80],
        "ventas_por_sku": list(ventas_por_sku.values())[:80],
        "movimientos_recientes": [
            {
                "accion": m.accion,
                "sku": m.sku,
                "producto": m.producto,
                "cantidad": m.cantidad,
                "ubicacion": m.ubicacion,
                "fecha": str(m.fecha),
                "precio_venta": m.precio_venta,
                "ingreso_total": m.ingreso_total,
                "costo_total": m.costo_total,
                "ganancia": m.ganancia,
            }
            for m in movimientos_recientes
        ],
    }


def generar_respuesta_fallback(pregunta: str, resumen: Dict[str, Any]) -> str:
    productos = resumen.get("productos", [])

    vencidos = [
        p
        for p in productos
        if p.get("dias_para_caducar") is not None
        and p.get("dias_para_caducar") < 0
    ]

    proximos_caducar = [
        p
        for p in productos
        if p.get("dias_para_caducar") is not None
        and 0 <= p.get("dias_para_caducar") <= 30
    ]

    stock_bajo = [
        p
        for p in productos
        if p.get("stock_actual", 0) <= p.get("stock_minimo", 10)
    ]

    baja_rotacion = [
        p
        for p in productos
        if p.get("stock_actual", 0) > 0
        and (
            p.get("cantidad_vendida", 0) == 0
            or p.get("porcentaje_vendido_inventario", 0) < 30
        )
    ]

    rentables = [
        p
        for p in productos
        if p.get("margen_porcentaje", 0) >= 30
        and p.get("ganancia_total", 0) > 0
    ]

    respuesta = [
        "RACKNOVA IA funcionó en modo automático interno.",
        "",
        "El modelo externo no respondió correctamente, pero se generó un análisis con reglas internas de RackNova.",
        "",
    ]

    if vencidos:
        respuesta.append("Productos vencidos:")

        for p in vencidos[:5]:
            respuesta.append(
                f"- {p.get('nombre')} ({p.get('sku')}) está vencido. "
                f"Recomendación: retirar o registrar merma. "
                f"Ubicación: {p.get('ubicacion')}."
            )

        respuesta.append("")

    if proximos_caducar:
        respuesta.append("Productos próximos a caducar:")

        for p in sorted(
            proximos_caducar,
            key=lambda x: x.get("dias_para_caducar", 9999),
        )[:5]:
            dias = p.get("dias_para_caducar")

            if dias <= 5:
                descuento = 40
            elif dias <= 10:
                descuento = 30
            elif dias <= 15:
                descuento = 20
            else:
                descuento = 10

            respuesta.append(
                f"- {p.get('nombre')} ({p.get('sku')}) caduca en {dias} día(s). "
                f"Sugerencia: descuento del {descuento}% y colocarlo visible."
            )

        respuesta.append("")

    if stock_bajo:
        respuesta.append("Productos con stock bajo:")

        for p in stock_bajo[:5]:
            respuesta.append(
                f"- {p.get('nombre')} ({p.get('sku')}): "
                f"{p.get('stock_actual')} / mínimo {p.get('stock_minimo')}."
            )

        respuesta.append("")

    if baja_rotacion:
        respuesta.append("Productos con baja rotación:")

        for p in baja_rotacion[:5]:
            respuesta.append(
                f"- {p.get('nombre')} ({p.get('sku')}) tiene baja rotación. "
                f"Evalúa promoción o cambio de ubicación."
            )

        respuesta.append("")

    if rentables:
        respuesta.append("Productos rentables:")

        for p in sorted(
            rentables,
            key=lambda x: x.get("ganancia_total", 0),
            reverse=True,
        )[:5]:
            respuesta.append(
                f"- {p.get('nombre')} ({p.get('sku')}) tiene margen de "
                f"{p.get('margen_porcentaje')}% y ganancia acumulada de "
                f"${round(p.get('ganancia_total', 0), 2)}."
            )

        respuesta.append("")

    if len(respuesta) <= 4:
        respuesta.append("No se detectaron alertas importantes con los datos actuales.")

    return "\n".join(respuesta)


def llamar_deepseek(pregunta: str, resumen: Dict[str, Any]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Falta configurar DEEPSEEK_API_KEY en Render.",
        )

    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    system_prompt = """
Eres RACKNOVA IA, un asistente experto en inventarios, caducidad, rotación, descuentos, rentabilidad, lotes FEFO y decisiones de compra.

Responde siempre en español.
Usa únicamente los datos enviados.
No inventes productos, precios, ventas ni cantidades.
Para vencidos recomienda retirar o registrar merma, no descuento.
Para próximos a caducar puedes sugerir:
1 a 5 días = 40%
6 a 10 días = 30%
11 a 15 días = 20%
16 a 30 días = 10%

Responde como asesor ejecutivo, breve y accionable.
No uses Markdown pesado ni listas largas.
Cuando haya muchos productos, menciona máximo 3 productos principales.
"""

    user_prompt = f"""
Pregunta del usuario:
{pregunta}

Resumen del inventario en JSON:
{json.dumps(resumen, ensure_ascii=False, default=str)}

Responde en español con estilo ejecutivo, claro y breve.
"""

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "thinking": {
            "type": "disabled",
        },
        "temperature": 0.3,
        "max_tokens": 1200,
    }

    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)

            content = data.get("choices", [{}])[0].get("message", {}).get("content")

            if content and content.strip():
                return content.strip()

            return generar_respuesta_fallback(pregunta, resumen)

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print("❌ Error DeepSeek:", e.code, error_body)

        raise HTTPException(
            status_code=500,
            detail=f"Error llamando DeepSeek: {error_body}",
        )

    except Exception as e:
        print("❌ Error IA:", e)

        raise HTTPException(
            status_code=500,
            detail=f"Error general de IA: {str(e)}",
        )


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
def listar_catalogo(session: SessionDep):
    return session.exec(select(ProductoCatalogo)).all()


@app.get("/catalogo/productos/buscar", response_model=List[ProductoCatalogo])
def buscar_catalogo(
    session: SessionDep,
    query: str = Query(..., min_length=1),
):
    q = f"%{query.strip()}%"

    statement = select(ProductoCatalogo).where(
        (ProductoCatalogo.sku.like(q))
        | (ProductoCatalogo.nombre.like(q))
    )

    return session.exec(statement).all()


@app.post("/catalogo/productos", response_model=ProductoCatalogo)
def crear_catalogo(producto: ProductoCatalogo, session: SessionDep):
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
def eliminar_catalogo(sku: str, session: SessionDep):
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
def listar_lotes_producto(sku: str, session: SessionDep):
    return obtener_lotes_activos(session, sku)


@app.get("/lotes", response_model=List[ProductoLote])
def listar_lotes(session: SessionDep):
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
def analizar_inventario_con_ia(data: IARequest, session: SessionDep):
    pregunta_limpia = data.pregunta.strip()

    if not pregunta_limpia:
        raise HTTPException(
            status_code=400,
            detail="La pregunta no puede estar vacía.",
        )

    productos = session.exec(select(Producto)).all()
    movimientos = session.exec(select(Movimiento)).all()

    resumen = construir_resumen_inventario(productos, movimientos, session)

    fuente = "deepseek"
    advertencia = None

    try:
        respuesta = llamar_deepseek(pregunta_limpia, resumen)

        if not respuesta or not respuesta.strip():
            fuente = "motor_interno_fallback"
            advertencia = (
                "DeepSeek respondió vacío. "
                "Se generó una respuesta con el motor interno de RackNova."
            )
            respuesta = generar_respuesta_fallback(pregunta_limpia, resumen)

    except HTTPException as e:
        detalle = str(e.detail)

        if "Insufficient Balance" in detalle or "insufficient balance" in detalle.lower():
            fuente = "motor_interno_fallback"
            advertencia = (
                "DeepSeek no tiene saldo suficiente. "
                "Se generó una respuesta con el motor interno de RackNova."
            )
            respuesta = generar_respuesta_fallback(pregunta_limpia, resumen)
        else:
            raise e

    return {
        "pregunta": pregunta_limpia,
        "respuesta": respuesta,
        "modelo": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "fuente": fuente,
        "advertencia": advertencia,
        "resumen_usado": {
            "total_productos": resumen["total_productos"],
            "total_movimientos": resumen["total_movimientos"],
            "productos_enviados": len(resumen["productos"]),
            "movimientos_recientes_enviados": len(resumen["movimientos_recientes"]),
        },
    }


# ==========================================================
# PRODUCTOS
# ==========================================================

@app.post("/productos", response_model=Producto)
def crear_producto(producto: Producto, session: SessionDep):
    try:
        producto.sku = normalizar_texto(producto.sku)
        producto.nombre = normalizar_texto(producto.nombre)
        producto.descripcion = normalizar_texto(producto.descripcion) or None

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
def listar_productos(session: SessionDep):
    return session.exec(select(Producto)).all()


@app.put("/productos/{sku}", response_model=Producto)
def update_producto(sku: str, updated: Producto, session: SessionDep):
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
def eliminar_producto_por_sku(sku: str, session: SessionDep):
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
def registrar_salida_producto(sku: str, salida: SalidaProducto, session: SessionDep):
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
def crear_movimiento(mov: Movimiento, session: SessionDep):
    mov.fecha = mexico_now()

    session.add(mov)
    session.commit()
    session.refresh(mov)

    return mov


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
# FINANZAS
# ==========================================================

@app.get("/finanzas/resumen")
def resumen_financiero(session: SessionDep):
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
def grafica_financiera(session: SessionDep):
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
# LOGIN / ADMIN
# ==========================================================

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
                "role": "admin",
            },
        }

    raise HTTPException(status_code=401, detail="Credenciales incorrectas")


@app.post("/auth/create_user")
def create_user(data: CreateUserRequest):
    if not data.usuario or not data.contrasena:
        raise HTTPException(status_code=400, detail="Usuario y contraseña son obligatorios")

    return {
        "mensaje": "Usuario creado correctamente en modo demo",
        "usuario": data.usuario,
        "rol": data.rol,
    }


@app.delete("/admin/clear-all")
def limpiar_toda_la_base(confirm: str, session: SessionDep):
    if confirm != "BORRAR_TODO_RACKNOVA":
        raise HTTPException(
            status_code=400,
            detail="Confirmación inválida. Esta acción borra toda la base de datos.",
        )

    try:
        if es_postgres():
            session.exec(
                text(
                    """
                    TRUNCATE TABLE
                        movimiento,
                        producto_lote,
                        producto,
                        producto_catalogo
                    RESTART IDENTITY CASCADE;
                    """
                )
            )
        else:
            session.exec(text("DELETE FROM movimiento"))
            session.exec(text("DELETE FROM producto_lote"))
            session.exec(text("DELETE FROM producto"))
            session.exec(text("DELETE FROM producto_catalogo"))

            session.exec(text("ALTER TABLE movimiento AUTO_INCREMENT = 1"))
            session.exec(text("ALTER TABLE producto_lote AUTO_INCREMENT = 1"))
            session.exec(text("ALTER TABLE producto AUTO_INCREMENT = 1"))
            session.exec(text("ALTER TABLE producto_catalogo AUTO_INCREMENT = 1"))

        session.commit()

        return {
            "mensaje": "Base de datos limpiada correctamente",
            "database": engine.dialect.name,
            "tablas_limpiadas": [
                "movimiento",
                "producto_lote",
                "producto",
                "producto_catalogo",
            ],
        }

    except Exception as e:
        session.rollback()
        print(f"❌ Error limpiando base de datos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# LOCAL DEV
# ==========================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=10000)
