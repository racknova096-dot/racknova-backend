from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import Column, Text, UniqueConstraint, text as sa_text
from sqlmodel import Field, Session, SQLModel, select

import multiempresa_tenant as rn_tenant


ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_IMAGE_BYTES = 800 * 1024


class ProductoImagen(SQLModel, table=True):
    __tablename__ = "producto_imagen"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id",
            "sku",
            name="uq_producto_imagen_empresa_sku",
        ),
    )

    empresa_id: UUID | None = Field(default=None, nullable=False, index=True)
    id_imagen: UUID = Field(default_factory=uuid4, primary_key=True)
    sku: str = Field(index=True, max_length=120)
    mime_type: str = Field(max_length=40)
    data_base64: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    byte_size: int = Field(default=0)
    sha256: str = Field(max_length=64, index=True)
    fecha_creacion: datetime
    fecha_actualizacion: datetime

    # Lista desde el nacimiento para RackNova Sync Local <-> Cloud.
    sync_uuid: UUID = Field(default_factory=uuid4, nullable=False, index=True)
    sync_revision: int = Field(default=0, nullable=False)
    sync_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    sync_origen_nodo: Optional[str] = Field(default=None, nullable=True, max_length=120)


class ProductoImagenUpsert(BaseModel):
    mime_type: str = PydanticField(min_length=1, max_length=40)
    data_base64: str = PydanticField(min_length=8)


def _now() -> datetime:
    return datetime.now().replace(tzinfo=None)


def _bind(
    session: Session,
    current_user: Any,
    empresa: Optional[str],
    *,
    write: bool,
) -> str:
    selected = rn_tenant.bind_empresa(
        session,
        current_user,
        empresa,
        allowed_roles=(
            {"owner", "admin", "operator"}
            if write
            else {"owner", "admin", "operator", "viewer"}
        ),
    )
    return str(selected["id_empresa"])


def _catalog_exists(session: Session, empresa_id: str, sku: str) -> bool:
    value = session.connection().execute(
        sa_text(
            """
            SELECT 1
            FROM producto_catalogo
            WHERE empresa_id = CAST(:empresa_id AS UUID)
              AND sku = :sku
            LIMIT 1
            """
        ),
        {"empresa_id": empresa_id, "sku": sku},
    ).scalar_one_or_none()
    return bool(value)


def _row_for(
    session: Session,
    empresa_id: str,
    sku: str,
) -> Optional[ProductoImagen]:
    return session.exec(
        select(ProductoImagen).where(
            (ProductoImagen.empresa_id == UUID(empresa_id))
            & (ProductoImagen.sku == sku)
        )
    ).first()


def _detect_type(raw: bytes) -> Optional[str]:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _decode_image(data: ProductoImagenUpsert) -> tuple[str, bytes, str]:
    mime = str(data.mime_type or "").strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Formato no permitido. Usa JPG, PNG o WEBP.",
        )

    encoded = str(data.data_base64 or "").strip()
    if encoded.startswith("data:"):
        try:
            encoded = encoded.split(",", 1)[1]
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Imagen inválida.") from exc

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="La imagen no contiene Base64 válido.") from exc

    if not raw:
        raise HTTPException(status_code=400, detail="La imagen está vacía.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="La imagen es demasiado grande. El máximo después de comprimir es 800 KB.",
        )

    detected = _detect_type(raw)
    if detected is None:
        raise HTTPException(status_code=400, detail="El archivo no parece ser una imagen JPG, PNG o WEBP válida.")
    if detected != mime:
        mime = detected

    canonical = base64.b64encode(raw).decode("ascii")
    return mime, raw, canonical


def _payload(row: ProductoImagen, *, include_data: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sku": row.sku,
        "mime_type": row.mime_type,
        "byte_size": int(row.byte_size or 0),
        "sha256": row.sha256,
        "fecha_actualizacion": row.fecha_actualizacion,
    }
    if include_data:
        result["data_base64"] = row.data_base64
    return result


def _capture_upsert(session: Session, local_vars: dict[str, Any]) -> None:
    # Importación diferida para no crear ciclos con racknova_runtime durante startup.
    from racknova_sync_capture import capture_operation_event

    capture_operation_event(
        session,
        event_type="catalog.product.image.upserted",
        local_vars=local_vars,
    )


def _capture_delete(session: Session, row: ProductoImagen, local_vars: dict[str, Any]) -> None:
    from racknova_sync_capture import capture_delete_tombstone

    capture_delete_tombstone(
        session,
        event_type="catalog.product.image.deleted",
        obj=row,
        local_vars=local_vars,
    )


def registrar_modulo_imagenes_producto(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    @app.get(
        "/catalogo/productos/{sku}/imagen",
        tags=["Catálogo - Imágenes"],
    )
    def obtener_imagen_producto(
        sku: str,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        empresa_id = _bind(session, current_user, rn_empresa_id, write=False)
        sku_clean = str(sku or "").strip()
        row = _row_for(session, empresa_id, sku_clean)
        if not row:
            raise HTTPException(status_code=404, detail="Este producto todavía no tiene imagen.")
        return _payload(row, include_data=True)

    @app.put(
        "/catalogo/productos/{sku}/imagen",
        tags=["Catálogo - Imágenes"],
    )
    def guardar_imagen_producto(
        sku: str,
        data: ProductoImagenUpsert,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        empresa_id = _bind(session, current_user, rn_empresa_id, write=True)
        sku_clean = str(sku or "").strip()
        if not sku_clean:
            raise HTTPException(status_code=400, detail="SKU obligatorio.")
        if not _catalog_exists(session, empresa_id, sku_clean):
            raise HTTPException(
                status_code=404,
                detail="Primero registra el producto en el catálogo.",
            )

        mime, raw, canonical = _decode_image(data)
        digest = hashlib.sha256(raw).hexdigest()
        row = _row_for(session, empresa_id, sku_clean)
        now = _now()

        if row is None:
            row = ProductoImagen(
                empresa_id=UUID(empresa_id),
                sku=sku_clean,
                mime_type=mime,
                data_base64=canonical,
                byte_size=len(raw),
                sha256=digest,
                fecha_creacion=now,
                fecha_actualizacion=now,
            )
        else:
            row.mime_type = mime
            row.data_base64 = canonical
            row.byte_size = len(raw)
            row.sha256 = digest
            row.fecha_actualizacion = now

        session.add(row)
        session.flush()
        _capture_upsert(session, locals())
        session.commit()
        session.refresh(row)

        return {
            **_payload(row, include_data=False),
            "mensaje": "Imagen del producto guardada correctamente.",
        }

    @app.delete(
        "/catalogo/productos/{sku}/imagen",
        tags=["Catálogo - Imágenes"],
    )
    def eliminar_imagen_producto(
        sku: str,
        session: Session = Depends(get_session),
        current_user: Any = Depends(get_current_user),
        rn_empresa_id: str | None = Header(default=None, alias="X-Empresa-ID"),
    ):
        empresa_id = _bind(session, current_user, rn_empresa_id, write=True)
        sku_clean = str(sku or "").strip()
        row = _row_for(session, empresa_id, sku_clean)
        if not row:
            return {"ok": True, "mensaje": "El producto ya estaba sin imagen."}

        _capture_delete(session, row, locals())
        session.delete(row)
        session.commit()
        return {"ok": True, "mensaje": "Imagen eliminada."}
