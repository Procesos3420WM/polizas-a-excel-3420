"""
update_manager.py - Sistema de actualización automática para Polizas a Excel 3420.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

# ── CONFIGURACIÓN ──────────────────────────────────────────────────────────────
# URL del repositorio GitHub independiente para la versión 3420.
# Configurar cuando se cree el repo en GitHub.
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/Procesos3420WM/polizas-a-excel-3420/main/version.json"

APP_NAME      = "Polizas a Excel 3420"
_LOCAL_DIR    = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PolizasAExcel3420"
UPDATE_STATE  = _LOCAL_DIR / "update_state.json"
UPDATES_CACHE = _LOCAL_DIR / "updates"
HTTP_TIMEOUT  = 3

UPDATE_CHECK_INTERVAL = timedelta(hours=1)


def _ensure_dirs():
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_CACHE.mkdir(parents=True, exist_ok=True)


def _parse_ver(v: str) -> tuple:
    """'1.2.10' -> (1, 2, 10). Comparación semver correcta."""
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_ver(remote) > _parse_ver(local)


def _already_checked_recently() -> bool:
    try:
        state = json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
        last_check_at = state.get("last_check_at")

        if not last_check_at:
            return False

        last_dt = datetime.fromisoformat(last_check_at)
        elapsed = datetime.now() - last_dt

        return elapsed < UPDATE_CHECK_INTERVAL

    except Exception:
        return False


def _save_check():
    try:
        _ensure_dirs()
        UPDATE_STATE.write_text(
            json.dumps(
                {
                    "last_check_at": datetime.now().isoformat(timespec="seconds")
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def force_recheck():
    """Fuerza que la próxima llamada a check_for_updates consulte GitHub."""
    try:
        if UPDATE_STATE.exists():
            UPDATE_STATE.unlink()
    except Exception:
        pass


def _fetch_manifest() -> dict | None:
    """Descarga y parsea version.json remoto. Retorna None si falla o hay timeout."""
    try:
        req = urllib.request.Request(
            UPDATE_MANIFEST_URL,
            headers={"User-Agent": f"{APP_NAME}/updater"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, progress_cb=None) -> bool:
    """Descarga url -> dest. progress_cb(bytes_done, total) es opcional."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/updater"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0

            with open(dest, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
                    done += len(chunk)

                    if progress_cb and total:
                        progress_cb(done, total)

        return True

    except Exception:
        return False


def _app_exe() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()

    return Path(__file__).resolve()


def _find_updater() -> Path | None:
    candidate = _app_exe().parent / "Updater.exe"
    return candidate if candidate.exists() else None


def _launch_updater(new_exe: Path) -> bool:
    """Lanza Updater.exe pasando los argumentos necesarios. Retorna True si se pudo lanzar."""
    updater = _find_updater()

    if updater is None:
        messagebox.showerror(
            "Actualización",
            "No se encontró Updater.exe junto al ejecutable principal.\n"
            "No es posible actualizar automáticamente.",
        )
        return False

    target = _app_exe()

    cmd = [
        str(updater),
        "--pid",         str(os.getpid()),
        "--target",      str(target),
        "--replacement", str(new_exe),
        "--restart",     str(target),
    ]

    try:
        subprocess.Popen(cmd)
        return True

    except Exception as e:
        messagebox.showerror("Actualización", f"No se pudo iniciar el actualizador:\n{e}")
        return False


# ── Diálogo de descarga ────────────────────────────────────────────────────────
class _DownloadDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, manifest: dict):
        super().__init__(parent)

        self._app      = parent
        self._manifest = manifest

        self.title("Actualizando...")
        self.resizable(False, False)
        self.configure(bg="#F7F9FC")
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        w, h = 400, 150

        self.update_idletasks()

        sx = parent.winfo_x() + (parent.winfo_width()  - w) // 2
        sy = parent.winfo_y() + (parent.winfo_height() - h) // 2

        self.geometry(f"{w}x{h}+{sx}+{sy}")

        tk.Label(
            self,
            text="Descargando actualización...",
            font=("Segoe UI", 11, "bold"),
            fg="#1B3F6E",
            bg="#F7F9FC",
        ).pack(pady=(18, 4))

        self._lbl = tk.Label(
            self,
            text="Iniciando...",
            font=("Segoe UI", 9),
            fg="#6B7A8D",
            bg="#F7F9FC",
        )
        self._lbl.pack()

        self._bar = ttk.Progressbar(self, length=350, mode="indeterminate")
        self._bar.pack(pady=12)
        self._bar.start(10)

        self.after(200, self._start)

    def _start(self):
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        manifest = self._manifest

        url     = manifest.get("download_url", "")
        sha256  = manifest.get("sha256", "")
        version = manifest.get("version", "new")

        if not url.lower().endswith(".exe"):
            self.after(
                0,
                lambda: self._fail("La URL de descarga no apunta a un archivo .exe."),
            )
            return

        _ensure_dirs()

        dest = UPDATES_CACHE / f"PolizasAExcel3420_{version}.exe"

        self.after(0, lambda: self._lbl.configure(text="Conectando a GitHub..."))

        def prog(done, total):
            pct = int(done * 100 / total)
            self.after(
                0,
                lambda p=pct: self._lbl.configure(text=f"Descargando... {p}%"),
            )

        if not _download(url, dest, prog):
            self.after(
                0,
                lambda: self._fail("Error al descargar. Verifica tu conexión."),
            )
            return

        if sha256:
            self.after(0, lambda: self._lbl.configure(text="Verificando integridad..."))

            if _sha256(dest).lower() != sha256.lower():
                dest.unlink(missing_ok=True)

                self.after(
                    0,
                    lambda: self._fail(
                        "El hash SHA256 no coincide.\n"
                        "El archivo descargado puede estar corrupto."
                    ),
                )
                return

        self.after(0, lambda d=dest: self._done(d))

    def _fail(self, msg: str):
        self._bar.stop()
        self.destroy()
        messagebox.showerror("Error de actualización", msg)

    def _done(self, dest: Path):
        self._bar.stop()
        self._lbl.configure(text="Lanzando actualizador...")
        self.update()

        launched = _launch_updater(dest)

        self.destroy()

        if launched:
            self._app.quit()


