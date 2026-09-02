from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from racknova_native_config import program_data

DEFAULT_MANIFEST_URL = (
    "https://racknova-dashboard.vercel.app/native-dashboard/manifest.json"
)
DEFAULT_INTERVAL_SECONDS = 900
DEFAULT_INITIAL_DELAY_SECONDS = 15
MAX_FILES = 500
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_FILE_BYTES = 40 * 1024 * 1024
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def dashboard_root() -> Path:
    path = program_data() / "Dashboard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_dir() -> Path:
    return dashboard_root() / "current"


def previous_dir() -> Path:
    return dashboard_root() / "previous"


def status_path() -> Path:
    return dashboard_root() / "status.json"


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def manifest_url() -> str:
    return str(
        os.getenv("RACKNOVA_DASHBOARD_MANIFEST_URL", DEFAULT_MANIFEST_URL)
        or DEFAULT_MANIFEST_URL
    ).strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_status(**updates: Any) -> dict[str, Any]:
    path = status_path()
    current = _read_json(path)
    current.update(updates)
    current["updated_at_epoch"] = time.time()
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)
    return current


def dashboard_update_status() -> dict[str, Any]:
    status = _read_json(status_path())
    status.setdefault("enabled", _bool_env("RACKNOVA_DASHBOARD_AUTOUPDATE", True))
    status.setdefault("manifest_url", manifest_url())
    status["current_available"] = _valid_dashboard_dir(current_dir())
    status["previous_available"] = _valid_dashboard_dir(previous_dir())
    return status


def _valid_dashboard_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "index.html").is_file()
        and (path / ".racknova-manifest.json").is_file()
    )


def active_dashboard_dir(embedded: Path) -> tuple[Path, str]:
    current = current_dir()
    if _valid_dashboard_dir(current):
        return current, "downloaded_current"

    previous = previous_dir()
    if _valid_dashboard_dir(previous):
        return previous, "downloaded_previous"

    return embedded, "embedded"


def _safe_relative_path(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").strip("/")
    if not raw or raw.startswith("."):
        raise ValueError(f"Ruta de Dashboard inválida: {value!r}")

    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Ruta de Dashboard inválida: {value!r}")
    if any(":" in part for part in parts):
        raise ValueError(f"Ruta de Dashboard inválida: {value!r}")
    return "/".join(parts)


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Manifest del Dashboard inválido.")
    if int(value.get("schema_version") or 0) != 1:
        raise ValueError("Versión de manifest del Dashboard no soportada.")
    if value.get("product") != "racknova-dashboard-native":
        raise ValueError("El manifest no pertenece a RackNova Dashboard Native.")
    if value.get("base_path") != "/ui/":
        raise ValueError("El Dashboard remoto no fue compilado para /ui/.")
    if value.get("api_mode") != "same-origin":
        raise ValueError("El Dashboard remoto no usa API local same-origin.")

    version = str(value.get("version") or "").strip()
    if not version or len(version) > 200:
        raise ValueError("Versión remota del Dashboard inválida.")

    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("El manifest no contiene archivos.")
    if len(raw_files) > MAX_FILES:
        raise ValueError("El manifest contiene demasiados archivos.")

    files: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("Entrada de archivo inválida en manifest.")
        relative = _safe_relative_path(item.get("path"))
        if relative in seen:
            raise ValueError(f"Archivo duplicado en manifest: {relative}")
        seen.add(relative)

        sha256 = str(item.get("sha256") or "").strip().lower()
        if not HEX_SHA256.fullmatch(sha256):
            raise ValueError(f"SHA-256 inválido para {relative}.")

        try:
            size = int(item.get("size"))
        except Exception as exc:
            raise ValueError(f"Tamaño inválido para {relative}.") from exc
        if size < 0 or size > MAX_FILE_BYTES:
            raise ValueError(f"Tamaño no permitido para {relative}.")

        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Dashboard remoto excede el tamaño máximo permitido.")

        files.append({"path": relative, "sha256": sha256, "size": size})

    if "index.html" not in seen:
        raise ValueError("El Dashboard remoto no contiene index.html.")

    clean = dict(value)
    clean["version"] = version
    clean["files"] = files
    return clean


def _fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "RackNova-Native-Dashboard-Updater/1",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_TOTAL_BYTES + 1)
    if len(raw) > MAX_TOTAL_BYTES:
        raise ValueError("Respuesta de manifest demasiado grande.")
    return json.loads(raw.decode("utf-8"))


