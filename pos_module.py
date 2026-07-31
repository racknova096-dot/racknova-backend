from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import or_
from sqlmodel import Field, Session, SQLModel, select


# ==========================================================
# MODELOS POS
# ==========================================================


class POSConfiguracion(SQLModel, table=True):
    __tablename__ = "pos_configuracion"

    id_configuracion: Optional[int] = Field(default=None, primary_key=True)
    activo: bool = True
    fecha_actualizacion: datetime
    actualizado_por: str = "Sistema"


class VentaPOS(SQLModel, table=True):
    __tablename__ = "venta_pos"

    id_venta: Optional[int] = Field(default=None, primary_key=True)
    folio: str = Field(index=True)
    usuario: str
    subtotal: float = 0
    descuento_total: float = 0
    total: float = 0
    costo_total: float = 0
    ganancia: float = 0
    efectivo_recibido: float = 0
    cambio: float = 0
    estado: str = "COMPLETADA"
    fecha: datetime


class VentaPOSDetalle(SQLModel, table=True):
    __tablename__ = "venta_pos_detalle"

    id_detalle: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    id_producto: Optional[int] = Field(default=None, index=True)
    sku: str = Field(index=True)
    codigo_barras: Optional[str] = Field(default=None, index=True)
    nombre: str
    cantidad: int
    precio_lista: float
    descuento_porcentaje: float = 0
    precio_unitario_final: float
    subtotal: float
    costo_unitario: float
    costo_total: float
    ganancia: float


class VentaPOSPago(SQLModel, table=True):
    __tablename__ = "venta_pos_pago"

    id_pago: Optional[int] = Field(default=None, primary_key=True)
    id_venta: int = Field(index=True)
    metodo: str
    monto: float
    referencia: Optional[str] = None


# ==========================================================
# REQUESTS
# ==========================================================


class POSConfiguracionRequest(BaseModel):
    activo: bool


class POSVentaItemRequest(BaseModel):
    sku: str = PydanticField(min_length=1)
    cantidad: int = PydanticField(gt=0)
    descuento_porcentaje: float = PydanticField(default=0, ge=0, le=100)


class POSPagoRequest(BaseModel):
    metodo: str = PydanticField(min_length=1)
    monto: float = PydanticField(gt=0)
    referencia: Optional[str] = None


class POSVentaRequest(BaseModel):
    items: List[POSVentaItemRequest] = PydanticField(min_length=1)
    pagos: List[POSPagoRequest] = PydanticField(min_length=1)
    efectivo_recibido: Optional[float] = PydanticField(default=None, ge=0)


# ==========================================================
# UTILIDADES
# ==========================================================


