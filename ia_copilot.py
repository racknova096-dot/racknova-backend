"""RackNova IA v2.

Copiloto sin memoria persistente para:
- guiar al usuario dentro del dashboard;
- responder consultas directas sin gastar tokens;
- consultar únicamente datos relevantes de inventario;
- usar DeepSeek solo cuando la pregunta requiere análisis.

Este módulo no importa los modelos de main.py para evitar dependencias circulares.
Los modelos Producto y Movimiento se reciben como argumentos.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import json
import os
import re
import unicodedata

from sqlalchemy import or_
from sqlmodel import Session, select


DeepSeekRequester = Callable[..., Dict[str, Any]]


PAGINAS: Dict[str, Dict[str, Any]] = {
    "/": {
        "nombre": "Dashboard",
        "descripcion": "Muestra el estado general del inventario, alertas y métricas principales.",
        "instruccion": "En Dashboard puedes consultar el resumen general y localizar rápidamente los indicadores que necesitan atención.",
        "roles": {"admin", "operator", "viewer"},
        "palabras": {"dashboard", "inicio", "portada", "resumen general"},
    },
    "/add": {
        "nombre": "Agregar",
        "descripcion": "Registra productos nuevos o reabastecimientos.",
        "instruccion": "Entra a Agregar, busca el SKU o nombre, completa cantidad, ubicación, costo y caducidad, y después guarda la entrada.",
        "roles": {"admin", "operator"},
        "palabras": {
            "agregar",
            "agrego",
            "añadir",
            "entrada",
            "reabastecer",
            "reabastecimiento",
            "restock",
        },
    },
    "/products": {
        "nombre": "Productos",
        "descripcion": "Consulta productos y registra ventas, salidas o eliminaciones.",
        "instruccion": "Entra a Productos, busca el artículo y selecciona Registrar salida. Captura la cantidad y confirma la operación.",
        "roles": {"admin", "operator", "viewer"},
        "palabras": {
            "productos",
            "producto",
            "salida",
            "vender",
            "venta",
            "eliminar unidades",
            "retirar",
        },
    },
    "/tracking": {
        "nombre": "Trackeo",
        "descripcion": "Consulta el historial de movimientos y sus responsables.",
        "instruccion": "Entra a Trackeo y usa los filtros de fecha, usuario o acción para encontrar el movimiento que necesitas.",
        "roles": {"admin", "operator", "viewer"},
        "palabras": {"trackeo", "tracking", "historial", "movimientos", "trazabilidad"},
    },
    "/reportes": {
        "nombre": "Reportes",
        "descripcion": "Genera y descarga reportes del inventario.",
        "instruccion": "Entra a Reportes, selecciona el reporte y el formato que necesitas, y después usa la opción de descarga.",
        "roles": {"admin", "operator", "viewer"},
        "palabras": {"reporte", "reportes", "pdf", "excel", "descargar"},
    },
    "/finanzas": {
        "nombre": "Finanzas",
        "descripcion": "Muestra ingresos, costos y ganancias registradas.",
        "instruccion": "Entra a Finanzas para revisar ingresos, costos, ganancias y productos con mejor o menor rentabilidad.",
        "roles": {"admin"},
        "palabras": {"finanzas", "ganancias", "ingresos", "costos", "rentabilidad"},
    },
    "/catalogo": {
        "nombre": "Catálogo",
        "descripcion": "Administra la identidad histórica de los productos.",
        "instruccion": "Entra a Catálogo para agregar, editar o eliminar SKU, nombre y descripción del catálogo histórico.",
        "roles": {"admin", "operator"},
        "palabras": {"catálogo", "catalogo", "catálogo histórico", "catalogo historico"},
    },
    "/racknova-ia": {
        "nombre": "RackNova IA",
        "descripcion": "Permite consultar inventario, ventas y ayuda de uso.",
        "instruccion": "En RackNova IA escribe una pregunta concreta sobre el inventario o sobre cómo utilizar una página.",
        "roles": {"admin", "operator"},
        "palabras": {"racknova ia", "inteligencia artificial", "asistente"},
    },
    "/pos": {
        "nombre": "Punto de Venta",
        "descripcion": (
            "Permite abrir caja, buscar productos, cobrar ventas, aplicar "
            "promociones, administrar clientes y crédito, consultar tickets "
            "y cerrar el turno."
        ),
        "instruccion": (
            "En Punto de Venta abre una caja, busca el producto por código, "
            "SKU o nombre, agrégalo al carrito, revisa cantidad, promoción y "
            "descuento, selecciona el método de pago y confirma la venta. "
            "Al terminar se muestra el resumen del ticket."
        ),
        "roles": {"admin", "operator"},
        "palabras": {
            "punto de venta",
            "pos",
            "caja",
            "abrir caja",
            "cerrar caja",
            "carrito",
            "cobrar",
            "cobro",
            "ticket",
            "promocion",
            "promociones",
            "cliente",
            "clientes",
            "credito",
            "fiado",
            "pago mixto",
            "efectivo recibido",
            "cambio",
        },
    },

    "/usuarios": {
        "nombre": "Usuarios",
        "descripcion": "Administra cuentas, roles y acceso al sistema.",
        "instruccion": "Entra a Usuarios para crear cuentas, cambiar roles, activar usuarios o desactivarlos.",
        "roles": {"admin"},
        "palabras": {"usuarios", "usuario", "roles", "cuentas", "permisos"},
    },
    "/rackview": {
        "nombre": "Vista del rack",
        "descripcion": "Muestra las posiciones físicas del rack.",
        "instruccion": "Entra a Vista del rack para consultar posiciones y localizar físicamente productos.",
        "roles": {"admin", "operator"},
        "palabras": {"rackview", "vista del rack", "rack", "posición física", "posicion fisica"},
    },
}

# La ruta antigua también abre la página Agregar.
PAGINAS["/add-product"] = PAGINAS["/add"]


STOPWORDS_PRODUCTO = {
    "a",
    "al",
    "algo",
    "articulo",
    "articulos",
    "cuanta",
    "cuantas",
    "cuanto",
    "cuantos",
    "cantidad",
    "cantidades",
    "de",
    "del",
    "donde",
    "el",
    "en",
    "esta",
    "estan",
    "existencia",
    "existencias",
    "hay",
    "la",
    "las",
    "los",
    "me",
    "mi",
    "mis",
    "nombre",
    "producto",
    "productos",
    "que",
    "quiero",
    "se",
    "sku",
    "stock",
    "tengo",
    "tiene",
    "tienen",
    "ubicacion",
    "ubicado",
    "ubicada",
    "unidades",
    "unidad",
    "ver",
}


def normalizar(value: Any) -> str:
    texto = str(value or "").strip().lower()
    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def contiene(texto: str, frases: Sequence[str]) -> bool:
    return any(normalizar(frase) in texto for frase in frases)


def respuesta_base(
    respuesta: str,
    *,
    fuente: str = "racknova_directo",
    tipo: str = "directa",
    accion: Optional[Dict[str, str]] = None,
    advertencia: Optional[str] = None,
    completa: bool = True,
    finish_reason: str = "stop",
    uso_tokens: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    return {
        "respuesta": respuesta.strip(),
        "fuente": fuente,
        "tipo_respuesta": tipo,
        "accion": accion,
        "advertencia": advertencia,
        "completa": completa,
        "continuaciones": 0,
        "finish_reason": finish_reason,
        "uso_tokens": uso_tokens
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def accion_navegar(ruta: str) -> Dict[str, str]:
    pagina = PAGINAS[ruta]
    return {
        "tipo": "navegar",
        "etiqueta": f"Abrir {pagina['nombre']}",
        "ruta": ruta,
    }


def detectar_tipo_respuesta(pregunta: str) -> str:
    texto = normalizar(pregunta)
    if contiene(
        texto,
        (
            "analiza",
            "analisis",
            "recomienda",
            "recomendacion",
            "compara",
            "por que",
            "que harias",
            "estrategia",
            "oportunidad",
            "riesgo",
        ),
    ):
        return "analisis"
    if contiene(texto, ("como ", "pasos", "guiame", "guia", "que debo hacer")):
        return "pasos"
    return "directa"


def es_pregunta_ayuda(pregunta: str) -> bool:
    texto = normalizar(pregunta)
    # RACKNOVA_IA_DETECTAR_AYUDA_POS
    if contiene(
        texto,
        (
            "como funciona el punto de venta",
            "cómo funciona el punto de venta",
            "como uso el pos",
            "cómo uso el pos",
            "como cobro",
            "cómo cobro",
            "abrir caja",
            "cerrar caja",
            "fondo inicial",
            "pago mixto",
            "como aplico una promocion",
            "cómo aplico una promoción",
            "como hago una venta",
            "cómo hago una venta",
            "como imprimo el ticket",
            "cómo imprimo el ticket",
        ),
    ):
        return True

    frases_ayuda = (
        "como agreg",
        "donde agreg",
        "como registro una entrada",
        "como registrar una entrada",
        "como hago una salida",
        "como registrar una salida",
        "donde hago una salida",
        "como genero un reporte",
        "como descargo",
        "como creo un usuario",
        "donde creo un usuario",
        "como uso",
        "que hace la pagina",
        "donde encuentro la pagina",
        "donde esta el boton",
        "guiame",
        "ayudame a usar",
    )
    if contiene(texto, frases_ayuda):
        return True
    return "pagina" in texto and contiene(
        texto,
        ("donde", "como", "abrir", "entrar", "usar", "sirve"),
    )


def buscar_pagina_mencionada(pregunta: str, ruta_actual: Optional[str]) -> str:
    texto = normalizar(pregunta)

    # RACKNOVA_IA_RUTA_POS
    if contiene(
        texto,
        (
            "punto de venta",
            "pos",
            "abrir caja",
            "cerrar caja",
            "fondo inicial",
            "carrito",
            "cobrar",
            "cobro",
            "ticket",
            "promocion",
            "promoción",
            "pago mixto",
            "cliente a credito",
            "cliente a crédito",
            "fiado",
        ),
    ):
        return "/pos"

    if ruta_actual == "/pos" and contiene(
        texto,
        (
            "venta",
            "vender",
            "producto",
            "descuento",
            "pago",
            "cantidad",
            "cliente",
        ),
    ):
        return "/pos"

    # Casos operativos frecuentes antes de la búsqueda general.
    if contiene(texto, ("agregar", "agrego", "entrada", "reabastecer", "restock")):
        return "/add"
    if contiene(texto, ("salida", "vender", "venta", "retirar", "eliminar unidades")):
        return "/products"

    for ruta, pagina in PAGINAS.items():
        if any(normalizar(palabra) in texto for palabra in pagina["palabras"]):
            return ruta

    if ruta_actual in PAGINAS:
        return str(ruta_actual)
    return "/"



# RACKNOVA_IA_GUIA_POS
def responder_ayuda_pos(
    pregunta: str,
    *,
    ya_esta_en_pagina: bool,
) -> Dict[str, Any]:
    texto = normalizar(pregunta)
    prefijo = "Ya estás en Punto de Venta. " if ya_esta_en_pagina else ""
    accion = None if ya_esta_en_pagina else accion_navegar("/pos")

    if contiene(texto, ("abrir caja", "fondo inicial", "iniciar turno")):
        respuesta = (
            prefijo
            + "Para iniciar: 1) selecciona una caja; 2) captura el fondo "
            "inicial disponible; 3) pulsa Abrir caja. El fondo inicial es "
            "el efectivo con el que comienza el cajero para entregar cambio."
        )
    elif contiene(texto, ("cerrar caja", "cerrar turno", "corte de caja")):
        respuesta = (
            prefijo
            + "Para cerrar el turno: 1) revisa el efectivo esperado; "
            "2) cuenta el efectivo físico; 3) captura el efectivo contado y "
            "observaciones; 4) pulsa Cerrar turno. RackNova calculará la diferencia."
        )
    elif contiene(
        texto,
        (
            "promocion",
            "promociones",
            "descuento automatico",
            "descuento automático",
            "3x2",
            "precio fijo",
        ),
    ):
        respuesta = (
            prefijo
            + "Las promociones se crean en Punto de Venta > Promociones. "
            "Selecciona el producto o todos los productos, define porcentaje, "
            "precio fijo o N por M, cantidad mínima y vigencia. En el carrito "
            "RackNova cotiza automáticamente y muestra el nombre y monto aplicado."
        )
    elif contiene(
        texto,
        (
            "descuento manual",
            "descuento del cajero",
            "descuento %",
        ),
    ):
        respuesta = (
            prefijo
            + "El descuento manual se captura en el producto dentro del carrito. "
            "Es independiente de la promoción automática y el total se vuelve a "
            "cotizar antes de cobrar."
        )
    elif contiene(
        texto,
        (
            "cliente",
            "clientes",
            "credito",
            "crédito",
            "fiado",
            "abono",
            "saldo",
        ),
    ):
        respuesta = (
            prefijo
            + "En Clientes y crédito puedes registrar al cliente, definir límite "
            "y días de crédito, consultar saldo y registrar abonos. Para una venta "
            "a crédito selecciona al cliente y cambia la modalidad a Crédito o Pago parcial."
        )
    elif contiene(
        texto,
        (
            "pago mixto",
            "efectivo",
            "tarjeta",
            "transferencia",
            "cambio",
            "forma de pago",
            "metodo de pago",
            "método de pago",
        ),
    ):
        respuesta = (
            prefijo
            + "En Cobro selecciona efectivo, tarjeta, transferencia o pago mixto. "
            "En efectivo captura lo recibido para calcular el cambio. En pago mixto "
            "los importes deben sumar exactamente el total cotizado."
        )
    elif contiene(
        texto,
        (
            "ticket",
            "imprimir",
            "folio",
            "historial de ventas",
            "ver venta",
            "cancelar venta",
            "devolucion",
            "devolución",
        ),
    ):
        respuesta = (
            prefijo
            + "Al completar la venta se abre el resumen del ticket con folio, "
            "productos, promociones, pagos, total y cambio. Desde ahí puedes imprimir. "
            "Las ventas anteriores se consultan en Historial; administradores pueden "
            "cancelar o registrar devoluciones."
        )
    else:
        respuesta = (
            prefijo
            + "Para vender: 1) abre una caja; 2) busca por código, SKU o nombre; "
            "3) agrega el producto y captura la cantidad; 4) revisa promociones y "
            "descuentos; 5) selecciona el pago; 6) cobra y revisa el ticket."
        )

    return respuesta_base(
        respuesta,
        tipo="pasos",
        accion=accion,
    )


def responder_ayuda(
    pregunta: str,
    ruta_actual: Optional[str],
    rol: str,
) -> Dict[str, Any]:
    ruta = buscar_pagina_mencionada(pregunta, ruta_actual)
    pagina = PAGINAS[ruta]

    if rol not in pagina["roles"]:
        return respuesta_base(
            f"La página {pagina['nombre']} no está disponible para tu rol actual.",
            tipo="directa",
        )

    if ruta == "/pos":
        return responder_ayuda_pos(
            pregunta,
            ya_esta_en_pagina=ruta_actual == ruta,
        )

    texto = normalizar(pregunta)
    ya_esta_en_pagina = ruta_actual == ruta
    prefijo = f"Ya estás en {pagina['nombre']}. " if ya_esta_en_pagina else ""

    if contiene(texto, ("como", "pasos", "guiame", "que debo hacer")):
        respuesta = prefijo + pagina["instruccion"]
        tipo = "pasos"
    else:
        respuesta = prefijo + pagina["descripcion"]
        tipo = "directa"

    return respuesta_base(
        respuesta,
        tipo=tipo,
        accion=None if ya_esta_en_pagina else accion_navegar(ruta),
    )


def obtener_tokens_producto(pregunta: str) -> List[str]:
    texto = normalizar(pregunta)
    tokens = re.findall(r"[a-z0-9][a-z0-9._/-]*", texto)
    resultado: List[str] = []
    for token in tokens:
        if token in STOPWORDS_PRODUCTO or len(token) < 2:
            continue
        if token not in resultado:
            resultado.append(token)
    return resultado[:6]


def buscar_productos(
    session: Session,
    Producto: Any,
    pregunta: str,
    limite: int = 10,
) -> List[Any]:
    tokens = obtener_tokens_producto(pregunta)
    if not tokens:
        return []

    condiciones = []
    for token in tokens:
        patron = f"%{token}%"
        condiciones.append(Producto.sku.ilike(patron))
        condiciones.append(Producto.nombre.ilike(patron))

    productos = session.exec(
        select(Producto)
        .where(or_(*condiciones))
        .limit(max(limite * 3, 20))
    ).all()

    def puntaje(producto: Any) -> Tuple[int, int, str]:
        sku = normalizar(getattr(producto, "sku", ""))
        nombre = normalizar(getattr(producto, "nombre", ""))
        combinado = f"{sku} {nombre}"
        coincidencias = sum(1 for token in tokens if token in combinado)
        exactitud = 2 if normalizar(" ".join(tokens)) in combinado else 0
        return (coincidencias + exactitud, -len(nombre), nombre)

    return sorted(productos, key=puntaje, reverse=True)[:limite]


def ubicacion_producto(producto: Any) -> str:
    rack = str(getattr(producto, "rack", "") or "-")
    nivel = str(getattr(producto, "nivel", "") or "-")
    slot = str(getattr(producto, "slot", "") or "-")
    return f"Rack {rack}, nivel {nivel}, posición {slot}"


def responder_busqueda_producto(
    session: Session,
    Producto: Any,
    pregunta: str,
) -> Dict[str, Any]:
    productos = buscar_productos(session, Producto, pregunta)
    if not productos:
        return respuesta_base(
            "No encontré un producto que coincida. Escribe el SKU o un nombre más específico.",
            accion=accion_navegar("/products"),
        )

    texto = normalizar(pregunta)
    consulta_ubicacion = contiene(texto, ("donde", "ubicacion", "ubicado", "ubicada"))

    if len(productos) == 1:
        producto = productos[0]
        nombre = str(getattr(producto, "nombre", "Producto"))
        sku = str(getattr(producto, "sku", ""))
        cantidad = int(getattr(producto, "cantidad", 0) or 0)
        ubicacion = ubicacion_producto(producto)

        if consulta_ubicacion:
            respuesta = f"{nombre} ({sku}) está en {ubicacion}. Stock: {cantidad} unidades."
        else:
            respuesta = f"Tienes {cantidad} unidades de {nombre} ({sku}). Ubicación: {ubicacion}."

        return respuesta_base(
            respuesta,
            accion=accion_navegar("/products"),
        )

    partes = []
    for producto in productos[:5]:
        partes.append(
            f"{getattr(producto, 'nombre', 'Producto')} "
            f"({getattr(producto, 'sku', '')}): "
            f"{int(getattr(producto, 'cantidad', 0) or 0)} unidades, "
            f"{ubicacion_producto(producto)}"
        )

    adicionales = len(productos) - len(partes)
    cierre = f" Hay {adicionales} coincidencias adicionales." if adicionales > 0 else ""
    return respuesta_base(
        "Encontré varias coincidencias: " + "; ".join(partes) + "." + cierre,
        accion=accion_navegar("/products"),
    )


def obtener_stock_bajo(session: Session, Producto: Any, limite: int = 20) -> List[Any]:
    return session.exec(
        select(Producto)
        .where(Producto.cantidad <= Producto.stock_minimo)
        .order_by(Producto.cantidad)
        .limit(limite)
    ).all()


def responder_stock_bajo(session: Session, Producto: Any) -> Dict[str, Any]:
    productos = obtener_stock_bajo(session, Producto)
    if not productos:
        return respuesta_base(
            "No tienes productos en stock bajo.",
            accion=accion_navegar("/products"),
        )

    partes = [
        f"{getattr(producto, 'nombre', 'Producto')} "
        f"({int(getattr(producto, 'cantidad', 0) or 0)}/"
        f"{int(getattr(producto, 'stock_minimo', 0) or 0)})"
        for producto in productos[:5]
    ]
    adicionales = len(productos) - len(partes)
    cierre = f" y {adicionales} más" if adicionales > 0 else ""
    return respuesta_base(
        f"Hay {len(productos)} productos en stock bajo. Los más urgentes son: "
        + ", ".join(partes)
        + cierre
        + ".",
        accion=accion_navegar("/products"),
    )


def obtener_caducidades(
    session: Session,
    Producto: Any,
    limite: int = 20,
) -> List[Any]:
    hoy = date.today()
    fecha_limite = hoy + timedelta(days=30)
    return session.exec(
        select(Producto)
        .where(Producto.caducidad.is_not(None))
        .where(Producto.caducidad <= fecha_limite)
        .order_by(Producto.caducidad)
        .limit(limite)
    ).all()


def responder_caducidades(session: Session, Producto: Any) -> Dict[str, Any]:
    productos = obtener_caducidades(session, Producto)
    if not productos:
        return respuesta_base(
            "No hay productos registrados para caducar en los próximos 30 días.",
            accion=accion_navegar("/products"),
        )

    hoy = date.today()
    partes = []
    for producto in productos[:5]:
        caducidad = getattr(producto, "caducidad", None)
        dias = (caducidad - hoy).days if caducidad else 0
        estado = "caducado" if dias < 0 else ("hoy" if dias == 0 else f"en {dias} días")
        partes.append(f"{getattr(producto, 'nombre', 'Producto')} ({estado})")

    adicionales = len(productos) - len(partes)
    cierre = f" y {adicionales} más" if adicionales > 0 else ""
    return respuesta_base(
        f"Hay {len(productos)} productos caducados o por caducar dentro de 30 días: "
        + ", ".join(partes)
        + cierre
        + ".",
        accion=accion_navegar("/products"),
    )


def resumen_inventario(session: Session, Producto: Any) -> Dict[str, Any]:
    # Se procesan los registros dentro del backend. El modelo de IA nunca recibe
    # la lista completa, únicamente este resumen y como máximo algunos ejemplos.
    productos = session.exec(select(Producto)).all()
    unidades = sum(max(int(getattr(p, "cantidad", 0) or 0), 0) for p in productos)
    stock_bajo = [
        p
        for p in productos
        if int(getattr(p, "cantidad", 0) or 0)
        <= int(getattr(p, "stock_minimo", 0) or 0)
    ]
    stock_alto = [
        p
        for p in productos
        if int(getattr(p, "cantidad", 0) or 0)
        >= int(getattr(p, "stock_alto", 0) or 0)
    ]
    hoy = date.today()
    proximos = [
        p
        for p in productos
        if getattr(p, "caducidad", None) is not None
        and getattr(p, "caducidad") <= hoy + timedelta(days=30)
    ]
    valor_costo = round(
        sum(
            max(int(getattr(p, "cantidad", 0) or 0), 0)
            * float(getattr(p, "costo_proveedor", 0) or 0)
            for p in productos
        ),
        2,
    )
    return {
        "productos_distintos": len(productos),
        "unidades_totales": unidades,
        "productos_stock_bajo": len(stock_bajo),
        "productos_stock_alto": len(stock_alto),
        "caducados_o_30_dias": len(proximos),
        "valor_inventario_a_costo": valor_costo,
        "stock_bajo_muestra": [
            {
                "sku": getattr(p, "sku", ""),
                "nombre": getattr(p, "nombre", ""),
                "cantidad": int(getattr(p, "cantidad", 0) or 0),
                "minimo": int(getattr(p, "stock_minimo", 0) or 0),
            }
            for p in sorted(stock_bajo, key=lambda item: int(getattr(item, "cantidad", 0) or 0))[:10]
        ],
        "caducidad_muestra": [
            {
                "sku": getattr(p, "sku", ""),
                "nombre": getattr(p, "nombre", ""),
                "caducidad": str(getattr(p, "caducidad", "")),
            }
            for p in sorted(
                proximos,
                key=lambda item: getattr(item, "caducidad", date.max) or date.max,
            )[:10]
        ],
    }


def responder_resumen_inventario(session: Session, Producto: Any) -> Dict[str, Any]:
    resumen = resumen_inventario(session, Producto)
    return respuesta_base(
        f"Tienes {resumen['productos_distintos']} productos y "
        f"{resumen['unidades_totales']} unidades. "
        f"Hay {resumen['productos_stock_bajo']} con stock bajo y "
        f"{resumen['caducados_o_30_dias']} caducados o por caducar en 30 días.",
        accion=accion_navegar("/"),
    )


def resumen_ventas(
    session: Session,
    Movimiento: Any,
    dias: int = 30,
) -> Dict[str, Any]:
    inicio = datetime.now() - timedelta(days=dias)
    movimientos = session.exec(
        select(Movimiento).where(Movimiento.fecha >= inicio)
    ).all()

    ventas: Dict[str, Dict[str, Any]] = {}
    total_unidades = 0
    ingresos = 0.0
    costos = 0.0

    for mov in movimientos:
        if normalizar(getattr(mov, "accion", "")) != "egreso":
            continue
        sku = str(getattr(mov, "sku", "") or "").strip()
        if not sku:
            continue
        cantidad = max(int(getattr(mov, "cantidad", 0) or 0), 0)
        ingreso = float(getattr(mov, "ingreso_total", 0) or 0)
        costo = float(getattr(mov, "costo_total", 0) or 0)
        total_unidades += cantidad
        ingresos += ingreso
        costos += costo
        item = ventas.setdefault(
            sku,
            {
                "sku": sku,
                "nombre": str(getattr(mov, "producto", "") or sku),
                "unidades": 0,
                "ingresos": 0.0,
                "ganancia": 0.0,
            },
        )
        item["unidades"] += cantidad
        item["ingresos"] += ingreso
        item["ganancia"] += ingreso - costo

    top_unidades = sorted(ventas.values(), key=lambda item: item["unidades"], reverse=True)[:10]
    top_ganancia = sorted(ventas.values(), key=lambda item: item["ganancia"], reverse=True)[:10]
    return {
        "periodo_dias": dias,
        "unidades_vendidas": total_unidades,
        "ingresos": round(ingresos, 2),
        "costos": round(costos, 2),
        "ganancia": round(ingresos - costos, 2),
        "productos_con_venta": len(ventas),
        "mas_vendidos": top_unidades,
        "mas_rentables": top_ganancia,
    }


def responder_resumen_ventas(session: Session, Movimiento: Any) -> Dict[str, Any]:
    resumen = resumen_ventas(session, Movimiento)
    if resumen["unidades_vendidas"] <= 0:
        return respuesta_base(
            "No hay ventas registradas durante los últimos 30 días.",
            accion=accion_navegar("/finanzas"),
        )

    top = resumen["mas_vendidos"][0] if resumen["mas_vendidos"] else None
    extra = f" El más vendido fue {top['nombre']} con {top['unidades']} unidades." if top else ""
    return respuesta_base(
        f"En los últimos 30 días se vendieron {resumen['unidades_vendidas']} unidades, "
        f"con ingresos de ${resumen['ingresos']:,.2f} y ganancia de "
        f"${resumen['ganancia']:,.2f}.{extra}",
        accion=accion_navegar("/finanzas"),
    )


def detectar_intencion(pregunta: str) -> str:
    texto = normalizar(pregunta)

    if contiene(texto, ("stock bajo", "stock critico", "stock minimo", "por agotarse")):
        return "stock_bajo"
    if contiene(texto, ("caduc", "vencer", "vence", "vencidos", "expirar")):
        return "caducidades"
    if es_pregunta_ayuda(pregunta):
        return "ayuda"
    if contiene(
        texto,
        (
            "resumen del inventario",
            "resumen de inventario",
            "como esta mi inventario",
            "estado del inventario",
            "panorama del inventario",
        ),
    ):
        return "resumen_inventario"
    if contiene(
        texto,
        (
            "ventas",
            "vendido",
            "mas vendido",
            "menos vendido",
            "ganancia",
            "rentable",
            "rentabilidad",
            "ingresos",
        ),
    ) and detectar_tipo_respuesta(pregunta) == "directa":
        return "resumen_ventas"
    if contiene(
        texto,
        (
            "cuantas",
            "cuantos",
            "cantidad",
            "donde esta",
            "donde estan",
            "ubicacion",
            "tengo de",
            "existencia",
            "sku",
        ),
    ):
        return "buscar_producto"
    return "analisis"


def contexto_pagina(ruta_actual: Optional[str], rol: str) -> Dict[str, Any]:
    pagina = PAGINAS.get(ruta_actual or "")
    if not pagina:
        return {"ruta": ruta_actual, "nombre": "Página no identificada", "rol": rol}
    return {
        "ruta": ruta_actual,
        "nombre": pagina["nombre"],
        "descripcion": pagina["descripcion"],
        "rol": rol,
    }

def preparar_historial(
    historial: Optional[Sequence[Any]],
) -> List[Dict[str, str]]:
    mensajes: List[Dict[str, str]] = []

    for item in list(historial or [])[-3:]:
        if isinstance(item, dict):
            rol = str(item.get("rol") or "").strip()
            contenido = str(item.get("contenido") or "").strip()
        else:
            rol = str(getattr(item, "rol", "") or "").strip()
            contenido = str(
                getattr(item, "contenido", "") or ""
            ).strip()

        if not contenido:
            continue

        if rol == "usuario":
            role = "user"
        elif rol == "asistente":
            role = "assistant"
        else:
            continue

        mensajes.append(
            {
                "role": role,
                "content": contenido,
            }
        )

    return mensajes
    
def llamar_ia_compacta(
    *,
    pregunta: str,
    ruta_actual: Optional[str],
    rol: str,
    contexto: Dict[str, Any],
    user_id: str,
    historial: Optional[Sequence[Any]],
    solicitar_deepseek: DeepSeekRequester,
) -> Dict[str, Any]:
    is_local_runtime = normalizar(os.getenv("RACKNOVA_MODE")) == "local"
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not is_local_runtime and not api_key:
        raise RuntimeError("Falta configurar DEEPSEEK_API_KEY.")

    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    tipo = detectar_tipo_respuesta(pregunta)
    max_tokens = 4096
    system_prompt = """
