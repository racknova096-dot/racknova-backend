from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from pydantic import BaseModel, Field


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
AI_RELAY_PATH = "/sync/v1/ai/complete"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_CONTEXT_CHARS = 60_000


class RackNovaAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=24_000)


class RackNovaAICloudCompletionRequest(BaseModel):
    empresa_id: str = Field(min_length=1, max_length=80)
    origin_node_code: str = Field(min_length=1, max_length=120)
    messages: list[RackNovaAIMessage] = Field(min_length=1, max_length=8)
    max_tokens: int = Field(default=4096, ge=64, le=4096)
    user_id: str = Field(default="racknova_local", min_length=1, max_length=200)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _validated_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    parsed = [RackNovaAIMessage.model_validate(item) for item in list(messages or [])]
    total_chars = sum(len(item.content) for item in parsed)
    if total_chars > MAX_CONTEXT_CHARS:
        raise RuntimeError("El contexto de RackNova IA excede el límite seguro.")
    return [item.model_dump() for item in parsed]


def _decode_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception:
        return str(exc)


def request_deepseek_from_cloud(
    *,
    messages: list[dict[str, Any]],
    max_tokens: int,
    user_id: str,
) -> dict[str, Any]:
    """Ejecuta DeepSeek únicamente desde RackNova Cloud.

    La clave permanece en el servidor Cloud; los nodos Local nunca la reciben.
    """
    api_key = _env("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("RackNova Cloud no tiene DEEPSEEK_API_KEY configurado.")

    model = _env("DEEPSEEK_MODEL", DEFAULT_MODEL)
    safe_messages = _validated_messages(messages)
    payload = {
        "model": model,
        "messages": safe_messages,
        "max_tokens": max(64, min(int(max_tokens or 4096), 4096)),
        "temperature": 0.1,
        "stream": False,
        "user_id": str(user_id or "racknova")[:200],
    }

    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=_json_dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "RackNova-Cloud-AI/1",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = _decode_http_error(exc)
        lower = detail.lower()
        if exc.code == 402 or "insufficient balance" in lower or "balance" in lower:
            raise RuntimeError(
                "RackNova IA está temporalmente sin saldo disponible en el proveedor de IA."
            ) from exc
        raise RuntimeError(
            f"Proveedor de IA respondió HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"RackNova Cloud no pudo contactar al proveedor de IA: {exc.reason}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Error consultando el proveedor de IA: {exc}") from exc

    choices = list(data.get("choices") or [])
    if not choices:
        raise RuntimeError("El proveedor de IA no devolvió una respuesta utilizable.")

    first = choices[0] or {}
    message = first.get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("El proveedor de IA devolvió contenido vacío.")

    return {
        "content": content,
        "finish_reason": str(first.get("finish_reason") or "stop"),
        "usage": data.get("usage") or {},
        "model": str(data.get("model") or model),
        "relay": "racknova_cloud",
    }


def request_deepseek_via_racknova_cloud(
    *,
    messages: list[dict[str, Any]],
    max_tokens: int,
    user_id: str,
) -> dict[str, Any]:
    """Cliente usado por RackNova Local cuando hay Internet.

    Envía únicamente el prompt/contexto compacto construido por ia_copilot.py.
    Nunca envía la base completa ni necesita la API key de DeepSeek en la PC.
    """
    cloud_url = _env("RACKNOVA_CLOUD_URL").rstrip("/")
    secret = _env("RACKNOVA_SYNC_SECRET")
    empresa_id = _env("RACKNOVA_EMPRESA_ID")
    node_code = _env("RACKNOVA_NODE_CODE")

    if not cloud_url:
        raise RuntimeError("RackNova Local no tiene RACKNOVA_CLOUD_URL configurado.")
    if not secret:
        raise RuntimeError("RackNova Local no tiene credencial de nodo para usar RackNova IA.")
    if not empresa_id or not node_code:
        raise RuntimeError("RackNova Local no tiene identidad de empresa/nodo completa.")

    safe_messages = _validated_messages(messages)
    body = RackNovaAICloudCompletionRequest(
        empresa_id=empresa_id,
        origin_node_code=node_code,
        messages=[RackNovaAIMessage(**item) for item in safe_messages],
        max_tokens=max_tokens,
        user_id=str(user_id or "racknova_local"),
    ).model_dump()

    request = urllib.request.Request(
        f"{cloud_url}{AI_RELAY_PATH}",
        data=_json_dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-RackNova-Sync-Secret": secret,
            "User-Agent": "RackNova-Local-AI/1",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = _decode_http_error(exc)
        if exc.code in {401, 403}:
            raise RuntimeError(
                "RackNova Cloud no aceptó la credencial de este nodo para usar la IA."
            ) from exc
        raise RuntimeError(
            f"RackNova IA Cloud respondió HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "RackNova IA necesita conexión a Internet para los análisis avanzados. "
            f"No se pudo contactar RackNova Cloud: {exc.reason}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo contactar RackNova Cloud para usar la IA: {exc}"
        ) from exc

    if not isinstance(data, dict) or not str(data.get("content") or "").strip():
        raise RuntimeError("RackNova Cloud devolvió una respuesta de IA inválida.")
    return data