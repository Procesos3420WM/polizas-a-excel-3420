# Contexto de mejoras — Polizas a Excel 3420

Documento de referencia para sesiones futuras de Claude Code.
Cubre todos los cambios significativos implementados desde que se integró la escritura en SharePoint vía Microsoft Graph API.

---

## Descripción general del proyecto

App de escritorio Python + Tkinter compilada con PyInstaller (`Polizas a Excel 3420.exe`).

**Flujo principal:**
1. El usuario copia una imagen de póliza bursátil al portapapeles
2. La app la detecta automáticamente (polling cada 1.5 s)
3. El usuario presiona "Procesar imagen" → Claude (Sonnet) extrae un JSON con los datos
4. Se abre un formulario para completar datos del cliente
5. El usuario confirma → la app escribe una fila en la hoja FICOS del Excel en SharePoint vía Graph API
6. Se registra un evento de auditoría en Neon vía Vercel

**Archivos fuente principales:**

| Archivo | Rol |
|---|---|
| `app.py` | GUI principal (Tkinter), flujo de estados, llamada a Claude |
| `graph_client.py` | Lectura de lista de asesores y escritura en SharePoint vía Graph API |
| `audit.py` | Envío de eventos de auditoría a Vercel/Neon |
| `update_manager.py` | Auto-actualización desde GitHub Releases |
| `updater.py` | Helper ejecutado al instalar una actualización |
| `build.bat` | Compila los dos .exe con PyInstaller y actualiza SHA256 en version.json |
| `version.json` | Manifiesto de versión para el auto-updater |

---

## Graph API — Integración con SharePoint (`graph_client.py`)

### Autenticación
- MSAL `PublicClientApplication` con login interactivo si no hay caché
- Token cacheado con DPAPI en `%LOCALAPPDATA%\PolizasAExcel3420\token_cache.bin`
- Scope: `Files.ReadWrite.All`

### Archivo SharePoint objetivo
- URL resuelta vía `/shares/{encoded_share_url}/driveItem`
- La función `_encode_share_url` convierte la URL de SharePoint a formato `u!<base64>`
- Los IDs de drive e item se cachean en variables de módulo para no resolver en cada escritura

### Lectura de asesores (`get_lista_oficial`)
- Lee hoja `LISTA OFICIAL`, rango `A2:C500`
- Columna A = nombre del asesor, columna C = sede/departamento
- Devuelve `{"asesores": [...], "sedes": {"NOMBRE": "LIMA", ...}}`
- Fallback: caché local en `%LOCALAPPDATA%\PolizasAExcel3420\asesores_cache.json`

### Escritura de fila (`write_row_to_sp`)
Escribe 15 columnas (A→O) en la hoja `FICOS`.

**Detección de siguiente fila libre:**
- NO usar `usedRange` — cuenta filas con formato pero sin datos y da un número incorrecto (dio fila 1685 en vez de 1278 en producción)
- Correcto: leer `A1:A3000` y escanear manualmente la última celda no vacía en columna A

**Sesiones de workbook (obligatorio):**
```python
sess = _graph_post(token, ".../workbook/createSession", {"persistChanges": True})
sid = sess.get("id")
# Pasar session_id en header "workbook-session-id" a todas las operaciones
# Cerrar siempre al final (también en el bloque except)
_graph_post(token, ".../workbook/closeSession", {}, session_id=sid)
```

**Fórmulas — CRÍTICO: deben ser en INGLÉS con comas:**
Graph API rechaza (HTTP 400) las fórmulas en español o con punto y coma.

```python
# CORRECTO
_F_FUNC = '=IFERROR(VLOOKUP(A{row},\'LISTA OFICIAL\'!$A$2:$B$500,2,FALSE),"")'
_F_SEDE = '=IFERROR(VLOOKUP(A{row},\'LISTA OFICIAL\'!$A$2:$D$500,3,FALSE),"")'
_F_MON  = '=IF(LEFT(TRIM(M{row}),4)="ICMP","USD",IF(LEFT(TRIM(M{row}),5)="PRIME","PEN",IF(LEFT(TRIM(M{row}),2)="IF","USD","")))'

# INCORRECTO — da HTTP 400
# =SI.ERROR(BUSCARV(...); "")
# =SI(IZQUIERDA(ESPACIOS(...)))
```

**Columnas escritas (A→O):**

| Col | Contenido |
|---|---|
| A | Asesor (texto directo) |
| B | Fórmula VLOOKUP → Funcionario |
| C | Fórmula VLOOKUP → Sede |
| D | Asistente |
| E | Fecha (DD/MM/YYYY, hora Lima UTC-5) |
| F | Código SAB |
| G | Código SAF |
| H | Nombre del cliente |
| I | Número de cuotas (cantidad) |
| J | Monto de inversión (solo COMPRA) |
| K | Monto venta (solo VENTA) |
| L | Fórmula → Moneda (USD/PEN) |
| M | Fondo/ticker (ej: "ICMP 17") |
| N | Plazo (ej: "26 DÍAS", "1 AÑO") |
| O | Tasa (ej: "4,41%") |

---

## Mejoras en `app.py`

### Estado `_ready_to_send`
Problema: tras confirmar el formulario, el polling detectaba la imagen del portapapeles y sobreescribía el estado, bloqueando el botón "Enviar a SharePoint".

Solución: flag `self._ready_to_send = True` que pausa el polling hasta que se envía o se cancela.

