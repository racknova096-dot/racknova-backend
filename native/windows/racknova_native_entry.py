from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from racknova_dashboard_updater import (
    active_dashboard_dir,
    dashboard_update_status,
    start_dashboard_update_worker,
)
from racknova_native_config import apply_native_environment


def resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / name
    return Path(__file__).resolve().parent / name


def get_app() -> Any:
    config, _ = apply_native_environment()

    # Importar después de fijar DATABASE_URL y RACKNOVA_MODE.
    from main import app

    embedded_dashboard = resource_path("dashboard_dist")

    def _dashboard() -> tuple[Path, str]:
        return active_dashboard_dir(embedded_dashboard)

    @app.get(
        "/racknova-native/health",
        include_in_schema=False,
    )
    def racknova_native_health() -> dict[str, Any]:
        _, dashboard_source = _dashboard()
        update_status = dashboard_update_status()
        return {
            "ok": True,
            "runtime": "native_windows",
            "fase_instalador": "F1",
            "activated": bool(config.get("activated", False)),
            "node_code": config.get("node_code"),
            "ui": "/ui/",
            "dashboard": {
                "source": dashboard_source,
                "version": update_status.get("installed_version"),
                "update_state": update_status.get("state"),
            },
        }

    @app.get(
        "/racknova-native/dashboard-update/status",
        include_in_schema=False,
    )
    def racknova_dashboard_update_status() -> dict[str, Any]:
        _, source = _dashboard()
        return {
            "source": source,
            **dashboard_update_status(),
        }

    @app.get("/ui", include_in_schema=False)
    def racknova_ui_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.get("/ui/{full_path:path}", include_in_schema=False)
    def racknova_ui(full_path: str) -> FileResponse:
        dashboard, _ = _dashboard()
        index = dashboard / "index.html"
        if not index.exists():
            raise HTTPException(
                status_code=503,
                detail="Dashboard Local no fue empaquetado ni actualizado.",
            )

        relative = (full_path or "").strip("/")
        if relative:
            candidate = (dashboard / relative).resolve()
            try:
                candidate.relative_to(dashboard.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Recurso no válido.")

            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)

        # SPA fallback: /ui/products, /ui/pos, etc. No cacheamos index para
        # que una actualización validada aparezca con el siguiente refresh.
        return FileResponse(
            index,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # Solo crea un hilo daemon; la primera consulta remota se retrasa y nunca
    # bloquea el arranque del servicio Windows.
    start_dashboard_update_worker()

    return app
