"""
Modulo de auditoria — envia eventos de ejecucion a Vercel, y Vercel escribe en Neon.

REGLAS DE PRIVACIDAD:
- NUNCA enviar contenido de polizas, imagenes, nombres, codigos de cliente,
  montos, ni JSON extraido.
- Solo metadatos de ejecucion: usuario Windows, equipo, version, evento,
  tokens, duracion, exito/error y banderas no sensibles.
- Si Vercel/Neon falla, el evento se descarta silenciosamente.
- La auditoria JAMAS debe romper, congelar ni ralentizar el flujo principal.
"""

import json
import os
import queue
import socket
import threading
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta


_ENDPOINT_URL: str | None = None
_API_KEY: str | None = None
_APP_VERSION: str = "unknown"
_USER_ID: str = ""
_HOSTNAME: str = ""

_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=200)
_WORKER_STARTED = False
_LOCK = threading.Lock()

LIMA_TZ = timezone(timedelta(hours=-5), name="PET")


def _detect_user() -> str:
    return (
        os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "unknown"
    )[:120]


def _detect_hostname() -> str:
    try:
        h = socket.gethostname()
    except Exception:
        h = os.environ.get("COMPUTERNAME") or "unknown"

    return (h or "unknown")[:120]


def _now_lima_naive_iso() -> str:
    return datetime.now(LIMA_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def init(endpoint_url: str | None, api_key: str | None, app_version: str = "1.0.0"):
    global _ENDPOINT_URL, _API_KEY, _APP_VERSION, _USER_ID, _HOSTNAME, _WORKER_STARTED

    with _LOCK:
        endpoint_url = (endpoint_url or "").strip()
        api_key = (api_key or "").strip()

        if endpoint_url.startswith("https://") and api_key:
            _ENDPOINT_URL = endpoint_url
            _API_KEY = api_key
        else:
            _ENDPOINT_URL = None
            _API_KEY = None

        _APP_VERSION = (app_version or "unknown")[:40]
        _USER_ID = _detect_user()
        _HOSTNAME = _detect_hostname()

        if _ENDPOINT_URL and _API_KEY and not _WORKER_STARTED:
            t = threading.Thread(
                target=_worker,
                daemon=True,
                name="audit-http-worker",
            )
            t.start()
            _WORKER_STARTED = True


def log(
    event_type: str,
    success: bool | None = None,
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    metadata: dict | None = None,
):
    if not _ENDPOINT_URL or not _API_KEY:
        return

    try:
        evt = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": _now_lima_naive_iso(),
            "user_identifier": _USER_ID,
            "hostname": _HOSTNAME,
            "app_version": _APP_VERSION,
            "event_type": (event_type or "unknown")[:40],
            "success": success if isinstance(success, bool) else None,
            "model_name": (model_name[:80] if model_name else None),
            "input_tokens": int(input_tokens) if isinstance(input_tokens, int) else None,
            "output_tokens": int(output_tokens) if isinstance(output_tokens, int) else None,
            "duration_ms": int(duration_ms) if isinstance(duration_ms, int) else None,
            "metadata": _sanitize_metadata(metadata),
        }

        _QUEUE.put_nowait(evt)

    except queue.Full:
        pass
    except Exception:
        pass


def _sanitize_metadata(md: dict | None) -> dict | None:
    if not md or not isinstance(md, dict):
        return None

    clean = {}

    for k, v in list(md.items())[:20]:
        if not isinstance(k, str):
            continue

        safe_k = k.strip()[:50]
        if not safe_k:
            continue

        if isinstance(v, bool):
            clean[safe_k] = v
        elif isinstance(v, int):
            clean[safe_k] = v
        elif isinstance(v, float):
            clean[safe_k] = v
        elif isinstance(v, str):
            clean[safe_k] = v.strip()[:120]

    return clean or None


def _post_event(evt: dict):
    try:
        body = json.dumps(evt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        req = urllib.request.Request(
            _ENDPOINT_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": _API_KEY,
                "User-Agent": "polizas-capture-audit/1.0",
            },
        )

        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    except Exception:
        pass


def _worker():
    while True:
        try:
            evt = _QUEUE.get(timeout=30)
        except queue.Empty:
            continue

        try:
            _post_event(evt)
        finally:
            try:
                _QUEUE.task_done()
            except Exception:
                pass