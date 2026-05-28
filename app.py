import os
import sys

# ── SSL fix para PyInstaller onefile ──────────────────────────────────────────
# certifi/cacert.pem se extrae al dir temporal; hay que apuntar requests/msal a él.
if getattr(sys, "frozen", False):
    try:
        import certifi as _certifi
        _ca = _certifi.where()
        os.environ.setdefault("SSL_CERT_FILE",       _ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE",  _ca)
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk

import base64
import hashlib
import io
import json
import re
import threading
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

try:
    from PIL import Image, ImageGrab, ImageEnhance, ImageTk
except ImportError:
    raise SystemExit("Falta Pillow: pip install Pillow")

try:
    import anthropic
except ImportError:
    raise SystemExit("Falta anthropic: pip install anthropic")

import audit
import update_manager
import graph_client


APP_VERSION = "1.0.0"
LIMA_TZ = timezone(timedelta(hours=-5), name="PET")


def now_lima() -> datetime:
    return datetime.now(LIMA_TZ).replace(tzinfo=None)


def today_lima() -> date:
    return datetime.now(LIMA_TZ).date()


# ── Auditoría ──────────────────────────────────────────────────────────────────
AUDIT_ENDPOINT = "https://project-legacy-audit.vercel.app/api/audit/polizas3420"
AUDIT_API_KEY  = "Ab3_Q9kL2Mx7P0eRZDfTN4H5S8aCWYJpU6B1oVdKiErsF_GnXqm2cO7A9wLQPZt5R0D8h4yN6fKJMeS1B_CapxVOWgT3UdlIHR7n"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()

def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return APP_DIR / name


def set_window_icon(win):
    try:
        ico = _resource_path("icon.ico")
        if ico.exists():
            try:
                win.iconbitmap(default=str(ico))
            except tk.TclError:
                win.iconbitmap(str(ico))
    except Exception:
        pass

# ── API Key ────────────────────────────────────────────────────────────────────
# El acceso a la app está controlado por Microsoft 365 (ver flujo de auth al inicio).
# La API key de Claude se desencripta en tiempo de compilación con la constante interna.
ENCRYPTED_API_KEY = "HczQewI6Tutf4fdlszW4DvXiLv0z3YQn/P9oayxkkiAB085XAD8I82vjpQ7uSZ1d98c43lTemzbhiX1YBT3ydT7WySgrIQDpYdm3LK0ljnem61nuaq/xDMy7W3swE5VcC/2aNz8+OsdD/4YX"
_APP_ACTIVATION = "G.Coril2026$"


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


API_KEY = _xor(
    base64.b64decode(ENCRYPTED_API_KEY),
    hashlib.sha256(_APP_ACTIVATION.encode()).digest(),
).decode()

audit.init(endpoint_url=AUDIT_ENDPOINT, api_key=AUDIT_API_KEY, app_version=APP_VERSION)
audit.log("app_start", success=True)


def fmt_tasa(v) -> str:
    """Convierte número a porcentaje con coma decimal (ej: 4.41 → '4,41%')."""
    if v is None:
        return ""
    try:
        return f"{float(v):.2f}".replace(".", ",") + "%"
    except Exception:
        return str(v)


def parse_tasa(raw: str) -> str:
    """Normaliza lo que escribió el usuario: '5' '5,0' '5.0' '5%' → '5,00%'."""
    raw = raw.strip().replace("%", "").replace(",", ".").strip()
    if not raw:
        return ""
    try:
        return f"{float(raw):.2f}".replace(".", ",") + "%"
    except ValueError:
        return ""


# ── Claude ─────────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-5"

PROMPT = """Analiza esta imagen de una póliza bursátil y extrae SOLO los datos indicados.

FORMATOS POSIBLES:

COMPRA — encabezado "COMPRA" o "PÓLIZA DE COMPRA":
- Extrae: fondo/ticker, cantidad de cuotas y monto neto de la póliza
- Si hay dos tablas lado a lado (compra + venta simulada), toma SOLO la de compra
- tipo = "COMPRA"

VENTA — encabezado "VENTA" o "PÓLIZA DE VENTA":
- Extrae: fondo/ticker, cantidad de cuotas, monto neto (usa "Monto Neto (cliente)" si existe, sino "Monto de Póliza")
- El encabezado tiene: tipo | nombre del cliente | número (código SAB)
- nombre_cliente: texto antes de " Y " si hay "Y PAREJA" o similar; si no hay " Y ", todo el nombre
- numero_orden: número entero en la esquina derecha del encabezado (es el código SAB)
- tipo = "VENTA"

PLAZO: Busca en el texto una duración de la inversión:
- Texto como "X días" → devuelve "X DÍAS" (ej: "26 días" → "26 DÍAS")
- Texto como "X meses" → devuelve "X MESES" (ej: "3 meses" → "3 MESES")
- Texto como "1 año" o "12 meses" → devuelve "1 AÑO"
- También puede aparecer como "FL1: DD/MM/AA" y "FL2: DD/MM/AA" — calcula los días de diferencia entre ambas fechas
- Si no encuentras el plazo, devuelve null

TASA: Busca el porcentaje de rentabilidad o rendimiento estimado:
- Aparece en frases como "rentabilidad estimada...sería X,XX%" o "X.XX%"
- También puede aparecer como "Utilidad Partícipe (sin IGK): X,XX%"
- Devuelve el valor como número sin el símbolo % (ej: 4.41 para "4,41%")
- Si no encuentras la tasa, devuelve null

REGLAS:
- Montos: sin símbolos ni separadores de miles, punto decimal (ej: 14574.51)
- El fondo es el código del instrumento con su número separado por espacio (ej: "ICMP 17", "PRIME 14", "ICMP 13") — conservar el espacio
- Campos inexistentes: null

Devuelve ÚNICAMENTE este JSON sin texto adicional ni markdown:
{
  "tipo": "COMPRA" o "VENTA",
  "ticker": "código del instrumento con espacio",
  "cantidad": número entero o null,
  "monto_compra": número o null,
  "monto_venta": número o null,
  "numero_orden": número entero o null,
  "nombre_cliente": "texto" o null,
  "plazo": "ej: 26 DÍAS, 1 AÑO, 3 MESES" o null,
  "tasa": número o null
}"""


def img_to_b64(img: Image.Image) -> str:
    img = img.convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.3)
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return base64.standard_b64encode(buf.getvalue()).decode()


