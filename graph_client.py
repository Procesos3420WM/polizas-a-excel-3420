"""
graph_client.py — Lee la lista de asesores y sedes desde SharePoint via Microsoft Graph API.

Credenciales idénticas al proyecto tesorería 2.
Token cacheado con DPAPI en %LOCALAPPDATA%\\PolizasAExcel3420\\token_cache.bin
Datos cacheados en %LOCALAPPDATA%\\PolizasAExcel3420\\asesores_cache.json
(fallback offline si no hay red o el token expiró)
"""

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

import msal
from msal_extensions import FilePersistenceWithDataProtection, PersistedTokenCache


# ── Azure AD ───────────────────────────────────────────────────────────────────
TENANT_ID  = "be804c86-6283-4082-8893-c84ee4c9473e"
CLIENT_ID  = "ad20afa7-9e57-4563-8faf-ad8cf7e2f0c9"
AUTHORITY  = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES     = ["Files.ReadWrite.All"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# ── Archivo SharePoint a consultar ─────────────────────────────────────────────
SP_FILE_URL = (
    "https://3420wm.sharepoint.com/:x:/r/sites/"
    "AutomatizacionesparaContabilidad-Procesos3420WM"
    "/_layouts/15/Doc.aspx"
    "?sourcedoc=%7B108C4A27-F520-4AED-A22A-B6E1ACE89BA4%7D"
    "&file=FORMATO%20FONDOS%20PUBLICOS%203420%20-%20Copia.xlsx"
    "&action=default&mobileredirect=true"
)

# Hoja y columnas: A = asesor, C = departamento (sede)
# Rango amplio — se filtran las filas vacías por código
ASESORES_SHEET = "LISTA OFICIAL"
ASESORES_RANGE = "A2:C500"

# ── Rutas locales ──────────────────────────────────────────────────────────────
_LOCAL_DIR      = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PolizasAExcel3420"
_CACHE_TOKEN    = _LOCAL_DIR / "token_cache.bin"
_CACHE_ASESORES = _LOCAL_DIR / "asesores_cache.json"


def _ensure_dirs():
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)


# ── Auth ───────────────────────────────────────────────────────────────────────
def _build_cache() -> PersistedTokenCache:
    _ensure_dirs()
    persistence = FilePersistenceWithDataProtection(str(_CACHE_TOKEN))
    return PersistedTokenCache(persistence)


def get_token() -> str:
    """Devuelve un access token válido. Abre el navegador solo si no hay caché."""
    cache = _build_cache()
    app   = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # Sin caché: login interactivo (abre el navegador)
    result = app.acquire_token_interactive(SCOPES, prompt="select_account")
    if "access_token" not in result:
        raise RuntimeError(
            f"Error de autenticación: {result.get('error_description', result)}"
        )
    return result["access_token"]


# ── Graph helpers ──────────────────────────────────────────────────────────────
def _headers(token: str, session_id: str = None) -> dict:
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    if session_id:
        h["workbook-session-id"] = session_id
    return h


def _graph_get(token: str, path: str, session_id: str = None) -> dict:
    url = f"{GRAPH_BASE}{path}"
    req = urllib.request.Request(url, headers=_headers(token, session_id))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} GET {path}: {body}") from e


def _graph_post(token: str, path: str, body: dict, session_id: str = None) -> dict:
    url  = f"{GRAPH_BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=_headers(token, session_id), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} POST {path}: {body_err}") from e


def _encode_share_url(url: str) -> str:
    """Codifica una URL de SharePoint para usarla en /shares/{id}/driveItem."""
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"u!{encoded}"


