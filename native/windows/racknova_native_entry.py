from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from racknova_native_config import apply_native_environment


def resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / name
    return Path(__file__).resolve().parent / name


def get_app() -> Any:
    config, _ = apply_native_environment()

    # Importar después de fijar DATABASE_URL y RACKNOVA_MODE.
    from main import app

    dashboard = resource_path("dashboard_dist")
    index = dashboard / "index.html"

    @app.get(
        "/racknova-native/health",
        include_in_schema=False,
    )
    def racknova_native_health() -> dict[str, Any]:
        return {
            "ok": True,
            "runtime": "native_windows",
            "fase_instalador": "F1",
            "activated": bool(config.get("activated", False)),
            "node_code": config.get("node_code"),
            "ui": "/ui/",
        }

    @app.get("/ui", include_in_schema=False)
    def racknova_ui_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.get("/ui/{full_path:path}", include_in_schema=False)
    def racknova_ui(full_path: str) -> FileResponse:
        if not index.exists():
            raise HTTPException(
                status_code=503,
                detail="Dashboard Local no fue empaquetado.",
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

        # SPA fallback: /ui/products, /ui/pos, etc.
        return FileResponse(index)

    return app