def _env_pos_habilitado() -> bool:
    value = os.getenv("POS_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "si", "sí", "on"}


def _dinero(value: Any) -> float:
    return round(float(value or 0), 2)


def _usuario_nombre(current_user: Any) -> str:
    return str(
        getattr(current_user, "nombre", None)
        or getattr(current_user, "usuario", None)
        or "Usuario"
    )


def _usuario_rol(current_user: Any) -> str:
    return str(getattr(current_user, "rol", "operator") or "operator").lower()


def _obtener_configuracion(session: Session) -> Optional[POSConfiguracion]:
    return session.exec(select(POSConfiguracion)).first()


def _config_pos_habilitado(session: Session) -> bool:
    config = _obtener_configuracion(session)
    return True if config is None else bool(config.activo)


def _pos_habilitado(session: Session) -> bool:
    return _env_pos_habilitado() and _config_pos_habilitado(session)


def _exigir_pos_habilitado(session: Session) -> None:
    if not _pos_habilitado(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El módulo Punto de Venta está desactivado.",
        )


def _folio_venta(fecha: datetime) -> str:
    sufijo = uuid4().hex[:6].upper()
    return f"RN-{fecha:%Y%m%d-%H%M%S}-{sufijo}"


def _serializar_venta(
    session: Session,
    venta: VentaPOS,
    incluir_detalle: bool = True,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id_venta": venta.id_venta,
        "folio": venta.folio,
        "usuario": venta.usuario,
        "subtotal": _dinero(venta.subtotal),
        "descuento_total": _dinero(venta.descuento_total),
        "total": _dinero(venta.total),
        "costo_total": _dinero(venta.costo_total),
        "ganancia": _dinero(venta.ganancia),
        "efectivo_recibido": _dinero(venta.efectivo_recibido),
        "cambio": _dinero(venta.cambio),
        "estado": venta.estado,
        "fecha": venta.fecha,
    }

    if incluir_detalle and venta.id_venta is not None:
        detalles = session.exec(
            select(VentaPOSDetalle).where(
                VentaPOSDetalle.id_venta == venta.id_venta
            )
        ).all()
        pagos = session.exec(
            select(VentaPOSPago).where(VentaPOSPago.id_venta == venta.id_venta)
        ).all()

        data["items"] = [
            {
                "id_detalle": item.id_detalle,
                "id_producto": item.id_producto,
                "sku": item.sku,
                "codigo_barras": item.codigo_barras,
                "nombre": item.nombre,
                "cantidad": item.cantidad,
                "precio_lista": _dinero(item.precio_lista),
                "descuento_porcentaje": _dinero(item.descuento_porcentaje),
                "precio_unitario_final": _dinero(item.precio_unitario_final),
                "subtotal": _dinero(item.subtotal),
                "costo_unitario": _dinero(item.costo_unitario),
                "costo_total": _dinero(item.costo_total),
                "ganancia": _dinero(item.ganancia),
            }
            for item in detalles
        ]
        data["pagos"] = [
            {
                "id_pago": pago.id_pago,
                "metodo": pago.metodo,
                "monto": _dinero(pago.monto),
                "referencia": pago.referencia,
            }
            for pago in pagos
        ]

    return data


# ==========================================================
# REGISTRO DE RUTAS
# ==========================================================


def registrar_modulo_pos(
    *,
    app: Any,
    get_session: Callable[..., Any],
    require_roles: Callable[..., Any],
    Producto: Any,
    Movimiento: Any,
    mexico_now: Callable[[], datetime],
    descontar_lotes_fefo: Callable[..., List[Dict[str, Any]]],
    obtener_caducidad_mas_proxima: Callable[..., Any],
) -> None:
    read_user = require_roles("admin", "operator", "viewer")
    operator_user = require_roles("admin", "operator")
    admin_user = require_roles("admin")

    @app.get("/pos/estado")
    def obtener_estado_pos(
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
    ):
        config = _obtener_configuracion(session)
        return {
            "habilitado": _pos_habilitado(session),
            "env_habilitado": _env_pos_habilitado(),
            "config_habilitado": True if config is None else bool(config.activo),
            "mensaje": (
                "Punto de Venta disponible."
                if _pos_habilitado(session)
                else "Punto de Venta desactivado."
            ),
        }

    @app.put("/pos/configuracion")
    def actualizar_configuracion_pos(
        data: POSConfiguracionRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(admin_user),
    ):
        config = _obtener_configuracion(session)
        ahora = mexico_now()

        if config is None:
            config = POSConfiguracion(
                activo=data.activo,
                fecha_actualizacion=ahora,
                actualizado_por=_usuario_nombre(current_user),
            )
        else:
            config.activo = data.activo
            config.fecha_actualizacion = ahora
            config.actualizado_por = _usuario_nombre(current_user)

        session.add(config)
        session.commit()
        session.refresh(config)

        return {
            "habilitado": _pos_habilitado(session),
            "env_habilitado": _env_pos_habilitado(),
            "config_habilitado": bool(config.activo),
            "mensaje": (
                "Punto de Venta activado."
                if config.activo
                else "Punto de Venta desactivado."
            ),
        }

    @app.get("/pos/productos/buscar")
    def buscar_producto_pos(
        query: str = Query(..., min_length=1),
        limite: int = Query(default=20, ge=1, le=50),
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),
    ):
        _exigir_pos_habilitado(session)
        valor = query.strip()
        if not valor:
            return []

        # Coincidencia exacta primero: ideal para pistola, SKU o escritura manual.
        exacto = session.exec(
            select(Producto).where(
                or_(
                    Producto.sku == valor,
                    Producto.codigo_barras == valor,
                )
            )
        ).first()

        if exacto:
            productos = [exacto]
        else:
            patron = f"%{valor}%"
            productos = session.exec(
                select(Producto)
                .where(
                    or_(
                        Producto.sku.ilike(patron),
                        Producto.nombre.ilike(patron),
                        Producto.codigo_barras.ilike(patron),
                    )
                )
                .limit(limite)
            ).all()

        return [
            {
                "id_producto": producto.id_producto,
                "sku": producto.sku,
                "codigo_barras": producto.codigo_barras,
                "nombre": producto.nombre,
                "descripcion": producto.descripcion,
                "cantidad": int(producto.cantidad or 0),
                "precio_venta_sugerido": _dinero(
                    producto.precio_venta_sugerido
                ),
                "costo_proveedor": _dinero(producto.costo_proveedor),
                "ubicacion": f"{producto.rack}-{producto.nivel}-{producto.slot}",
                "rack": producto.rack,
                "nivel": producto.nivel,
                "slot": producto.slot,
                "caducidad": producto.caducidad,
            }
            for producto in productos
        ]

    @app.post("/pos/ventas")
    def crear_venta_pos(
        data: POSVentaRequest,
        session: Session = Depends(get_session),
        current_user: Any = Depends(operator_user),
    ):
        _exigir_pos_habilitado(session)
        ahora = mexico_now()
        rol = _usuario_rol(current_user)
        descuento_maximo = 100.0 if rol == "admin" else 10.0

        # Agrupa SKU repetidos para impedir descuentos inconsistentes y dobles líneas.
        items_agrupados: Dict[str, POSVentaItemRequest] = {}
        for item in data.items:
            sku = item.sku.strip()
            if not sku:
                raise HTTPException(status_code=400, detail="SKU inválido.")

            if item.descuento_porcentaje > descuento_maximo:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Tu rol permite un descuento máximo de "
                        f"{descuento_maximo:.0f}% por producto."
                    ),
                )

            existente = items_agrupados.get(sku)
            if existente:
                if abs(
                    existente.descuento_porcentaje - item.descuento_porcentaje
                ) > 0.001:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"El SKU {sku} aparece con descuentos diferentes."
                        ),
                    )
                existente.cantidad += item.cantidad
            else:
                items_agrupados[sku] = POSVentaItemRequest(
                    sku=sku,
                    cantidad=item.cantidad,
                    descuento_porcentaje=item.descuento_porcentaje,
                )

        calculados: List[Dict[str, Any]] = []
        subtotal_lista = 0.0
        descuento_total = 0.0
        total = 0.0
        costo_total_venta = 0.0

        try:
            for item in items_agrupados.values():
                statement = select(Producto).where(Producto.sku == item.sku)
                try:
                    statement = statement.with_for_update()
                except AttributeError:
                    pass

                producto = session.exec(statement).first()
                if not producto:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Producto {item.sku} no encontrado.",
                    )

                cantidad_disponible = int(producto.cantidad or 0)
                if item.cantidad > cantidad_disponible:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Stock insuficiente para {producto.nombre}. "
                            f"Disponible: {cantidad_disponible}."
                        ),
                    )

                precio_lista = _dinero(producto.precio_venta_sugerido)
                if precio_lista <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{producto.nombre} no tiene precio de venta configurado."
                        ),
                    )

                descuento_unitario = _dinero(
                    precio_lista * (item.descuento_porcentaje / 100)
                )
                precio_final = _dinero(precio_lista - descuento_unitario)
                subtotal_linea_lista = _dinero(precio_lista * item.cantidad)
                subtotal_linea = _dinero(precio_final * item.cantidad)
                descuento_linea = _dinero(
                    subtotal_linea_lista - subtotal_linea
                )
                costo_unitario = _dinero(producto.costo_proveedor)
                costo_linea = _dinero(costo_unitario * item.cantidad)
                ganancia_linea = _dinero(subtotal_linea - costo_linea)

                subtotal_lista = _dinero(
                    subtotal_lista + subtotal_linea_lista
                )
                descuento_total = _dinero(
                    descuento_total + descuento_linea
                )
                total = _dinero(total + subtotal_linea)
                costo_total_venta = _dinero(
                    costo_total_venta + costo_linea
                )

                calculados.append(
                    {
                        "request": item,
                        "producto": producto,
                        "precio_lista": precio_lista,
                        "precio_final": precio_final,
                        "subtotal": subtotal_linea,
                        "costo_unitario": costo_unitario,
                        "costo_total": costo_linea,
                        "ganancia": ganancia_linea,
                    }
                )

            metodos_validos = {"efectivo", "tarjeta", "transferencia"}
            pagos_normalizados: List[Dict[str, Any]] = []
            monto_pagado = 0.0
            efectivo_aplicado = 0.0

            for pago in data.pagos:
                metodo = pago.metodo.strip().lower()
                if metodo not in metodos_validos:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Método inválido. Usa efectivo, tarjeta o transferencia."
                        ),
                    )

                monto = _dinero(pago.monto)
                monto_pagado = _dinero(monto_pagado + monto)
                if metodo == "efectivo":
                    efectivo_aplicado = _dinero(efectivo_aplicado + monto)

                pagos_normalizados.append(
                    {
                        "metodo": metodo,
                        "monto": monto,
                        "referencia": (
                            pago.referencia.strip()
                            if pago.referencia
                            else None
                        ),
                    }
                )

            if abs(monto_pagado - total) > 0.01:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Los pagos suman ${monto_pagado:.2f}, "
                        f"pero el total es ${total:.2f}."
                    ),
                )

            recibido = _dinero(
                efectivo_aplicado
                if data.efectivo_recibido is None
                else data.efectivo_recibido
            )
            if efectivo_aplicado > 0 and recibido < efectivo_aplicado:
                raise HTTPException(
                    status_code=400,
                    detail="El efectivo recibido no cubre el pago en efectivo.",
                )
            if efectivo_aplicado <= 0:
                recibido = 0

            cambio = _dinero(max(recibido - efectivo_aplicado, 0))
            ganancia_venta = _dinero(total - costo_total_venta)

            venta = VentaPOS(
                folio=_folio_venta(ahora),
                usuario=_usuario_nombre(current_user),
                subtotal=subtotal_lista,
                descuento_total=descuento_total,
                total=total,
                costo_total=costo_total_venta,
                ganancia=ganancia_venta,
                efectivo_recibido=recibido,
                cambio=cambio,
                estado="COMPLETADA",
                fecha=ahora,
            )
            session.add(venta)
            session.flush()

            if venta.id_venta is None:
                raise RuntimeError("No fue posible generar el identificador de venta.")

            for calculado in calculados:
                item = calculado["request"]
                producto = calculado["producto"]

                lotes = descontar_lotes_fefo(
                    session=session,
                    sku=producto.sku,
                    cantidad=item.cantidad,
                )

                producto.cantidad = int(producto.cantidad or 0) - item.cantidad
                producto.caducidad = obtener_caducidad_mas_proxima(
                    session,
                    producto.sku,
                )
                producto.ultima_actualizacion = ahora
                session.add(producto)

                detalle = VentaPOSDetalle(
                    id_venta=venta.id_venta,
                    id_producto=producto.id_producto,
                    sku=producto.sku,
                    codigo_barras=producto.codigo_barras,
                    nombre=producto.nombre,
                    cantidad=item.cantidad,
                    precio_lista=calculado["precio_lista"],
                    descuento_porcentaje=item.descuento_porcentaje,
                    precio_unitario_final=calculado["precio_final"],
                    subtotal=calculado["subtotal"],
                    costo_unitario=calculado["costo_unitario"],
                    costo_total=calculado["costo_total"],
                    ganancia=calculado["ganancia"],
                )
                session.add(detalle)

                ubicacion = f"{producto.rack}-{producto.nivel}-{producto.slot}"
                movimiento = Movimiento(
                    accion="Egreso",
                    sku=producto.sku,
                    producto=producto.nombre,
                    cantidad=item.cantidad,
                    ubicacion=ubicacion,
                    usuario=_usuario_nombre(current_user),
                    fecha=ahora,
                    costo_proveedor=calculado["costo_unitario"],
                    precio_venta=calculado["precio_final"],
                    ingreso_total=calculado["subtotal"],
                    costo_total=calculado["costo_total"],
                    ganancia=calculado["ganancia"],
                )
                session.add(movimiento)

                # Guardamos el detalle FEFO en memoria para devolverlo en el ticket.
                calculado["lotes"] = lotes

            for pago in pagos_normalizados:
                session.add(
                    VentaPOSPago(
                        id_venta=venta.id_venta,
                        metodo=pago["metodo"],
                        monto=pago["monto"],
                        referencia=pago["referencia"],
                    )
                )

            session.commit()
            session.refresh(venta)

            respuesta = _serializar_venta(session, venta, incluir_detalle=True)
            respuesta["mensaje"] = "Venta registrada correctamente."
            respuesta["lotes_descontados"] = [
                {
                    "sku": item["producto"].sku,
                    "lotes": item.get("lotes", []),
                }
                for item in calculados
            ]
            return respuesta

        except HTTPException:
            session.rollback()
            raise
        except Exception as error:
            session.rollback()
            print(f"❌ Error creando venta POS: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo registrar la venta: {str(error)}",
            ) from error

    @app.get("/pos/ventas")
    def listar_ventas_pos(
        limite: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
    ):
        _exigir_pos_habilitado(session)
        ventas = session.exec(
            select(VentaPOS).order_by(VentaPOS.fecha.desc()).limit(limite)
        ).all()
        return [
            _serializar_venta(session, venta, incluir_detalle=False)
            for venta in ventas
        ]

    @app.get("/pos/ventas/{id_venta}")
    def obtener_venta_pos(
        id_venta: int,
        session: Session = Depends(get_session),
        current_user: Any = Depends(read_user),
    ):
        _exigir_pos_habilitado(session)
        venta = session.get(VentaPOS, id_venta)
        if not venta:
            raise HTTPException(status_code=404, detail="Venta no encontrada.")
        return _serializar_venta(session, venta, incluir_detalle=True)