# ── API pública ────────────────────────────────────────────────────────────────
def get_lista_oficial() -> dict:
    """
    Lee columnas A (asesor) y C (departamento/sede) de la hoja LISTA OFICIAL.

    Devuelve:
        {
            "asesores": ["NOMBRE1", "NOMBRE2", ...],   # lista para el Combobox
            "sedes":    {"NOMBRE1": "LIMA", ...}        # mapa asesor → sede
        }

    Rango dinámico: lee A2:C500 y filtra filas donde columna A está vacía.

    Si cualquier paso falla → devuelve el caché local (o estructura vacía).
    """
    try:
        token = get_token()

        # Resolver el archivo via share URL
        share_id = _encode_share_url(SP_FILE_URL)
        item     = _graph_get(token, f"/shares/{share_id}/driveItem")

        drive_id = item["parentReference"]["driveId"]
        item_id  = item["id"]

        # Leer columnas A y C de LISTA OFICIAL
        sheet = quote(ASESORES_SHEET)
        data  = _graph_get(
            token,
            f"/drives/{drive_id}/items/{item_id}"
            f"/workbook/worksheets/{sheet}/range(address='{ASESORES_RANGE}')"
        )

        valores = data.get("values", [])

        asesores = []
        sedes    = {}

        for row in valores:
            # Cada fila tiene al menos 3 columnas (A, B, C)
            # col A = asesor, col C = departamento/sede
            nombre = str(row[0]).strip().upper() if len(row) > 0 else ""
            sede   = str(row[2]).strip().upper() if len(row) > 2 else ""

            if not nombre or nombre in ("NONE", ""):
                continue

            asesores.append(nombre)
            if sede and sede != "NONE":
                sedes[nombre] = sede

        resultado = {"asesores": asesores, "sedes": sedes}

        if asesores:
            _save_cache(resultado)

        return resultado

    except Exception:
        return _load_cache()


# ── SharePoint write ───────────────────────────────────────────────────────────
FICOS_SHEET = "FICOS"

# Formula templates — {row} is replaced with the actual Excel row number
_F_FUNC = '=IFERROR(VLOOKUP(A{row},\'LISTA OFICIAL\'!$A$2:$B$500,2,FALSE),"")'
_F_SEDE = '=IFERROR(VLOOKUP(A{row},\'LISTA OFICIAL\'!$A$2:$D$500,3,FALSE),"")'
_F_MON  = '=IF(LEFT(TRIM(M{row}),4)="ICMP","USD",IF(LEFT(TRIM(M{row}),5)="PRIME","PEN",IF(LEFT(TRIM(M{row}),2)="IF","USD","")))'

# Module-level cache for resolved drive/item IDs (avoids re-resolving on every write)
_CACHED_DRIVE_ID: "str | None" = None
_CACHED_ITEM_ID:  "str | None" = None


def _resolve_file_cached(token: str) -> tuple:
    global _CACHED_DRIVE_ID, _CACHED_ITEM_ID
    if _CACHED_DRIVE_ID and _CACHED_ITEM_ID:
        return _CACHED_DRIVE_ID, _CACHED_ITEM_ID
    share_id = _encode_share_url(SP_FILE_URL)
    item = _graph_get(token, f"/shares/{share_id}/driveItem")
    _CACHED_DRIVE_ID = item["parentReference"]["driveId"]
    _CACHED_ITEM_ID  = item["id"]
    return _CACHED_DRIVE_ID, _CACHED_ITEM_ID


def _graph_patch(token: str, path: str, body: dict, session_id: str = None) -> dict:
    url  = f"{GRAPH_BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=_headers(token, session_id), method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} PATCH {path}: {body_err}") from e


