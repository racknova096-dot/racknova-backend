"""
Aplica la integración de RackNova POS Fase 1 sobre el main.py actual.

Uso, desde la raíz de racknova-backend:
    python aplicar_pos_backend.py

El script crea main.py.backup_antes_pos y se detiene si no reconoce un ancla.
No vuelve a aplicar cambios ya presentes.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

MAIN = Path("main.py")
BACKUP = Path("main.py.backup_antes_pos")


def insert_after(text: str, anchor: str, addition: str, label: str) -> str:
    if addition.strip() in text:
        print(f"✓ {label}: ya estaba aplicado")
        return text
    if anchor not in text:
        raise RuntimeError(f"No se encontró el ancla para {label}: {anchor!r}")
    print(f"+ {label}")
    return text.replace(anchor, anchor + addition, 1)


def insert_before(text: str, anchor: str, addition: str, label: str) -> str:
    if addition.strip() in text:
        print(f"✓ {label}: ya estaba aplicado")
        return text
    if anchor not in text:
        raise RuntimeError(f"No se encontró el ancla para {label}: {anchor!r}")
    print(f"+ {label}")
    return text.replace(anchor, addition + anchor, 1)


def replace_optional(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"✓ {label}: ya estaba aplicado")
        return text
    if old not in text:
        print(f"⚠ {label}: no se encontró el bloque; revísalo manualmente")
        return text
    print(f"+ {label}")
    return text.replace(old, new, 1)


def main() -> int:
    if not MAIN.exists():
        print("ERROR: ejecuta este script desde la raíz de racknova-backend.")
        return 1

    original = MAIN.read_text(encoding="utf-8")
    if not BACKUP.exists():
        shutil.copy2(MAIN, BACKUP)
        print(f"✓ Respaldo creado: {BACKUP}")

    text = original

    text = insert_after(
        text,
        "import urllib.error\n",
        "\nfrom pos_module import registrar_modulo_pos\n",
        "importar módulo POS",
    )

    text = insert_after(
        text,
        "    descripcion: Optional[str] = None\n",
        "    codigo_barras: Optional[str] = Field(default=None, index=True)\n",
        "agregar codigo_barras a Producto",
    )

    migration_anchor = '''        agregar_columna_si_falta(\n            session,\n            "producto",\n            "descripcion",\n            "TEXT NULL",\n        )\n'''
    migration_addition = '''\n        agregar_columna_si_falta(\n            session,\n            "producto",\n            "codigo_barras",\n            "VARCHAR(128) NULL",\n        )\n'''
    text = insert_after(
        text,
        migration_anchor,
        migration_addition,
        "migración codigo_barras",
    )

    text = insert_after(
        text,
        "        producto.descripcion = normalizar_texto(producto.descripcion) or None\n",
        "        producto.codigo_barras = normalizar_texto(producto.codigo_barras) or None\n",
        "normalizar codigo_barras al crear/restock",
    )

    text = insert_after(
        text,
        "            producto_existente.descripcion = catalogo.descripcion\n",
        "            producto_existente.codigo_barras = (\n"
        "                producto.codigo_barras or producto_existente.codigo_barras\n"
        "            )\n",
        "conservar/actualizar código en restock",
    )

    text = insert_after(
        text,
        "        db_producto.precio_venta_sugerido = updated.precio_venta_sugerido or 0\n",
        "        db_producto.codigo_barras = (\n"
        "            normalizar_texto(updated.codigo_barras)\n"
        "            or db_producto.codigo_barras\n"
        "        )\n",
        "actualizar código de barras en edición",
    )

    register_block = '''# ==========================================================\n# PUNTO DE VENTA — MÓDULO OPCIONAL\n# ==========================================================\nregistrar_modulo_pos(\n    app=app,\n    get_session=get_session,\n    require_roles=require_roles,\n    Producto=Producto,\n    Movimiento=Movimiento,\n    mexico_now=mexico_now,\n    descontar_lotes_fefo=descontar_lotes_fefo,\n    obtener_caducidad_mas_proxima=obtener_caducidad_mas_proxima,\n)\n\n'''
    text = insert_before(
        text,
        "# ==========================================================\n# UTILIDADES IA — RACKNOVA\n",
        register_block,
        "registrar rutas POS",
    )

    old_pg = '''                    TRUNCATE TABLE\n                        producto_lote,\n                        movimiento,\n                        producto\n                    RESTART IDENTITY CASCADE;'''
    new_pg = '''                    TRUNCATE TABLE\n                        venta_pos_pago,\n                        venta_pos_detalle,\n                        venta_pos,\n                        producto_lote,\n                        movimiento,\n                        producto\n                    RESTART IDENTITY CASCADE;'''
    text = replace_optional(
        text,
        old_pg,
        new_pg,
        "incluir ventas POS en limpiar toda la base (PostgreSQL)",
    )

    old_mysql = '''            session.exec(text("DELETE FROM producto_lote;"))\n            session.exec(text("DELETE FROM movimiento;"))\n            session.exec(text("DELETE FROM producto;"))'''
    new_mysql = '''            session.exec(text("DELETE FROM venta_pos_pago;"))\n            session.exec(text("DELETE FROM venta_pos_detalle;"))\n            session.exec(text("DELETE FROM venta_pos;"))\n            session.exec(text("DELETE FROM producto_lote;"))\n            session.exec(text("DELETE FROM movimiento;"))\n            session.exec(text("DELETE FROM producto;"))'''
    text = replace_optional(
        text,
        old_mysql,
        new_mysql,
        "incluir ventas POS en limpiar toda la base (MySQL)",
    )

    MAIN.write_text(text, encoding="utf-8")
    print("\n✅ main.py actualizado.")
    print("Siguiente validación: python -m py_compile main.py pos_module.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("main.py no se sobrescribió después del punto de error; usa el respaldo si lo necesitas.")
        raise
