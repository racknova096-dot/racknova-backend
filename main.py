# ==========================================================
# RACKNOVA API — SISTEMA DE INVENTARIO CON FASTAPI + IA
# ==========================================================

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends
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


# IMPORTA EL ENGINE Y LA SESIÓN DESDE database.py
try:
    from database import engine, get_session
    print("✅ Database module imported successfully")
except Exception as e:
    print(f"❌ ERROR importing database: {e}")
    sys.exit(1)


MEXICO_TZ = ZoneInfo("America/Mexico_City")


def mexico_now():
    return datetime.now(MEXICO_TZ).replace(tzinfo=None)


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
    cantidad: int = 0
    descripcion: Optional[str] = None

    rack: str
    nivel: str
    slot: str

    costo_proveedor: float = 0
    precio_venta_sugerido: float = 0

    caducidad: Optional[date] = None
    stock_minimo: int = 10

    fecha_registro: datetime = Field(default_factory=mexico_now)
    ultima_actualizacion: datetime = Field(default_factory=mexico_now)


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
# STARTUP
# ==========================================================

@app.on_event("startup")
def on_startup():
    try:
        print("Creating database tables...")
        SQLModel.metadata.create_all(engine)
        print("✅ Database tables created successfully")
    except Exception as e:
        print(f"❌ ERROR creating tables: {e}")
        sys.exit(1)


# ==========================================================
# UTILIDADES IA
# ==========================================================

def calcular_dias_caducidad(caducidad_value: Optional[date]):
    if not caducidad_value:
        return None

    hoy = mexico_now().date()
    return (caducidad_value - hoy).days