def call_claude(img: Image.Image) -> dict:
    import time as _t
    client  = anthropic.Anthropic(api_key=API_KEY)
    img_w, img_h = img.size
    t0 = _t.perf_counter()
    in_tok = out_tok = None
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": img_to_b64(img)}},
                {"type": "text", "text": PROMPT},
            ]}],
        )
        try:
            in_tok  = getattr(resp.usage, "input_tokens",  None)
            out_tok = getattr(resp.usage, "output_tokens", None)
        except Exception:
            pass
        parts = [getattr(b, "text", None) for b in getattr(resp, "content", [])]
        raw   = "\n".join(p for p in parts if p).strip()
        if not raw:
            raise json.JSONDecodeError("Respuesta vacía", "", 0)
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        data  = json.loads(clean)
        audit.log("claude_call", success=True, model_name=CLAUDE_MODEL,
                  input_tokens=in_tok if isinstance(in_tok, int) else None,
                  output_tokens=out_tok if isinstance(out_tok, int) else None,
                  duration_ms=int((_t.perf_counter() - t0) * 1000),
                  metadata={"img_w": img_w, "img_h": img_h})
        return data
    except Exception as e:
        audit.log("claude_call", success=False, model_name=CLAUDE_MODEL,
                  input_tokens=in_tok if isinstance(in_tok, int) else None,
                  output_tokens=out_tok if isinstance(out_tok, int) else None,
                  duration_ms=int((_t.perf_counter() - t0) * 1000),
                  metadata={"img_w": img_w, "img_h": img_h,
                            "error_type": type(e).__name__[:50]})
        raise


# ── Clipboard helpers ──────────────────────────────────────────────────────────
def _resample_filter():
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _normalize_image(img: Image.Image) -> Image.Image:
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        try:
            bg.paste(img, mask=img.getchannel("A"))
            return bg
        except Exception:
            return img.convert("RGB")
    return img.convert("RGB")


def _open_image_file(path) -> "Image.Image | None":
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        with Image.open(p) as im:
            im.load()
            return _normalize_image(im)
    except Exception:
        return None


def _get_clipboard_image() -> "Image.Image | None":
    clip = ImageGrab.grabclipboard()
    if isinstance(clip, Image.Image):
        return _normalize_image(clip)
    if isinstance(clip, list):
        for path in clip:
            img = _open_image_file(path)
            if img is not None:
                return img
    return None


def _image_fingerprint(img: Image.Image) -> str:
    small = img.copy()
    small.thumbnail((96, 96), _resample_filter())
    small = small.convert("RGB")
    h = hashlib.sha256()
    h.update(f"{img.size[0]}x{img.size[1]}".encode())
    h.update(small.tobytes())
    return h.hexdigest()


