from __future__ import annotations

import argparse
import getpass
import json
import socket
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


def _configure_utf8_streams() -> None:
    """Avoid cp1252 crashes when imported modules print Unicode on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_streams()

from racknova_native_config import (
    DEFAULT_DB_PORT,
    DEFAULT_EMPRESA_ID,
    apply_native_environment,
    config_path,
    diagnostics_dir,
    load_config,
    load_secrets,
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



def _cloud_json(
    *,
    method: str,
    url: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "X-RackNova-Sync-Secret": secret,
    }

    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")

    request = urllib.request.Request(
        url=url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")

    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        raise RuntimeError(
            f"RackNova Cloud respondió HTTP {exc.code}: "
            f"{body[:1000]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"No pude conectar con RackNova Cloud: {exc}"
        ) from exc

    if not raw.strip():
        return {}

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "RackNova Cloud devolvió una respuesta no JSON."
        ) from exc

    if not isinstance(value, dict):
        raise RuntimeError(
            "RackNova Cloud devolvió un formato inesperado."
        )

    return value


def activation_status() -> dict[str, Any]:
    config = load_config()
    secrets = load_secrets()

    return {
        "activated": bool(config.get("activated", False)),
        "empresa_id": str(
            config.get("empresa_id") or DEFAULT_EMPRESA_ID
        ),
        "node_code": str(config.get("node_code") or ""),
        "node_name": str(config.get("node_name") or ""),
        "cloud_url": str(config.get("cloud_url") or ""),
        "node_credential_configured": bool(
            str(secrets.get("node_credential") or "").strip()
        ),
        "sync_interval_seconds": int(
            config.get("sync_interval_seconds") or 15
        ),
    }


def activate_cloud(
    *,
    cloud_url: str,
    empresa_id: str,
    sync_secret: str,
    node_code: str | None = None,
    node_name: str | None = None,
    sync_interval: int = 15,
) -> dict[str, Any]:
    cloud_url = str(cloud_url or "").strip().rstrip("/")
    sync_secret = str(sync_secret or "").strip()

    if not cloud_url:
        raise RuntimeError("cloud_url es obligatorio.")

    allowed_http = (
        cloud_url.startswith("http://127.0.0.1")
        or cloud_url.startswith("http://localhost")
    )

    if not cloud_url.startswith("https://") and not allowed_http:
        raise RuntimeError(
            "RackNova Cloud debe usar HTTPS. "
            "HTTP solo se permite para localhost."
        )

    if len(sync_secret) < 20:
        raise RuntimeError(
            "RACKNOVA_SYNC_SECRET debe tener al menos 20 caracteres."
        )

    try:
        empresa_id = str(UUID(str(empresa_id)))
    except ValueError as exc:
        raise RuntimeError(
            "empresa_id no es un UUID válido."
        ) from exc

    if not 5 <= int(sync_interval) <= 3600:
        raise RuntimeError(
            "sync_interval debe estar entre 5 y 3600 segundos."
        )

    current_config = load_config()
    current_secrets = load_secrets()

    current_empresa_raw = str(
        current_config.get("empresa_id")
        or DEFAULT_EMPRESA_ID
    )

    try:
        current_empresa = str(UUID(current_empresa_raw))
    except ValueError as exc:
        raise RuntimeError(
            "La empresa configurada actualmente en RackNova Local "
            "no es válida."
        ) from exc

    # Protección crítica:
    # nunca cambiamos silenciosamente el tenant de una base existente.
    if current_empresa != empresa_id:
        raise RuntimeError(
            "RackNova Local pertenece actualmente a la empresa "
            f"{current_empresa}, pero intentaste activarlo para "
            f"{empresa_id}. No realizaré el cambio automáticamente."
        )

    required_local_secrets = (
        "db_password",
        "pg_super_password",
        "jwt_secret",
    )

    missing = [
        key
        for key in required_local_secrets
        if not str(current_secrets.get(key) or "").strip()
    ]

    if missing:
        raise RuntimeError(
            "Faltan secretos locales protegidos: "
            + ", ".join(missing)
        )

    raw_code = str(
        node_code
        or current_config.get("node_code")
        or f"LOCAL-{socket.gethostname().upper()}"
    ).strip().upper()

    clean_code = "".join(
        ch if (ch.isalnum() or ch in "-_") else "-"
        for ch in raw_code
    ).strip("-_")[:120]

    if not clean_code:
        raise RuntimeError("node_code inválido.")

    clean_name = str(
        node_name
        or current_config.get("node_name")
        or f"RackNova Local - {socket.gethostname()}"
    ).strip()[:180]

    # --------------------------------------------------------
    # PASO 1:
    # validar Cloud + secreto + empresa ANTES de guardar nada.
    # Este endpoint además registra/upsertea el nodo remotamente.
    # --------------------------------------------------------

    remote = _cloud_json(
        method="POST",
        url=cloud_url + "/sync/v1/nodes/register",
        secret=sync_secret,
        payload={
            "empresa_id": empresa_id,
            "node_code": clean_code,
            "node_name": clean_name,
            "node_type": "LOCAL_SERVER",
            "app_version": str(
                current_config.get("app_version")
                or "native-f1"
            ),
        },
        timeout=30,
    )

    if not bool(remote.get("ok", False)):
        raise RuntimeError(
            "RackNova Cloud no confirmó el registro del nodo."
        )

    # --------------------------------------------------------
    # PASO 2:
    # Cloud validado. Ahora sí persistimos configuración.
    # --------------------------------------------------------

    new_secrets = dict(current_secrets)
    new_secrets["node_credential"] = sync_secret

    new_config = dict(current_config)
    new_config.update(
        {
            "activated": True,
            "empresa_id": empresa_id,
            "node_code": clean_code,
            "node_name": clean_name,
            "cloud_url": cloud_url,
            "sync_interval_seconds": int(sync_interval),
            "activated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }
    )

    cfg_path = config_path()
    sec_path = secrets_path()

    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    backup_dir = cfg_path.parent / "ActivationBackups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if cfg_path.exists():
        shutil.copy2(
            cfg_path,
            backup_dir / f"config-{stamp}.json",
        )

    if sec_path.exists():
        shutil.copy2(
            sec_path,
            backup_dir / f"secrets-{stamp}.dat",
        )

    original_config = (
        cfg_path.read_bytes()
        if cfg_path.exists()
        else None
    )

    original_secrets = (
        sec_path.read_bytes()
        if sec_path.exists()
        else None
    )

    tmp_config = Path(str(cfg_path) + ".tmp")

    try:
        save_secret_json(
            sec_path,
            new_secrets,
        )

        tmp_config.write_text(
            _json_dump(new_config) + "\n",
            encoding="utf-8",
        )

        tmp_config.replace(cfg_path)

    except Exception:
        try:
            if original_secrets is None:
                sec_path.unlink(missing_ok=True)
            else:
                sec_path.write_bytes(original_secrets)
        except Exception:
            pass

        try:
            if original_config is None:
                cfg_path.unlink(missing_ok=True)
            else:
                cfg_path.write_bytes(original_config)
        except Exception:
            pass

        tmp_config.unlink(missing_ok=True)
        raise

    return {
        "ok": True,
        "activated": True,
        "empresa_id": empresa_id,
        "cloud_url": cloud_url,
        "node_code": clean_code,
        "node_name": clean_name,
        "sync_interval_seconds": int(sync_interval),
        "node_credential_configured": True,
        "remote_node": remote.get("node"),
        "restart_required": True,
        "message": (
            "RackNova Local quedó vinculado a Cloud. "
            "Reinicia RackNovaLocal para iniciar RackNova Sync."
        ),
    }

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

    activate = sub.add_parser(
        "activate-cloud",
        help="Vincular RackNova Local con RackNova Cloud.",
    )
    activate.add_argument("--cloud-url", required=True)
    activate.add_argument("--empresa-id", required=True)
    activate.add_argument("--sync-secret")
    activate.add_argument("--node-code")
    activate.add_argument("--node-name")
    activate.add_argument(
        "--sync-interval",
        type=int,
        default=15,
    )

    sub.add_parser("activation-status")
    sub.add_parser("init-schema")
    sub.add_parser("health")
    sub.add_parser("diagnose")

    args = parser.parse_args()

    if args.command == "bootstrap-secrets":
        bootstrap_secrets(Path(args.file))
        print("OK: secretos protegidos con DPAPI.")
        return 0

    if args.command == "activation-status":
        print(_json_dump(activation_status()))
        return 0

    if args.command == "activate-cloud":
        secret = str(args.sync_secret or "").strip()

        if not secret:
            secret = getpass.getpass(
                "RACKNOVA_SYNC_SECRET: "
            ).strip()

        result = activate_cloud(
            cloud_url=args.cloud_url,
            empresa_id=args.empresa_id,
            sync_secret=secret,
            node_code=args.node_code,
            node_name=args.node_name,
            sync_interval=args.sync_interval,
        )

        print(_json_dump(result))
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