def _download_file(url: str, target: Path, expected_size: int, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "User-Agent": "RackNova-Native-Dashboard-Updater/1",
        },
        method="GET",
    )

    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=20) as response, target.open("wb") as fh:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_FILE_BYTES:
                raise ValueError("Archivo remoto excedió el tamaño máximo permitido.")
            digest.update(chunk)
            fh.write(chunk)

    if received != expected_size:
        raise ValueError(
            f"Tamaño inválido para {target.name}: esperado={expected_size}, recibido={received}."
        )
    actual = digest.hexdigest().lower()
    if actual != expected_sha256:
        raise ValueError(
            f"SHA-256 inválido para {target.name}: esperado={expected_sha256}, recibido={actual}."
        )


def _remote_file_url(manifest_location: str, relative: str) -> str:
    base = manifest_location.rsplit("/", 1)[0] + "/files/"
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in relative.split("/"))
    return urllib.parse.urljoin(base, encoded)


def _installed_manifest() -> dict[str, Any]:
    for folder in (current_dir(), previous_dir()):
        if _valid_dashboard_dir(folder):
            value = _read_json(folder / ".racknova-manifest.json")
            if value:
                return value
    return {}


def _activate_staging(staging: Path) -> None:
    current = current_dir()
    previous = previous_dir()

    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)

    moved_current = False
    try:
        if current.exists():
            current.replace(previous)
            moved_current = True
        staging.replace(current)
    except Exception:
        if moved_current and not current.exists() and previous.exists():
            previous.replace(current)
        raise


def check_for_dashboard_update() -> dict[str, Any]:
    if not _bool_env("RACKNOVA_DASHBOARD_AUTOUPDATE", True):
        return _write_status(
            enabled=False,
            state="disabled",
            manifest_url=manifest_url(),
        )

    url = manifest_url()
    _write_status(
        enabled=True,
        state="checking",
        manifest_url=url,
        last_check_epoch=time.time(),
        last_error=None,
    )

    staging: Path | None = None
    try:
        manifest = _validate_manifest(_fetch_json(url))
        installed = _installed_manifest()
        installed_version = str(installed.get("version") or "")
        remote_version = str(manifest["version"])

        if installed_version and installed_version == remote_version and _valid_dashboard_dir(current_dir()):
            return _write_status(
                state="current",
                installed_version=installed_version,
                remote_version=remote_version,
                last_success_epoch=time.time(),
                last_error=None,
            )

        staging = dashboard_root() / f"staging-{uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)

        for item in manifest["files"]:
            relative = str(item["path"])
            target = staging.joinpath(*relative.split("/"))
            _download_file(
                _remote_file_url(url, relative),
                target,
                int(item["size"]),
                str(item["sha256"]),
            )

        (staging / ".racknova-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

        if not (staging / "index.html").is_file():
            raise ValueError("La descarga verificada no contiene index.html.")

        _activate_staging(staging)
        staging = None

        return _write_status(
            state="updated",
            installed_version=remote_version,
            remote_version=remote_version,
            source_commit=manifest.get("source_commit"),
            last_success_epoch=time.time(),
            last_error=None,
        )
    except Exception as exc:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return _write_status(
            state="error",
            last_error=str(exc)[:2000],
            last_error_epoch=time.time(),
        )


def start_dashboard_update_worker() -> bool:
    global _WORKER_STARTED

    if not _bool_env("RACKNOVA_DASHBOARD_AUTOUPDATE", True):
        _write_status(
            enabled=False,
            state="disabled",
            manifest_url=manifest_url(),
        )
        return False

    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        _WORKER_STARTED = True

        initial_delay = _int_env(
            "RACKNOVA_DASHBOARD_UPDATE_INITIAL_DELAY_SECONDS",
            DEFAULT_INITIAL_DELAY_SECONDS,
            5,
            600,
        )
        interval = _int_env(
            "RACKNOVA_DASHBOARD_UPDATE_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
            60,
            86400,
        )

        def loop() -> None:
            time.sleep(initial_delay)
            while True:
                try:
                    check_for_dashboard_update()
                except Exception as exc:
                    _write_status(state="error", last_error=str(exc)[:2000])
                time.sleep(interval)

        thread = threading.Thread(
            target=loop,
            name="racknova-dashboard-updater",
            daemon=True,
        )
        thread.start()
        return True
