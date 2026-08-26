from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes
from pathlib import Path
from typing import Any

CRYPTPROTECT_LOCAL_MACHINE = 0x4


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def protect_bytes(data: bytes) -> bytes:
    if not data:
        return b""

    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "RackNova Local",
        None,
        None,
        None,
        CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(out_blob),
    )
    _ = in_buffer

    if not ok:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect_bytes(data: bytes) -> bytes:
    if not data:
        return b""

    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    _ = in_buffer

    if not ok:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def save_secret_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encrypted = protect_bytes(raw)
    path.write_text(
        base64.b64encode(encrypted).decode("ascii"),
        encoding="ascii",
    )


def load_secret_json(path: Path) -> dict[str, Any]:
    encoded = path.read_text(encoding="ascii").strip()
    encrypted = base64.b64decode(encoded)
    raw = unprotect_bytes(encrypted)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("El almacén DPAPI no contiene un objeto JSON.")
    return value