```python
def _poll(self):
    if not self._processing and not self._form_visible and not self._ready_to_send:
        # detectar portapapeles...
```

### Retry de asesores al abrir formulario
Si el login MSAL interactivo ocurre mientras se carga el formulario, la lista de asesores puede llegar vacía. Al mostrar el formulario se reintenta si está vacía:

```python
def _set_awaiting_form(self, tipo):
    ...
    if not self._asesor_combo["values"]:
        threading.Thread(target=self._fetch_asesores, daemon=True).start()
```

### Autocomplete de asesor — implementación actual
Usa `ttk.Combobox` + popup `Toplevel` propio (no el dropdown nativo, que roba el foco vía `grab -global`).

**Por qué NO usar `ttk::combobox::Post`:**  
Hace `grab -global` en el dropdown, robando el foco del Entry. El usuario no puede seguir escribiendo sin hacer clic de nuevo.

**Implementación correcta:**
- `Toplevel` con `wm_overrideredirect(True)` + `wm_attributes("-topmost", True)`
- `Listbox` dentro sin grab → no roba foco
- Se posiciona debajo del combobox con `geometry(f"{w}x{h}+{x}+{y}")`
- `FocusOut` con delay de 150 ms para que el clic en el listbox llegue antes de que se oculte

**Filtro por inicio de palabra (no subcadena):**
```python
# CORRECTO — "br" filtra BRENDA, BRUNO, JUAN BRICEÑO pero NO SABRINA
filtered = [v for v in all_vals
            if any(w.startswith(typed_upper) for w in v.upper().split())]

# INCORRECTO — "br" devolvería también SABRINA
filtered = [v for v in all_vals if typed_upper in v.upper()]
```

**Inline autocomplete:**
- Solo cuando el nombre COMPLETO empieza por lo escrito (para que `icursor(len(typed))` tenga sentido visual)
- Usa `after(0, lambda: ...)` para diferir `icursor` y `selection_range` tras `set()`, porque `set()` mueve el cursor al final y sus callbacks internos sobrescriben la posición si se llama directamente

```python
self._asesor_combo.set(suggestion)
self._asesor_combo.after(0, lambda c=cursor: (
    self._asesor_combo.icursor(c),
    self._asesor_combo.selection_range(c, "end"),
))
```

### Campo Plazo — número + unidad separados
Reemplaza el campo de texto libre por un Entry numérico + Combobox readonly.

- Entry numérico (solo dígitos, `validate="key"`)
- Combobox: `DÍAS / MESES / AÑOS` (default `DÍAS`)
- StringVar computada via `trace_add` con singular automático para 1:
  - `1 DÍA`, `1 MES`, `1 AÑO`
  - `2+ DÍAS`, `2+ MESES`, `2+ AÑOS`
- Al pre-llenar desde Claude: parsear con regex `r'^(\d+)\s+(.+)$'` y normalizar singular→plural para el combobox
- Al limpiar el formulario (tras confirmar): resetear `_plazo_num.set("")` y `_plazo_unit.set("DÍAS")` explícitamente (la StringVar computada no se limpia sola)

### Campo Tasa — normalización de formato
`parse_tasa(raw)` acepta cualquier variante del usuario y normaliza a `"X,XX%"`:

```python
def parse_tasa(raw: str) -> str:
    raw = raw.strip().replace("%", "").replace(",", ".").strip()
    try:
        return f"{float(raw):.2f}".replace(".", ",") + "%"
    except ValueError:
        return ""
```

Binding en el Entry:
- `<FocusOut>` → normaliza al salir del campo
- `<Return>` → normaliza al presionar Enter

Ejemplos: `5` → `5,00%` / `5,0` → `5,00%` / `5,0%` → `5,00%` / `4.41` → `4,41%`

### Versión en el título
```python
APP_VERSION = "1.0.0"
self.title(f"Captura de Pólizas → 3420  v{APP_VERSION}")
```

---

## Build y distribución

### `build.bat`
Tras compilar el exe, calcula SHA256 automáticamente y lo guarda en `version.json`:
```batch
powershell -Command "& { $hash = (Get-FileHash 'dist\Polizas a Excel 3420.exe' -Algorithm SHA256).Hash; $j = Get-Content 'version.json' -Raw | ConvertFrom-Json; $j.sha256 = $hash; ($j | ConvertTo-Json -Depth 5) | Set-Content 'version.json' -Encoding UTF8 }"
```

### GitHub Releases
- Repositorio: `Procesos3420WM/polizas-a-excel-3420`
- GitHub convierte espacios en puntos en los nombres de asset: el exe se llama `Polizas.a.Excel.3420.exe`
- La URL en `version.json` debe usar puntos, no `%20`:
  ```
  https://github.com/Procesos3420WM/polizas-a-excel-3420/releases/download/v1.0.0/Polizas.a.Excel.3420.exe
  ```

---

## Auditoría
- Endpoint: `https://project-legacy-audit.vercel.app/api/audit/polizas3420`
- Tabla Neon: `polizas_3420_audit`
- Eventos registrados: `app_start`, `claude_call`, `sp_write`
- **No modificar** el endpoint ni la tabla — comparten infraestructura con legacy

---

## Seguridad — constraint crítico
El endpoint de auditoría y la tabla Neon son compartidos con el proyecto legacy original.
**Nunca modificar** `AUDIT_ENDPOINT`, `AUDIT_API_KEY`, ni la estructura de la tabla `polizas_3420_audit`.
