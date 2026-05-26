"""
updater.py — Actualizador externo para Polizas a Excel 3420.

Compilar con PyInstaller:
    pyinstaller --onefile --windowed --name=Updater updater.py

Uso (llamado automáticamente por la app principal):
    Updater.exe --pid PID --target RUTA_EXE_ACTUAL --replacement NUEVO_EXE --restart RUTA_A_RELANZAR

Flujo:
  1. Espera que el proceso con PID termine.
  2. Crea backup del exe actual (target + ".bak").
  3. Reemplaza target con replacement.
  4. Relanza restart.
  5. Registra todo en %LOCALAPPDATA%\\PolizasAExcel3420\\updater.log
"""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_LOG_DIR  = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PolizasAExcel3420"
_LOG_FILE = _LOG_DIR / "updater.log"


def _log(msg: str):
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _wait_pid(pid: int, timeout: int = 30) -> bool:
    """Espera a que el proceso termine. Usa WinAPI; fallback a polling."""
    SYNCHRONIZE  = 0x00100000
    WAIT_TIMEOUT = 0x00000102

    try:
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            _log(f"Proceso {pid} ya terminó o sin acceso — continuando.")
            return True
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout * 1000)
        ctypes.windll.kernel32.CloseHandle(handle)
        if result == WAIT_TIMEOUT:
            _log(f"Timeout esperando proceso {pid}.")
            return False
        return True
    except Exception as e:
        _log(f"WaitForSingleObject falló ({e}) — usando polling.")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except (OSError, ProcessLookupError):
                return True
        return False


def _show_error(msg: str):
    """Muestra un messagebox de error si tkinter está disponible."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showwarning("Actualización — Polizas a Excel 3420", msg)
        r.destroy()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Actualizador de Polizas a Excel 3420")
    parser.add_argument("--pid",         type=int,  required=True,
                        help="PID del proceso principal a esperar")
    parser.add_argument("--target",      type=Path, required=True,
                        help="Ruta del exe actual a reemplazar")
    parser.add_argument("--replacement", type=Path, required=True,
                        help="Ruta del nuevo exe descargado")
    parser.add_argument("--restart",     type=Path, required=True,
                        help="Ruta del exe a relanzar después de actualizar")
    args = parser.parse_args()

    pid         = args.pid
    target      = args.target.resolve()
    replacement = args.replacement.resolve()
    restart     = args.restart.resolve()
    backup      = target.parent / (target.name + ".bak")

    _log("=" * 60)
    _log(f"Updater iniciado | PID={pid}")
    _log(f"  Target:      {target}")
    _log(f"  Replacement: {replacement}")
    _log(f"  Restart:     {restart}")

    # 1. Esperar al proceso principal
    _log(f"Esperando cierre del proceso {pid}...")
    if not _wait_pid(pid):
        _log("El proceso no terminó. Abortando.")
        _show_error(
            "No se pudo completar la actualización.\n"
            "Cierre la aplicación manualmente e intente de nuevo."
        )
        return

    _log("Proceso terminado. Procediendo con el reemplazo.")

    # 2. Backup del exe actual
    try:
        if target.exists():
            shutil.copy2(str(target), str(backup))
            _log(f"Backup creado: {backup}")
    except Exception as e:
        _log(f"No se pudo crear backup: {e} — continuando sin backup.")

    # 3. Reemplazar exe
    try:
        shutil.copy2(str(replacement), str(target))
        _log(f"Reemplazo exitoso: {target}")
    except PermissionError:
        _log("Sin permisos para reemplazar el exe.")
        if backup.exists():
            try:
                shutil.copy2(str(backup), str(target))
                _log("Backup restaurado correctamente.")
            except Exception as be:
                _log(f"Error restaurando backup: {be}")
        _show_error(
            f"No se pudo actualizar automáticamente.\n\n"
            f"Cierre la aplicación y reemplace el archivo manualmente:\n{target}\n\n"
            f"Revisa el log en:\n{_LOG_FILE}"
        )
        return
    except Exception as e:
        _log(f"Error inesperado al reemplazar: {e}")
        if backup.exists():
            try:
                shutil.copy2(str(backup), str(target))
                _log("Backup restaurado.")
            except Exception:
                pass
        return

    # 4. Limpiar archivo temporal
    try:
        replacement.unlink()
        _log("Archivo temporal eliminado.")
    except Exception as e:
        _log(f"No se pudo eliminar temporal: {e}")

    # 5. Relanzar la aplicación
    try:
        _log(f"Relanzando: {restart}")
        subprocess.Popen([str(restart)])
        _log("Aplicación relanzada correctamente.")
    except Exception as e:
        _log(f"Error al relanzar la aplicación: {e}")

    _log("Updater finalizado.\n")


if __name__ == "__main__":
    main()
