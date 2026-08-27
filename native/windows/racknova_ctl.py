from __future__ import annotations

import argparse
import json
import socket
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from racknova_native_config import (
    DEFAULT_DB_PORT,
    DEFAULT_EMPRESA_ID,
    apply_native_environment,
    config_path,
    diagnostics_dir,
    load_config,
    secrets_path,
)
from racknova_secrets import save_secret_json


def _resource(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / name
    root = Path(__file__).resolve().parents[2]
    return root / name


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _run_text(args: list[str]) -> str:
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    return proc.stdout.strip()


def bootstrap_secrets(source: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("bootstrap secrets debe ser un objeto JSON.")

    required = {"db_password", "pg_super_password", "jwt_secret"}
    missing = sorted(k for k in required if not str(payload.get(k) or ""))
    if missing:
        raise RuntimeError(
            "Faltan secretos obligatorios: " + ", ".join(missing)
        )

    save_secret_json(
        secrets_path(),
        {
            "db_password": str(payload["db_password"]),
            "pg_super_password": str(payload["pg_super_password"]),
            "jwt_secret": str(payload["jwt_secret"]),
            "node_credential": str(payload.get("node_credential") or ""),
        },
    )

    config = {
        "native_installer_phase": "F1",
        "activated": bool(payload.get("activated", False)),
        "empresa_id": str(payload.get("empresa_id") or DEFAULT_EMPRESA_ID),
        "node_code": str(
            payload.get("node_code")
            or f"LOCAL-{socket.gethostname().upper()}"
        )[:120],
        "node_name": str(
            payload.get("node_name")
            or f"RackNova Local - {socket.gethostname()}"
        )[:180],
        "cloud_url": str(payload.get("cloud_url") or "").rstrip("/"),
        "db_host": "127.0.0.1",
        "db_port": int(payload.get("db_port") or DEFAULT_DB_PORT),
        "db_name": "racknova",
        "db_user": "racknova_app",
        "sync_interval_seconds": 15,
        "app_version": str(payload.get("app_version") or "native-f1"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(_json_dump(config) + "\n", encoding="utf-8")

    try:
        source.unlink()
    except OSError:
        pass


def init_schema() -> None:
    apply_native_environment()

    # Importar main registra todos los modelos SQLModel actuales.
    import main  # noqa: F401
    from database import engine
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)

    migration_names = (
        "001_multiempresa_fase1.sql",
        "002_multiempresa_fase2_local_first.sql",
    )

    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        cursor = raw.cursor()
        try:
            for name in migration_names:
                path = _resource(name)
                if not path.exists():
                    raise RuntimeError(f"No se empaquetó {name}.")
                cursor.execute(path.read_text(encoding="utf-8"))
        finally:
            cursor.close()
    finally:
        raw.close()

    try:
        from racknova_sync_worker import ensure_sync_schema
        from sqlmodel import Session

        with Session(engine) as session:
            ensure_sync_schema(session)
    except Exception as exc:
        raise RuntimeError(
            f"Esquema base listo, pero falló ensure_sync_schema: {exc}"
        ) from exc


def health() -> dict[str, Any]:
    result: dict[str, Any] = {
        "runtime": "native_windows",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_exists": config_path().exists(),
        "secrets_exists": secrets_path().exists(),
        "database": {"ok": False},
        "http": {"ok": False},
    }

    try:
        apply_native_environment()
        from database import engine
        from sqlalchemy import text as sa_text

        with engine.connect() as conn:
            value = conn.execute(sa_text("SELECT 1")).scalar_one()
        result["database"] = {"ok": value == 1}
    except Exception as exc:
        result["database"] = {"ok": False, "error": str(exc)}

    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/racknova-native/health",
            timeout=5,
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
            result["http"] = {
                "ok": int(response.status) == 200,
                "status": int(response.status),
                "body": body[:2000],
            }
    except Exception as exc:
        result["http"] = {"ok": False, "error": str(exc)}

    return result


def diagnose() -> Path:
    out_dir = diagnostics_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = out_dir / f"_diag_{stamp}"
    work.mkdir(parents=True, exist_ok=True)

    safe_cfg = dict(load_config())
    safe_cfg.pop("database_url", None)
    (work / "config.REDACTED.json").write_text(
        _json_dump(safe_cfg) + "\n",
        encoding="utf-8",
    )
    (work / "health.json").write_text(
        _json_dump(health()) + "\n",
        encoding="utf-8",
    )

    service_report = {
        "RackNovaLocal": _run_text(["sc.exe", "query", "RackNovaLocal"]),
        "RackNovaPostgreSQL16": _run_text(
            ["sc.exe", "query", "RackNovaPostgreSQL16"]
        ),
    }
    (work / "services.json").write_text(
        _json_dump(service_report) + "\n",
        encoding="utf-8",
    )

    netstat = _run_text(["netstat.exe", "-ano"])
    relevant = "\n".join(
        line for line in netstat.splitlines()
        if ":8000" in line or ":54329" in line
    )
    (work / "ports.txt").write_text(relevant + "\n", encoding="utf-8")

    zip_path = out_dir / f"RackNova_Diagnostico_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in work.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(work))

    shutil_targets = sorted(
        work.rglob("*"),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for path in shutil_targets:
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            path.rmdir()
    work.rmdir()

    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(prog="RackNovaCtl")
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("bootstrap-secrets")
    boot.add_argument("--file", required=True)

    sub.add_parser("init-schema")
    sub.add_parser("health")
    sub.add_parser("diagnose")

    args = parser.parse_args()

    if args.command == "bootstrap-secrets":
        bootstrap_secrets(Path(args.file))
        print("OK: secretos protegidos con DPAPI.")
        return 0

    if args.command == "init-schema":
        init_schema()
        print("OK: esquema RackNova Local inicializado.")
        return 0

    if args.command == "health":
        result = health()
        print(_json_dump(result))
        return 0 if (
            result["database"].get("ok")
            and result["http"].get("ok")
        ) else 2

    if args.command == "diagnose":
        print(diagnose())
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