Eres RackNova IA, el copiloto de la plataforma RackNova.

Reglas obligatorias:
1. Responde solamente lo necesario para contestar la pregunta actual.
3. Si la respuesta es directa, usa como máximo dos oraciones.
4. Si preguntan cómo realizar algo, indica entre 3 y 6 pasos concretos.
5. Solo haz un análisis amplio cuando el usuario lo pida explícitamente.
6. No agregues recomendaciones que no fueron solicitadas, salvo que exista un riesgo importante.
7. No inventes productos, cifras, páginas, botones ni funciones.
8. Usa únicamente los datos incluidos en CONTEXTO RACKNOVA para mencionar cifras.
9. Si faltan datos, dilo claramente en una sola frase.
10. Escribe en español claro, profesional y directo y muy amable.
""".strip()

    user_prompt = f"""
TIPO DE RESPUESTA: {tipo}
PÁGINA ACTUAL: {json.dumps(contexto_pagina(ruta_actual, rol), ensure_ascii=False)}
PREGUNTA ACTUAL: {pregunta}
CONTEXTO RACKNOVA: {json.dumps(contexto, ensure_ascii=False, default=str, separators=(',', ':'))}
""".strip()

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    messages.extend(preparar_historial(historial))

    messages.append(
        {
            "role": "user",
            "content": user_prompt,
        }
    )

    if is_local_runtime:
        # La PC local nunca recibe DEEPSEEK_API_KEY. El contexto compacto
        # viaja por HTTPS a RackNova Cloud usando la credencial del nodo.
        from racknova_ai_relay import request_deepseek_via_racknova_cloud

        resultado = request_deepseek_via_racknova_cloud(
            messages=messages,
            max_tokens=max_tokens,
            user_id=user_id,
        )
    else:
        resultado = solicitar_deepseek(
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            user_id=user_id,
        )

    contenido = str(resultado.get("content") or "").strip()
    if not contenido:
        raise RuntimeError("DeepSeek no devolvió contenido.")

    finish_reason = str(resultado.get("finish_reason") or "stop")
    return respuesta_base(
        contenido,
        fuente="deepseek",
        tipo=tipo,
        completa=finish_reason != "length",
        finish_reason=finish_reason,
        uso_tokens=resultado.get("usage") or {},
    )


def contexto_analisis(
    session: Session,
    Producto: Any,
    Movimiento: Any,
    pregunta: str,
) -> Dict[str, Any]:
    texto = normalizar(pregunta)
    contexto: Dict[str, Any] = {"inventario": resumen_inventario(session, Producto)}

    if contiene(
        texto,
        (
            "venta",
            "vendido",
            "ganancia",
            "rentable",
            "rotacion",
            "descuento",
            "ingreso",
        ),
    ):
        contexto["ventas_ultimos_30_dias"] = resumen_ventas(session, Movimiento)

    # El contexto solo contiene métricas y muestras limitadas; nunca la base completa.
    return contexto


def procesar_consulta_ia(
    *,
    pregunta: str,
    ruta_actual: Optional[str],
    pagina_actual: Optional[str],
    historial: Optional[Sequence[Any]],
    session: Session,
    current_user: Any,
    Producto: Any,
    Movimiento: Any,
    solicitar_deepseek: DeepSeekRequester,
) -> Dict[str, Any]:
    pregunta_limpia = str(pregunta or "").strip()
    if not pregunta_limpia:
        raise ValueError("La pregunta no puede estar vacía.")

    rol = normalizar(getattr(current_user, "rol", "operator")) or "operator"
    intencion = detectar_intencion(pregunta_limpia)

    if intencion == "ayuda":
        return responder_ayuda(pregunta_limpia, ruta_actual, rol)
    if intencion == "stock_bajo":
        return responder_stock_bajo(session, Producto)
    if intencion == "caducidades":
        return responder_caducidades(session, Producto)
    if intencion == "resumen_inventario":
        return responder_resumen_inventario(session, Producto)
    if intencion == "resumen_ventas":
        return responder_resumen_ventas(session, Movimiento)
    if intencion == "buscar_producto":
        return responder_busqueda_producto(session, Producto, pregunta_limpia)

    contexto = contexto_analisis(session, Producto, Movimiento, pregunta_limpia)
    user_id = f"racknova_{getattr(current_user, 'id_usuario', 'user')}"

    try:
        return llamar_ia_compacta(
            pregunta=pregunta_limpia,
            ruta_actual=ruta_actual,
            rol=rol,
            contexto=contexto,
            user_id=user_id,
            historial=historial,
            solicitar_deepseek=solicitar_deepseek,
        )
    except Exception as error:
        resumen = contexto.get("inventario", {})
        return respuesta_base(
            (
                "No pude completar el análisis con la IA externa. "
                f"El inventario tiene {resumen.get('productos_distintos', 0)} productos, "
                f"{resumen.get('productos_stock_bajo', 0)} con stock bajo y "
                f"{resumen.get('caducados_o_30_dias', 0)} caducados o por caducar en 30 días."
            ),
            fuente="motor_interno_fallback",
            tipo="directa",
            advertencia=f"La IA externa no estuvo disponible: {str(error)[:180]}",
        )
