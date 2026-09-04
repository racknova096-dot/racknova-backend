from __future__ import annotations

from typing import Any, Callable

from racknova_runtime_base import *  # noqa: F401,F403
from racknova_runtime_base import register_runtime_routes as _register_runtime_routes_base
from product_images_module import registrar_modulo_imagenes_producto


def register_runtime_routes(
    *,
    app: Any,
    get_session: Callable[..., Any],
    get_current_user: Callable[..., Any],
) -> None:
    """Registra runtime base y extensiones comerciales compartidas Local/Cloud."""
    _register_runtime_routes_base(
        app=app,
        get_session=get_session,
        get_current_user=get_current_user,
    )
    registrar_modulo_imagenes_producto(
        app=app,
        get_session=get_session,
        get_current_user=get_current_user,
    )