def construir_resumen_inventario(
    productos: List[Producto],
    movimientos: List[Movimiento],
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
        ingreso = ingresos_por_sku.get(producto.sku, {})

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

        productos_resumen.append(
            {
                "sku": producto.sku,
                "nombre": producto.nombre,
                "stock_actual": producto.cantidad,
                "stock_minimo": producto.stock_minimo,
                "ubicacion": f"{producto.rack}-{producto.nivel}-{producto.slot}",
                "costo_proveedor": producto.costo_proveedor,
                "precio_venta_sugerido": producto.precio_venta_sugerido,
                "caducidad": str(producto.caducidad) if producto.caducidad else None,
                "dias_para_caducar": dias_caducidad,
                "cantidad_vendida": cantidad_vendida,
                "cantidad_ingresada_estimada": cantidad_ingresada_estimada,
                "porcentaje_vendido_inventario": round(porcentaje_vendido, 2),
                "ingreso_total": ingreso_total,
                "ganancia_total": ganancia_total,
                "margen_porcentaje": round(margen, 2),
                "ultima_venta": str(venta.get("ultima_venta"))
                if venta.get("ultima_venta")
                else None,
                "ultima_entrada": str(ingreso.get("ultima_entrada"))
                if ingreso.get("ultima_entrada")
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
        p for p in productos
        if p.get("dias_para_caducar") is not None
        and p.get("dias_para_caducar") < 0
    ]

    proximos_caducar = [
        p for p in productos
        if p.get("dias_para_caducar") is not None
        and 0 <= p.get("dias_para_caducar") <= 30
    ]

    stock_bajo = [
        p for p in productos
        if p.get("stock_actual", 0) < p.get("stock_minimo", 10)
    ]

    baja_rotacion = [
        p for p in productos
        if p.get("stock_actual", 0) > 0
        and (
            p.get("cantidad_vendida", 0) == 0
            or p.get("porcentaje_vendido_inventario", 0) < 30
        )
    ]

    rentables = [
        p for p in productos
        if p.get("margen_porcentaje", 0) >= 30
        and p.get("ganancia_total", 0) > 0
    ]

    proximos_caducar = sorted(
        proximos_caducar,
        key=lambda p: p.get("dias_para_caducar", 9999),
    )[:5]

    vencidos = vencidos[:5]
    stock_bajo = stock_bajo[:5]
    baja_rotacion = baja_rotacion[:5]
    rentables = sorted(
        rentables,
        key=lambda p: p.get("ganancia_total", 0),
        reverse=True,
    )[:5]

    respuesta = []
    respuesta.append("⚠️ **RackNova iA funcionó en modo automático interno.**")
    respuesta.append("")
    respuesta.append(
        "El modelo externo de IA no respondió correctamente, pero RackNova generó un análisis con su motor interno de reglas usando los datos actuales del inventario."
    )
    respuesta.append("")

    if vencidos:
        respuesta.append("## Productos vencidos")
        for p in vencidos:
            respuesta.append(
                f"- **{p.get('nombre')} ({p.get('sku')})** está vencido desde hace "
                f"{abs(p.get('dias_para_caducar'))} día(s). "
                f"Recomendación: retirar/eliminar del inventario y registrar merma. "
                f"Ubicación: {p.get('ubicacion')}."
            )
        respuesta.append("")

    if proximos_caducar:
        respuesta.append("## Productos próximos a caducar")
        for p in proximos_caducar:
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
                f"- **{p.get('nombre')} ({p.get('sku')})** caduca en {dias} día(s). "
                f"Recomendación: mover a una posición visible y considerar descuento del {descuento}%. "
                f"Stock actual: {p.get('stock_actual')} pieza(s)."
            )
        respuesta.append("")

    if stock_bajo:
        respuesta.append("## Productos con stock bajo")
        for p in stock_bajo:
            respuesta.append(
                f"- **{p.get('nombre')} ({p.get('sku')})** tiene stock bajo. "
                f"Actual: {p.get('stock_actual')} / mínimo: {p.get('stock_minimo')}. "
                f"Recomendación: revisar reposición."
            )
        respuesta.append("")

    if baja_rotacion:
        respuesta.append("## Productos con baja rotación")
        for p in baja_rotacion:
            respuesta.append(
                f"- **{p.get('nombre')} ({p.get('sku')})** tiene baja rotación. "
                f"Porcentaje vendido del inventario estimado: "
                f"{p.get('porcentaje_vendido_inventario', 0)}%. "
                f"Recomendación: cambiar a una posición más visible y evaluar promoción."
            )
        respuesta.append("")

    if rentables:
        respuesta.append("## Productos rentables")
        for p in rentables:
            respuesta.append(
                f"- **{p.get('nombre')} ({p.get('sku')})** tiene buen margen. "
                f"Margen aproximado: {p.get('margen_porcentaje')}%. "
                f"Ganancia acumulada: ${round(p.get('ganancia_total', 0), 2)}. "
                f"Recomendación: mantener seguimiento y disponibilidad."
            )
        respuesta.append("")

    if not vencidos and not proximos_caducar and not stock_bajo and not baja_rotacion and not rentables:
        respuesta.append(
            "No se detectaron alertas importantes con los datos actuales. "
            "El inventario parece estable, pero se recomienda seguir registrando entradas y salidas para mejorar el análisis."
        )

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
Eres RACKNOVA IA, un asistente experto en inventarios, almacenes, caducidad,
rotación, descuentos, rentabilidad y decisiones de compra.

Reglas estrictas:
- Responde siempre en español.
- Usa únicamente los datos enviados en el resumen del inventario.
- No inventes productos, precios, ventas ni cantidades.
- Si falta información, dilo claramente.
- No digas que tienes acceso directo a la base de datos.
- No recomiendes descuento para productos vencidos; para vencidos recomienda retirar, eliminar o registrar merma.
- Para productos próximos a caducar, puedes sugerir descuento:
  1 a 5 días = 40%
  6 a 10 días = 30%
  11 a 15 días = 20%
  16 a 30 días = 10%
- Si el usuario pregunta qué comprar, prioriza stock bajo, buena venta, buen margen y buena rotación.
- Si el usuario pregunta qué mover de lugar, prioriza productos sin venta o con baja rotación.
- Responde como asesor ejecutivo, no como reporte técnico largo.
- No puedes usar viñetas.
- No uses asteriscos de Markdown como **texto** o *viñetas*.
- Nunca despues de un viñeta debe haber otra porque visualmente se ve muy mal
- Tampoco el uso de ###
- Siempre intenta que la respuesta se vea bien para el usuario
- Termina con una recomendación concreta.
- Tu respuesta final nunca debe estar vacía.
"""

    user_prompt = f"""
Pregunta del usuario:
{pregunta}

Resumen del inventario en JSON:
{json.dumps(resumen, ensure_ascii=False, default=str)}

Responde con un análisis claro, en español y en formato de lista.
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
            "type": "disabled"
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

        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content")

        if content and content.strip():
            return content.strip()

        print("⚠️ DeepSeek respondió vacío. Respuesta completa:", data)

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
# ENDPOINTS PRINCIPALES
# ==========================================================

@app.get("/")
def home():
    return {"mensaje": "Servidor FastAPI-RackNova corriendo correctamente ✅"}


@app.get("/check_db")
def check_db(session: SessionDep):
    try:
        result = session.exec(text("SHOW TABLES;")).all()
        tablas = [r[0] for r in result]

        return {
            "conexion": "exitosa",
            "tablas": tablas,
        }

    except Exception as e:
        print(f"❌ Database check error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


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

    resumen = construir_resumen_inventario(productos, movimientos)

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

        if (
            "Insufficient Balance" in detalle
            or "insufficient balance" in detalle.lower()
        ):
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


@app.post("/productos", response_model=Producto)
def crear_producto(producto: Producto, session: SessionDep):
    try:
        existe_sku = session.exec(
            select(Producto).where(Producto.sku == producto.sku)
        ).first()

        if existe_sku:
            raise HTTPException(
                status_code=400,
                detail=f"El SKU '{producto.sku}' ya existe.",
            )

        existe_slot = session.exec(
            select(Producto).where(
                (Producto.rack == producto.rack)
                & (Producto.nivel == producto.nivel)
                & (Producto.slot == producto.slot)
            )
        ).first()

        if existe_slot:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"El slot {producto.rack}-{producto.nivel}-{producto.slot} "
                    "ya contiene un producto."
                ),
            )

        producto.costo_proveedor = producto.costo_proveedor or 0
        producto.precio_venta_sugerido = producto.precio_venta_sugerido or 0
        producto.stock_minimo = producto.stock_minimo or 10
        producto.fecha_registro = mexico_now()
        producto.ultima_actualizacion = mexico_now()

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

        db_producto.sku = updated.sku
        db_producto.nombre = updated.nombre
        db_producto.cantidad = updated.cantidad
        db_producto.descripcion = updated.descripcion
        db_producto.rack = updated.rack
        db_producto.nivel = updated.nivel
        db_producto.slot = updated.slot
        db_producto.costo_proveedor = updated.costo_proveedor or 0
        db_producto.precio_venta_sugerido = updated.precio_venta_sugerido or 0
        db_producto.caducidad = updated.caducidad
        db_producto.stock_minimo = (
            updated.stock_minimo if updated.stock_minimo is not None else 10
        )
        db_producto.ultima_actualizacion = mexico_now()

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
            raise HTTPException(
                status_code=404,
                detail=f"Producto con SKU {sku} no encontrado",
            )

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

        db_producto.ultima_actualizacion = mexico_now()

        if salida.cantidad_vendida == db_producto.cantidad:
            session.delete(db_producto)
        else:
            db_producto.cantidad -= salida.cantidad_vendida
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
        mov.fecha = mexico_now()

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

        ingresos = sum(
            m.ingreso_total or 0 for m in movimientos if m.accion == "Egreso"
        )

        costos = sum(
            m.costo_total or 0 for m in movimientos if m.accion == "Ingreso"
        )

        ganancia = sum(
            m.ganancia or 0 for m in movimientos if m.accion == "Egreso"
        )

        return {
            "ingresos": ingresos,
            "costos": costos,
            "ganancia": ganancia,
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
                    "ganancia": 0,
                }

            if m.accion == "Ingreso":
                datos[fecha_key]["costos"] += m.costo_total or 0

            elif m.accion == "Egreso":
                datos[fecha_key]["ingresos"] += m.ingreso_total or 0
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
                "role": "admin",
            },
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


@app.delete("/admin/clear-all")
def limpiar_toda_la_base(confirm: str, session: SessionDep):
    if confirm != "BORRAR_TODO_RACKNOVA":
        raise HTTPException(
            status_code=400,
            detail="Confirmación inválida. Esta acción borra toda la base de datos.",
        )

    try:
        session.exec(text("DELETE FROM movimiento"))
        session.exec(text("DELETE FROM producto"))

        session.exec(text("ALTER TABLE movimiento AUTO_INCREMENT = 1"))
        session.exec(text("ALTER TABLE producto AUTO_INCREMENT = 1"))

        session.commit()

        return {
            "mensaje": "Base de datos limpiada correctamente",
            "tablas_limpiadas": ["movimiento", "producto"],
        }

    except Exception as e:
        session.rollback()
        print(f"❌ Error limpiando base de datos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=10000)
