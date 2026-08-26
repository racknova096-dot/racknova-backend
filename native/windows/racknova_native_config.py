from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from racknova_secrets import load_secret_json

DEFAULT_EMPRESA_ID = "11111111-1111-4111-8111-111111111111"
DEFAULT_PORT = 8000
DEFAULT_DB_PORT = 54329


def program_data() -> Path:
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    return base / "RackNova"


def config_path() -> Path:
    return program_data() / "Config" / "config.json"


def secrets_path() -> Path:
    return program_data() / "Config" / "secrets.dat"


def logs_dir() -> Path:
    path = program_data() / "Logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def diagnostics_dir() -> Path:
    path = program_data() / "Diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = program_data() / "Backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config.json inválido.")
    return value


def load_secrets() -> dict[str, Any]:
    path = secrets_path()
    if not path.exists():
        return {}
    return load_secret_json(path)


def database_url(
    config: dict[str, Any],
    secrets: dict[str, Any],
) -> str:
    host = str(config.get("db_host") or "127.0.0.1")
    port = int(config.get("db_port") or DEFAULT_DB_PORT)
    name = str(config.get("db_name") or "racknova")
    user = str(config.get("db_user") or "racknova_app")
    password = str(secrets.get("db_password") or "")
    if not password:
        raise RuntimeError("RackNova Local no tiene db_password configurado.")

    return (
        "postgresql+psycopg2://"
        f"{quote_plus(user)}:{quote_plus(password)}@"
        f"{host}:{port}/{quote_plus(name)}?sslmode=disable"
    )


def apply_native_environment() -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_config()
    secrets = load_secrets()

    os.environ["RACKNOVA_MODE"] = "local"
    os.environ["DATABASE_URL"] = database_url(config, secrets)
    os.environ["RACKNOVA_EMPRESA_ID"] = str(
        config.get("empresa_id") or DEFAULT_EMPRESA_ID
    )
    os.environ["RACKNOVA_NODE_CODE"] = str(
        config.get("node_code") or "LOCAL-WINDOWS"
    )
    os.environ["RACKNOVA_NODE_NAME"] = str(
        config.get("node_name") or "RackNova Local Windows"
    )
    os.environ["RACKNOVA_NODE_TYPE"] = "LOCAL_SERVER"
    os.environ["RACKNOVA_APP_VERSION"] = str(
        config.get("app_version") or "native-f1"
    )

    jwt_secret = str(secrets.get("jwt_secret") or "").strip()
    if not jwt_secret:
        raise RuntimeError(
            "RackNova Local no tiene SECRET_KEY local protegida."
        )
    os.environ["SECRET_KEY"] = jwt_secret

    jwt_secret = str(secrets.get("jwt_secret") or "").strip()
    if not jwt_secret:
        raise RuntimeError(
            "RackNova Local no tiene SECRET_KEY local protegida."
        )
    os.environ["SECRET_KEY"] = jwt_secret

    cloud_url = str(config.get("cloud_url") or "").strip().rstrip("/")
    if cloud_url:
        os.environ["RACKNOVA_CLOUD_URL"] = cloud_url
    else:
        os.environ.pop("RACKNOVA_CLOUD_URL", None)

    credential = str(secrets.get("node_credential") or "").strip()
    if credential:
        # Compatibilidad temporal de F1 con B3 actual.
        # F2 la sustituirá por credencial única de nodo validada en Cloud.
        os.environ["RACKNOVA_SYNC_SECRET"] = credential
        os.environ["RACKNOVA_SYNC_AUTOSTART"] = "true"
    else:
        os.environ.pop("RACKNOVA_SYNC_SECRET", None)
        os.environ["RACKNOVA_SYNC_AUTOSTART"] = "false"

    os.environ["RACKNOVA_SYNC_INTERVAL_SECONDS"] = str(
        int(config.get("sync_interval_seconds") or 15)
    )

    return config, secrets