# ── GUI ────────────────────────────────────────────────────────────────────────
PREVIEW_W       = 360
PREVIEW_H       = 200
WIDTH_BASE      = 400
WIDTH_WITH_FORM = 800
HEIGHT_BASE     = 620


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Captura de Pólizas → 3420  v{APP_VERSION}")
        set_window_icon(self)
        self.resizable(False, False)
        self.configure(bg="#F7F9FC")
        self._last_hash     = None
        self._pending       = None
        self._tk_img        = None
        self._processing    = False
        self._ready_to_send = False
        self._auth_ok       = False
        self._count         = 0
        self._json_data     = None
        self._cliente_data  = None
        self._form_visible  = False
        self._build()
        self._set_authenticating()
        self._poll()
        self.geometry(f"{WIDTH_BASE}x{HEIGHT_BASE}")

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg="#1B3F6E", height=56)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="Captura de Pólizas → 3420",
                 font=("Segoe UI", 13, "bold"), fg="white", bg="#1B3F6E"
                 ).pack(side="left", padx=18, pady=14)
        self.lbl_count = tk.Label(hdr, text="0 enviadas",
                                  font=("Segoe UI", 9), fg="#8BADD4", bg="#1B3F6E")
        self.lbl_count.pack(side="right", padx=14)

        # Dos columnas
        self._content = tk.Frame(self, bg="#F7F9FC")
        self._content.pack(fill="both", expand=True)
        self._left_col = tk.Frame(self._content, bg="#F7F9FC", width=400)
        self._left_col.pack(side="left", fill="both", expand=False)
        self._left_col.pack_propagate(False)
        self._right_col = tk.Frame(self._content, bg="#F7F9FC", width=380)
        self._right_col.pack_propagate(False)

        # Instrucción
        self.lbl_instruccion = tk.Label(
            self._left_col,
            text="📋  Copia una imagen desde el correo\ny aparecerá aquí automáticamente.",
            font=("Segoe UI", 10), fg="#6B7A8D", bg="#F7F9FC",
            justify="center", wraplength=340)
        self.lbl_instruccion.pack(pady=(16, 8))

        # Preview
        self._preview_frame = tk.Frame(
            self._left_col, bg="#E8EEF6",
            highlightthickness=1, highlightbackground="#C9D8EC",
            width=PREVIEW_W, height=PREVIEW_H)
        self._preview_frame.pack(padx=20)
        self._preview_frame.pack_propagate(False)
        self.lbl_preview = tk.Label(self._preview_frame, bg="#E8EEF6",
                                    text="Sin imagen", font=("Segoe UI", 9), fg="#A0AFBF")
        self.lbl_preview.place(relx=0.5, rely=0.5, anchor="center")

        # Estado
        self.lbl_estado = tk.Label(self._left_col, text="",
                                   font=("Segoe UI", 10, "bold"), fg="#1B3F6E", bg="#F7F9FC")
        self.lbl_estado.pack(pady=(12, 2))
        self.lbl_sub = tk.Label(self._left_col, text="",
                                font=("Segoe UI", 9), fg="#6B7A8D",
                                bg="#F7F9FC", wraplength=340, justify="center")
        self.lbl_sub.pack()

        # Botón procesar
        self.btn = tk.Button(
            self._left_col, text="⚙  Procesar imagen",
            font=("Segoe UI", 12, "bold"), bg="#1B3F6E", fg="white",
            activebackground="#142E52", activeforeground="white",
            relief="flat", padx=16, pady=12, cursor="hand2",
            state="disabled", disabledforeground="#8BADD4",
            command=self._on_btn_click)
        self.btn.pack(fill="x", padx=20, pady=(14, 4))

        # Botón enviar a SharePoint
        self.btn_enviar = tk.Button(
            self._left_col, text="📤  Enviar a SharePoint",
            font=("Segoe UI", 11, "bold"), bg="#0F6E56", fg="white",
            activebackground="#0A5240", activeforeground="white",
            relief="flat", padx=16, pady=10, cursor="hand2",
            state="disabled", disabledforeground="#7EC8A0",
            command=self._on_enviar)
        self.btn_enviar.pack(fill="x", padx=20, pady=(0, 6))

        # Log
        tk.Label(self._left_col, text="Registro",
                 font=("Segoe UI", 8, "bold"), fg="#7A8FA6", bg="#F7F9FC"
                 ).pack(anchor="w", padx=20, pady=(6, 2))
        frm = tk.Frame(self._left_col, bg="white",
                       highlightthickness=1, highlightbackground="#DDE8F4")
        frm.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.log_box = tk.Text(frm, font=("Consolas", 8), fg="#555E6B", bg="white",
                               relief="flat", bd=0, padx=6, pady=6,
                               state="disabled", wrap="word")
        sb = tk.Scrollbar(frm, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)

        self._build_form()

    def _build_form(self):
        ff = tk.Frame(self._right_col, bg="#EEF3FA",
                      highlightthickness=1, highlightbackground="#C9D8EC")
        ff.pack(fill="both", expand=True, padx=(0, 16), pady=(16, 14))
        self._form_frame = ff

        tk.Label(ff, text="📝  Datos del cliente y operación",
                 font=("Segoe UI", 11, "bold"), fg="#1B3F6E", bg="#EEF3FA"
                 ).pack(anchor="w", padx=18, pady=(16, 10))

        grid = tk.Frame(ff, bg="#EEF3FA")
        grid.pack(fill="x", padx=18, pady=(0, 4))
        grid.columnconfigure(1, weight=1)

        self._form_vars = {}
        vcmd_digits = (self.register(lambda s: s.isdigit() or s == ""), "%P")

        def add_field(row, label, key, digits=False, default=""):
            tk.Label(grid, text=label, font=("Segoe UI", 9), fg="#4A5568",
                     bg="#EEF3FA", anchor="w"
                     ).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
            var = tk.StringVar(value=default)
            kwargs = {"validate": "key", "validatecommand": vcmd_digits} if digits else {}
            e = tk.Entry(grid, textvariable=var, font=("Segoe UI", 10),
                         relief="flat", bd=0, bg="white",
                         highlightthickness=1, highlightbackground="#C9D8EC",
                         highlightcolor="#1B3F6E", **kwargs)
            e.grid(row=row, column=1, sticky="ew", pady=3, ipady=5)
            self._form_vars[key] = var
            return e

        default_asistente = os.environ.get("USERNAME", "").upper()

        tk.Label(grid, text="— BASE SP 3420 —", font=("Segoe UI", 8),
                 fg="#A0AFBF", bg="#EEF3FA"
                 ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))

        # Asesor: Entry + popup Listbox propio (no roba foco como ttk::combobox::Post)
        tk.Label(grid, text="Asesor", font=("Segoe UI", 9), fg="#4A5568",
                 bg="#EEF3FA", anchor="w"
                 ).grid(row=1, column=0, sticky="w", pady=3, padx=(0, 10))
        asesor_var = tk.StringVar()
        self._asesor_combo = ttk.Combobox(
            grid, textvariable=asesor_var,
            font=("Segoe UI", 10), state="normal",
            values=[], height=12,
        )
        self._asesor_combo.grid(row=1, column=1, sticky="ew", pady=3, ipady=3)
        self._form_vars["asesor"] = asesor_var

        self._asesor_popup   = None
        self._asesor_listbox = None

        def _hide_popup(evt=None):
            if self._asesor_popup and self._asesor_popup.winfo_exists():
                self._asesor_popup.withdraw()

        def _show_popup(items):
            if not items:
                _hide_popup()
                return
            # Crear ventana si no existe o fue destruida
            if self._asesor_popup is None or not self._asesor_popup.winfo_exists():
                pop = tk.Toplevel(self)
                pop.wm_overrideredirect(True)
                pop.wm_attributes("-topmost", True)
                frm = tk.Frame(pop, relief="solid", bd=1, bg="#C9D8EC")
                frm.pack(fill="both", expand=True)
                lb  = tk.Listbox(frm, font=("Segoe UI", 10), selectmode="single",
                                 relief="flat", bd=0, highlightthickness=0,
                                 activestyle="none", bg="white", fg="#1B3F6E",
                                 selectbackground="#1B3F6E", selectforeground="white")
                sb  = tk.Scrollbar(frm, command=lb.yview)
                lb.configure(yscrollcommand=sb.set)
                sb.pack(side="right", fill="y")
                lb.pack(side="left", fill="both", expand=True)
                self._asesor_popup   = pop
                self._asesor_listbox = lb

                def _pick(evt=None):
                    sel = lb.curselection()
                    if sel:
                        self._asesor_combo.set(lb.get(sel[0]))
                        self._asesor_combo.icursor("end")
                        self._asesor_combo.selection_clear()
                    _hide_popup()
                    self._asesor_combo.focus_set()

                lb.bind("<ButtonRelease-1>", _pick)
                lb.bind("<Return>",          _pick)

            lb = self._asesor_listbox
            lb.delete(0, "end")
            visible = min(len(items), 10)
            for item in items:
                lb.insert("end", item)
            lb.configure(height=visible)

            self._asesor_combo.update_idletasks()
            x  = self._asesor_combo.winfo_rootx()
            y  = self._asesor_combo.winfo_rooty() + self._asesor_combo.winfo_height()
            w  = self._asesor_combo.winfo_width()
            rh = self._asesor_combo.winfo_height()       # usa la altura del combo como referencia
            self._asesor_popup.geometry(f"{w}x{visible * rh}+{x}+{y}")
            self._asesor_popup.deiconify()
            self._asesor_popup.lift()

        def _asesor_key(event):
            if event.keysym in ("Escape",):
                _hide_popup()
                return
            if event.keysym in ("Return", "Tab"):
                _hide_popup()
                return
            if event.keysym in ("Up", "Down", "Left", "Right", "Home", "End"):
                return
            typed       = self._asesor_combo.get()
            typed_upper = typed.upper()
            all_vals    = getattr(self._asesor_combo, "_all_values", [])

            # Filtrar: cualquier nombre o apellido que EMPIECE por lo escrito
            if typed_upper:
                filtered = [v for v in all_vals
                            if any(w.startswith(typed_upper) for w in v.upper().split())]
            else:
                filtered = list(all_vals)

            self._asesor_combo["values"] = filtered
            _show_popup(filtered)

            # Autocomplete inline: solo si el nombre completo empieza por lo escrito
            if event.keysym not in ("BackSpace", "Delete") and typed_upper:
                starts = [v for v in all_vals if v.upper().startswith(typed_upper)]
                if starts:
                    suggestion = starts[0]
                    cursor     = len(typed)
                    self._asesor_combo.set(suggestion)
                    self._asesor_combo.after(0, lambda c=cursor: (
                        self._asesor_combo.icursor(c),
                        self._asesor_combo.selection_range(c, "end"),
                    ))

        self._asesor_combo.bind("<KeyRelease>", _asesor_key)
        self._asesor_combo.bind("<FocusOut>",   lambda e: self.after(150, _hide_popup))

        add_field(2, "Cliente",      "nombre_cliente")
        add_field(3, "Asistente",    "asistente",  default=default_asistente)
        add_field(4, "Código SAB",   "codigo_sab", digits=True)
        add_field(5, "Código SAF",   "codigo_saf", digits=True)
        # Plazo: número + unidad (DÍAS / MESES / AÑOS)
        tk.Label(grid, text="Plazo", font=("Segoe UI", 9), fg="#4A5568",
                 bg="#EEF3FA", anchor="w"
                 ).grid(row=6, column=0, sticky="w", pady=3, padx=(0, 10))

        plazo_frame = tk.Frame(grid, bg="#EEF3FA")
        plazo_frame.grid(row=6, column=1, sticky="ew", pady=3)
        plazo_frame.columnconfigure(0, weight=1)

        self._plazo_num  = tk.StringVar()
        self._plazo_unit = tk.StringVar(value="DÍAS")
        plazo_var = tk.StringVar()

        _SINGULAR = {"DÍAS": "DÍA", "MESES": "MES", "AÑOS": "AÑO"}

        def _update_plazo(*_):
            n = self._plazo_num.get().strip()
            u = self._plazo_unit.get().strip()
            if not n:
                plazo_var.set("")
                return
            try:
                num = int(n)
            except ValueError:
                plazo_var.set("")
                return
            unit_display = _SINGULAR.get(u, u) if num == 1 else u
            plazo_var.set(f"{num} {unit_display}")

        self._plazo_num.trace_add("write",  _update_plazo)
        self._plazo_unit.trace_add("write", _update_plazo)

        tk.Entry(plazo_frame, textvariable=self._plazo_num,
                 font=("Segoe UI", 10), relief="flat", bd=0,
                 bg="white", highlightthickness=1, highlightbackground="#C9D8EC",
                 highlightcolor="#1B3F6E", width=6,
                 validate="key", validatecommand=vcmd_digits,
                 ).grid(row=0, column=0, sticky="ew", ipady=5, padx=(0, 4))

        ttk.Combobox(plazo_frame, textvariable=self._plazo_unit,
                     values=["DÍAS", "MESES", "AÑOS"],
                     font=("Segoe UI", 10), state="readonly", width=7,
                     ).grid(row=0, column=1, ipady=3)

        self._form_vars["plazo"] = plazo_var

        _tasa_entry = add_field(7, "Tasa", "tasa")

        def _normalizar_tasa(evt=None):
            val = parse_tasa(self._form_vars["tasa"].get())
            if val:
                self._form_vars["tasa"].set(val)

        _tasa_entry.bind("<FocusOut>", _normalizar_tasa)
        _tasa_entry.bind("<Return>",   _normalizar_tasa)

        # Botón confirmar formulario
        self._btn_confirmar = tk.Button(
            ff, text="✅  Confirmar datos",
            font=("Segoe UI", 11, "bold"), bg="#1B3F6E", fg="white",
            activebackground="#142E52", activeforeground="white",
            relief="flat", padx=16, pady=14, cursor="hand2",
            command=self._on_confirmar)
        self._btn_confirmar.pack(fill="x", padx=18, pady=(6, 18))

    # ── Formulario ─────────────────────────────────────────────────────────────
    def _show_form(self):
        if not self._form_visible:
            self._right_col.pack(side="right", fill="both", expand=True)
            self._form_visible = True
            self.geometry(f"{WIDTH_WITH_FORM}x{HEIGHT_BASE}")

    def _hide_form(self):
        if self._form_visible:
            self._right_col.pack_forget()
            self._form_visible = False
            self.geometry(f"{WIDTH_BASE}x{HEIGHT_BASE}")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _log(self, msg, level="info"):
        icons = {"info": "·", "ok": "✔", "warn": "⚠", "err": "✖"}
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {icons.get(level,'·')} {msg}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _show_preview(self, img):
        thumb = img.copy()
        thumb.thumbnail((PREVIEW_W, PREVIEW_H), _resample_filter())
        self._tk_img = ImageTk.PhotoImage(thumb)
        self.lbl_preview.configure(image=self._tk_img, text="")

    def _rebuild_preview_label(self):
        for w in self._preview_frame.winfo_children():
            w.destroy()
        self._tk_img = None
        self._preview_frame.configure(bg="#E8EEF6", highlightbackground="#C9D8EC")
        self.lbl_preview = tk.Label(self._preview_frame, bg="#E8EEF6",
                                    text="Sin imagen", font=("Segoe UI", 9), fg="#A0AFBF")
        self.lbl_preview.place(relx=0.5, rely=0.5, anchor="center")

    # ── Estados UI ────────────────────────────────────────────────────────────
    def _set_idle(self):
        self._ready_to_send = False
        self._hide_form()
        self._json_data    = None
        self._pending      = None
        self._cliente_data = None
        self.lbl_instruccion.configure(
            text="📋  Copia una imagen desde el correo\ny aparecerá aquí automáticamente.")
        self.lbl_estado.configure(text="", fg="#1B3F6E")
        self.lbl_sub.configure(text="", fg="#6B7A8D")
        self.btn.configure(state="disabled", text="⚙  Procesar imagen")
        self.btn_enviar.configure(state="disabled", text="📤  Enviar a SharePoint")
        self._rebuild_preview_label()

    def _set_detected(self, img):
        self.lbl_instruccion.configure(text="¿Es esta la imagen que quieres procesar?")
        self.lbl_estado.configure(text="✅  Imagen lista", fg="#0F6E56")
        self.lbl_sub.configure(text=f"{img.size[0]}×{img.size[1]} px", fg="#6B7A8D")
        self.btn.configure(state="normal", text="⚙  Procesar imagen")
        self.btn_enviar.configure(state="disabled")
        self._show_preview(img)

    def _set_processing(self):
        self.lbl_instruccion.configure(text="Procesando la imagen…")
        self.lbl_estado.configure(text="⏳  Leyendo datos...", fg="#185FA5")
        self.lbl_sub.configure(text="Esto tarda unos segundos.")
        self.btn.configure(state="disabled")

    def _set_awaiting_form(self, tipo: str):
        label = "venta" if tipo == "VENTA" else "compra"
        self.lbl_instruccion.configure(
            text=f"Póliza de {label} detectada.\nCompleta los datos y confirma.")
        self.lbl_estado.configure(text="📝  Completa el formulario", fg="#185FA5")
        self.lbl_sub.configure(text="Puedes dejar campos vacíos si no aplican.")
        self.btn.configure(state="disabled")
        self._show_form()
        if not self._asesor_combo["values"]:
            threading.Thread(target=self._fetch_asesores, daemon=True).start()

    def _set_ready_to_send(self, detalle: str):
        self._ready_to_send = True
        self._hide_form()
        self.lbl_instruccion.configure(
            text="Datos confirmados.\nPresiona el botón para registrar en SharePoint.")
        self.lbl_estado.configure(text="✅  Datos listos", fg="#0F6E56")
        self.lbl_sub.configure(text=detalle, fg="#2E7D5A")
        self.btn_enviar.configure(state="normal", text="📤  Enviar a SharePoint")

    def _set_sending(self):
        self.lbl_estado.configure(text="⏳  Enviando a SharePoint...", fg="#185FA5")
        self.lbl_sub.configure(text="Registrando en FICOS. Espera un momento.")
        self.btn_enviar.configure(state="disabled", text="⏳  Enviando...")

    def _on_success_send(self, fila: int):
        self._count += 1
        self.lbl_count.configure(text=f"{self._count} enviadas")
        for w in self._preview_frame.winfo_children():
            w.destroy()
        self._preview_frame.configure(bg="#EBF7F0", highlightbackground="#7EC8A0")
        tk.Label(self._preview_frame, text="✅",
                 font=("Segoe UI", 44), bg="#EBF7F0"
                 ).place(relx=0.5, rely=0.25, anchor="center")
        tk.Label(self._preview_frame, text="¡Registrado en SharePoint!",
                 font=("Segoe UI", 13, "bold"), fg="#0A5C3A", bg="#EBF7F0"
                 ).place(relx=0.5, rely=0.55, anchor="center")
        tk.Label(self._preview_frame,
                 text=f"Fila {fila} en la hoja FICOS",
                 font=("Segoe UI", 9), fg="#2E7D5A", bg="#EBF7F0",
                 wraplength=320, justify="center"
                 ).place(relx=0.5, rely=0.76, anchor="center")
        self.btn_enviar.configure(state="disabled")
        self.after(4000, self._set_idle)

    def _on_send_error(self, error: str):
        short = error[:100] if len(error) > 100 else error
        self.lbl_estado.configure(text="⚠  Error al enviar a SharePoint", fg="#993C1D")
        self.lbl_sub.configure(text=short, fg="#993C1D")
        self.btn_enviar.configure(state="normal", text="📤  Reintentar")

    def _on_error(self, msg: str):
        self.lbl_estado.configure(text="⚠  Ocurrió un error", fg="#993C1D")
        self.lbl_sub.configure(text=msg)
        self.btn.configure(state="disabled")
        self.after(3500, self._set_idle)

    # ── Polling ────────────────────────────────────────────────────────────────
    def _poll(self):
        if not self._auth_ok:
            self.after(1500, self._poll)
            return
        if not self._processing and not self._form_visible and not self._ready_to_send:
            try:
                img = _get_clipboard_image()
                if img is not None:
                    fp = _image_fingerprint(img)
                    if fp != self._last_hash:
                        self._last_hash = fp
                        self._pending   = img.copy()
                        self._set_detected(self._pending)
                        self._log(f"Imagen detectada: {img.size[0]}×{img.size[1]}px")
            except Exception:
                pass
        self.after(1500, self._poll)

    # ── Flujo ─────────────────────────────────────────────────────────────────
    def _on_btn_click(self):
        if not self._pending:
            return
        self._processing = True
        self._set_processing()
        self._log("Enviando a Claude...")
        threading.Thread(target=self._run_claude,
                         args=(self._pending.copy(),), daemon=True).start()

    def _run_claude(self, img):
        try:
            data = call_claude(img)
            self.after(0, lambda d=data: self._on_claude_success(d))
        except json.JSONDecodeError:
            self.after(0, lambda: self._on_claude_error(
                "Respuesta inválida", "No se pudieron leer los datos.\nIntenta de nuevo."))
        except anthropic.APIConnectionError:
            self.after(0, lambda: self._on_claude_error(
                "Error de conexión", "Sin conexión a internet.\nVerifica tu red e intenta de nuevo."))
        except anthropic.AuthenticationError:
            self.after(0, lambda: self._on_claude_error(
                "API Key inválida", "Código de activación inválido.\nBorra .pwd y reinicia."))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.after(0, lambda msg=f"Error: {type(e).__name__}: {e}\n{tb}":
                       self._on_claude_error(msg, "Error inesperado.\nRevisa el registro."))
        finally:
            self.after(0, lambda: setattr(self, "_processing", False))

    def _on_claude_success(self, data: dict):
        self._json_data = data
        tipo   = data.get("tipo", "COMPRA")
        monto  = data.get("monto_venta") if tipo == "VENTA" else data.get("monto_compra")
        ticker = data.get("ticker", "")
        partes = [f"[{tipo}]", ticker]
        if isinstance(monto, (int, float)): partes.append(f"Neto: {monto:,.2f}")
        self._log(f"JSON OK → {'  |  '.join(p for p in partes if p)}", "ok")

        # Pre-llenar campos del formulario extraídos de la imagen
        numero_orden = data.get("numero_orden")
        if numero_orden is not None:
            self._form_vars["codigo_sab"].set(str(int(numero_orden)))

        nombre_cliente = data.get("nombre_cliente")
        if nombre_cliente:
            self._form_vars["nombre_cliente"].set(nombre_cliente.strip().upper())

        plazo = data.get("plazo")
        if plazo:
            _TO_PLURAL = {"DÍA": "DÍAS", "DIA": "DÍAS", "MES": "MESES", "AÑO": "AÑOS", "ANO": "AÑOS"}
            m = re.match(r'^(\d+)\s+(.+)$', str(plazo).strip().upper())
            if m:
                unit_raw = m.group(2).strip()
                unit_plural = _TO_PLURAL.get(unit_raw, unit_raw if unit_raw in ("DÍAS", "MESES", "AÑOS") else "DÍAS")
                self._plazo_num.set(m.group(1))
                self._plazo_unit.set(unit_plural)
            else:
                self._plazo_num.set(str(plazo).strip())

        tasa = data.get("tasa")
        if tasa is not None:
            self._form_vars["tasa"].set(fmt_tasa(tasa))

        self._set_awaiting_form(tipo)

    def _on_claude_error(self, log_msg: str, ui_msg: str):
        self._log(log_msg, "err")
        self._on_error(ui_msg)

    def _on_confirmar(self):
        if not self._json_data:
            self._on_error("No hay datos extraídos.")
            return

        cliente = {
            "asesor":         self._form_vars["asesor"].get().strip(),
            "codigo_sab":     self._form_vars["codigo_sab"].get().strip(),
            "codigo_saf":     self._form_vars["codigo_saf"].get().strip(),
            "nombre_cliente": self._form_vars["nombre_cliente"].get().strip(),
            "asistente":      self._form_vars["asistente"].get().strip(),
            "plazo":          self._form_vars["plazo"].get().strip(),
            "tasa":           self._form_vars["tasa"].get().strip(),
        }

        tipo   = self._json_data.get("tipo", "COMPRA")
        monto  = self._json_data.get("monto_venta") if tipo == "VENTA" else self._json_data.get("monto_compra")
        ticker = self._json_data.get("ticker", "")
        partes = [f"[{tipo}]", ticker]
        if isinstance(monto, (int, float)):
            partes.append(f"Neto: {monto:,.2f}")
        detalle = "  |  ".join(p for p in partes if p)

        self._log(f"Datos confirmados → {detalle}", "ok")
        self._cliente_data = cliente

        # Limpiar formulario conservando asistente
        for k in self._form_vars:
            if k != "asistente":
                self._form_vars[k].set("")
        self._plazo_num.set("")
        self._plazo_unit.set("DÍAS")

        self._set_ready_to_send(detalle)

    def _on_enviar(self):
        if not self._json_data or not self._cliente_data:
            return
        self._set_sending()
        threading.Thread(
            target=self._run_enviar,
            args=(self._json_data, self._cliente_data),
            daemon=True,
        ).start()

    def _run_enviar(self, json_data: dict, cliente: dict):
        result = graph_client.write_row_to_sp(json_data, cliente)
        self.after(0, lambda r=result: self._on_enviar_done(r))

    def _on_enviar_done(self, result: dict):
        if result.get("ok"):
            fila = result["fila"]
            self._log(f"Escrito en FICOS fila {fila}", "ok")
            audit.log("sp_write", success=True,
                      metadata={"fila": fila,
                                "tipo": self._json_data.get("tipo", "?") if self._json_data else "?"})
            self._on_success_send(fila)
        else:
            error = result.get("error", "Error desconocido")
            self._log(f"Error al escribir en SharePoint: {error}", "err")
            audit.log("sp_write", success=False, metadata={"error": str(error)[:100]})
            self._on_send_error(str(error))

    # ── Autenticación Microsoft 365 ───────────────────────────────────────────
    def _set_authenticating(self):
        self.lbl_instruccion.configure(
            text="Iniciando sesión con Microsoft 365…\nSe abrirá el navegador si es necesario.")
        self.lbl_estado.configure(text="🔐  Verificando acceso...", fg="#185FA5")
        self.lbl_sub.configure(text="Comprobando que tu cuenta tiene acceso a SharePoint.")
        self.btn.configure(state="disabled")
        self.btn_enviar.configure(state="disabled")

    _AUTH_WEEK_MS = 7 * 24 * 3600 * 1000   # revalidar una vez por semana

    def start_sp_auth(self):
        threading.Thread(target=lambda: self._run_sp_auth(initial=True),
                         daemon=True).start()

    def _run_sp_auth(self, initial: bool):
        try:
            graph_client.verify_access()
            self.after(0, lambda: self._on_auth_success(initial))
        except Exception as e:
            self.after(0, lambda err=str(e): self._on_auth_fail(err, initial))

    def _on_auth_success(self, initial: bool):
        self._auth_ok = True
        if initial:
            self._log("Acceso a SharePoint verificado", "ok")
            self._set_idle()
            self.after(100, self.start_update_check)
            self.after(200, self.load_asesores)
        else:
            # Re-auth en segundo plano: solo loguear, NO tocar el estado actual
            self._log("Sesión Microsoft renovada silenciosamente", "ok")
        # Programar siguiente verificación semanal
        self.after(self._AUTH_WEEK_MS, self._periodic_auth_check)

    def _on_auth_fail(self, error: str, initial: bool):
        self._auth_ok = False
        short = error[:120] if len(error) > 120 else error
        self._log(f"Auth fallida: {short}", "err")
        if initial:
            # Primera autenticación: bloquear toda la app
            self.lbl_instruccion.configure(
                text="Sin acceso a SharePoint.\nContacta a tu supervisor.")
            self.lbl_estado.configure(text="⚠  Acceso denegado", fg="#993C1D")
            self.lbl_sub.configure(text=short, fg="#993C1D")
            self.btn.configure(
                state="normal", text="🔄  Reintentar login",
                command=lambda: (self._set_authenticating(),
                                 self.after(200, self.start_sp_auth)))
        else:
            # Re-auth fallida en segundo plano: avisar sin interrumpir el proceso
            # El usuario puede seguir; si intenta enviar a SP, get_token() lo re-autenticará
            self._log("No se pudo renovar la sesión — se pedirá login al enviar a SP", "warn")
            self.after(self._AUTH_WEEK_MS, self._periodic_auth_check)

    def _periodic_auth_check(self):
        threading.Thread(target=self._run_periodic_auth, daemon=True).start()

    def _run_periodic_auth(self):
        """
        Intento silencioso semanal.
        Si el refresh token sigue vivo → renovamos y reprogramamos.
        Si expiró → lanzamos re-auth interactiva en segundo plano sin tocar el proceso.
        """
        try:
            graph_client.get_token()          # silent refresh — no abre navegador
            self.after(0, lambda: self._on_auth_success(initial=False))
        except Exception:
            # Refresh token expirado: lanzar re-auth interactiva en background
            # (abre navegador si es necesario; el hilo actual espera al login)
            self.after(0, lambda: self._log(
                "Token semanal expirado — re-login en segundo plano", "warn"))
            threading.Thread(
                target=lambda: self._run_sp_auth(initial=False),
                daemon=True).start()

    def start_update_check(self):
        update_manager.check_for_updates_with_status(
            parent=self,
            current_version=APP_VERSION,
            force=True,
        )

    # ── Carga de asesores desde SharePoint ────────────────────────────────────
    def load_asesores(self):
        threading.Thread(target=self._fetch_asesores, daemon=True).start()

    def _fetch_asesores(self):
        try:
            resultado = graph_client.get_lista_oficial()
            asesores  = resultado.get("asesores", [])
            if asesores:
                self.after(0, lambda a=asesores: self._set_asesores(a))
        except Exception:
            pass

    def _set_asesores(self, asesores: list):
        try:
            self._asesor_combo["values"] = asesores
            self._asesor_combo._all_values = asesores
            self._log(f"Lista de asesores cargada ({len(asesores)} registros)", "ok")
        except Exception:
            pass


if __name__ == "__main__":
    app = App()
    app.after(150, app.start_sp_auth)   # login Microsoft → si ok: update_check + load_asesores
    app.mainloop()