def write_row_to_sp(json_data: dict, cliente: dict) -> dict:
    """
    Escribe una fila de 15 columnas (A→O) en la siguiente fila vacía de FICOS.

    Retorna {"ok": True, "fila": N} o {"ok": False, "error": "mensaje"}.
    """
    sid = None
    try:
        token = get_token()
        drive_id, item_id = _resolve_file_cached(token)

        # Abrir sesión persistente (igual que tesorería 2)
        sess = _graph_post(token,
                           f"/drives/{drive_id}/items/{item_id}/workbook/createSession",
                           {"persistChanges": True})
        sid = sess.get("id")

        sheet = quote(FICOS_SHEET)

        # Siguiente fila vacía — lee columna A para encontrar la última celda con datos
        # (usedRange incluye filas con formato vacías y devuelve un número incorrecto)
        col_a = _graph_get(token,
                           f"/drives/{drive_id}/items/{item_id}"
                           f"/workbook/worksheets/{sheet}/range(address='A1:A3000')",
                           session_id=sid)
        col_values = col_a.get("values", [])
        last_row = 0
        for i, row in enumerate(col_values):
            cell = str(row[0]).strip() if row and row[0] not in (None, "", "None") else ""
            if cell:
                last_row = i + 1  # 1-indexed
        next_row = last_row + 1

        # Datos de la póliza
        tipo      = json_data.get("tipo", "COMPRA")
        es_venta  = tipo == "VENTA"
        fondo     = (json_data.get("ticker") or "").upper()
        cantidad  = json_data.get("cantidad")
        monto_inv = None if es_venta else json_data.get("monto_compra")
        monto_vta = json_data.get("monto_venta") if es_venta else None

        fecha     = datetime.now(timezone(timedelta(hours=-5))).strftime("%d/%m/%Y")
        asesor    = (cliente.get("asesor")         or "").upper()
        asistente = (cliente.get("asistente")      or "").upper()
        nombre    = (cliente.get("nombre_cliente") or "").upper()
        sab       = cliente.get("codigo_sab") or ""
        saf       = cliente.get("codigo_saf") or ""
        plazo     = cliente.get("plazo")      or ""
        tasa      = cliente.get("tasa")       or ""

        n = next_row

        row_values = [
            asesor,                                       # A  ASESOR
            _F_FUNC.format(row=n),                        # B  FUNCIONARIO
            _F_SEDE.format(row=n),                        # C  SEDE
            asistente,                                    # D  ASISTENTE
            fecha,                                        # E  FECHA
            sab,                                          # F  CÓDIGO SAB
            saf,                                          # G  CÓDIGO SAF
            nombre,                                       # H  NOMBRE DEL CLIENTE
            cantidad if cantidad is not None else "",     # I  NÚMERO DE CUOTAS
            monto_inv if monto_inv is not None else "",   # J  MONTO DE INVERSIÓN
            monto_vta if monto_vta is not None else "",   # K  MONTO VENTA
            _F_MON.format(row=n),                         # L  MONEDA
            fondo,                                        # M  FONDO
            plazo,                                        # N  PLAZO
            tasa,                                         # O  TASA
        ]

        _graph_patch(
            token,
            f"/drives/{drive_id}/items/{item_id}"
            f"/workbook/worksheets/{sheet}/range(address='A{n}:O{n}')",
            {"formulas": [row_values]},
            session_id=sid,
        )

        # Cerrar sesión
        try:
            _graph_post(token,
                        f"/drives/{drive_id}/items/{item_id}/workbook/closeSession",
                        {}, session_id=sid)
        except Exception:
            pass

        return {"ok": True, "fila": n}

    except Exception as e:
        # Intentar cerrar sesión aunque haya fallado
        if sid:
            try:
                token2 = get_token()
                drive_id2, item_id2 = _resolve_file_cached(token2)
                _graph_post(token2,
                            f"/drives/{drive_id2}/items/{item_id2}/workbook/closeSession",
                            {}, session_id=sid)
            except Exception:
                pass
        return {"ok": False, "error": str(e)}


# ── Verificación de acceso ────────────────────────────────────────────────────
def verify_access() -> None:
    """
    Verifica que la cuenta Microsoft autenticada puede leer el archivo SharePoint.
    Lanza excepción si el token no es válido o el usuario no tiene acceso.
    """
    token    = get_token()
    share_id = _encode_share_url(SP_FILE_URL)
    _graph_get(token, f"/shares/{share_id}/driveItem")


# ── Caché offline ──────────────────────────────────────────────────────────────
def _save_cache(data: dict):
    try:
        _ensure_dirs()
        _CACHE_ASESORES.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_cache() -> dict:
    try:
        if _CACHE_ASESORES.exists():
            return json.loads(_CACHE_ASESORES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"asesores": [], "sedes": {}}