# ── API pública ────────────────────────────────────────────────────────────────
def check_for_updates(parent: tk.Tk, current_version: str, force: bool = False):
    if not force and _already_checked_recently():
        return

    _save_check()

    manifest = _fetch_manifest()

    if not manifest:
        return

    remote = manifest.get("version", "")

    if not _is_newer(remote, current_version):
        return

    notes     = manifest.get("notes", "")
    mandatory = manifest.get("mandatory", False)

    def show():
        prefix = "⚠  Actualización importante\n\n" if mandatory else ""

        msg = (
            f"{prefix}"
            f"Versión actual:   {current_version}\n"
            f"Nueva versión:    {remote}\n"
        )

        if notes:
            msg += f"\n{notes}\n"

        msg += "\n¿Deseas actualizar ahora?"

        if messagebox.askyesno("Actualización disponible", msg, parent=parent):
            _DownloadDialog(parent, manifest)

    parent.after(0, show)

def check_for_updates_with_status(parent: tk.Tk, current_version: str, force: bool = True):
    """
    Revisa actualizaciones mostrando una ventana pequeña de espera al abrir la app.
    """

    win = tk.Toplevel(parent)
    win.title("Actualizaciones")
    win.resizable(False, False)
    win.configure(bg="#F7F9FC")
    win.grab_set()
    win.protocol("WM_DELETE_WINDOW", lambda: None)

    w, h = 360, 130
    parent.update_idletasks()

    sx = parent.winfo_x() + (parent.winfo_width() - w) // 2
    sy = parent.winfo_y() + (parent.winfo_height() - h) // 2

    win.geometry(f"{w}x{h}+{sx}+{sy}")

    tk.Label(
        win,
        text="Buscando actualizaciones...",
        font=("Segoe UI", 11, "bold"),
        fg="#1B3F6E",
        bg="#F7F9FC",
    ).pack(pady=(22, 6))

    lbl = tk.Label(
        win,
        text="Consultando GitHub. Espera un momento.",
        font=("Segoe UI", 9),
        fg="#6B7A8D",
        bg="#F7F9FC",
    )
    lbl.pack()

    bar = ttk.Progressbar(win, length=280, mode="indeterminate")
    bar.pack(pady=14)
    bar.start(10)

    def close_check_window():
        try:
            bar.stop()
            win.grab_release()
            win.destroy()
        except Exception:
            pass

    def worker():
        if not force and _already_checked_recently():
            parent.after(0, close_check_window)
            return

        _save_check()

        manifest = _fetch_manifest()

        if not manifest:
            parent.after(0, close_check_window)
            return

        remote = manifest.get("version", "")

        if not _is_newer(remote, current_version):
            parent.after(0, close_check_window)
            return

        notes = manifest.get("notes", "")
        mandatory = manifest.get("mandatory", False)

        def show_update_prompt():
            close_check_window()

            prefix = "⚠  Actualización importante\n\n" if mandatory else ""

            msg = (
                f"{prefix}"
                f"Versión actual:   {current_version}\n"
                f"Nueva versión:    {remote}\n"
            )

            if notes:
                msg += f"\n{notes}\n"

            msg += "\n¿Deseas actualizar ahora?"

            if messagebox.askyesno("Actualización disponible", msg, parent=parent):
                _DownloadDialog(parent, manifest)

        parent.after(0, show_update_prompt)

    threading.Thread(target=worker, daemon=True).start()
