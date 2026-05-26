"""
generar_key.py  —  uso exclusivo del administrador
Corre este script UNA VEZ para encriptar la API key y escribirla en app.py
"""
import base64, hashlib, getpass, sys, re
from pathlib import Path

APP_FILE = Path(__file__).parent / "app.py"

def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def encrypt(api_key: str, password: str) -> str:
    key = hashlib.sha256(password.encode()).digest()
    return base64.b64encode(_xor(api_key.encode(), key)).decode()

print("=" * 50)
print("  Generador de API Key encriptada")
print("=" * 50)
print()

if not APP_FILE.exists():
    sys.exit(f"Error: no se encontró {APP_FILE}")

api_key = getpass.getpass("  API Key de Anthropic (no se mostrará): ").strip()
if not api_key.startswith("sk-ant-"):
    sys.exit("Error: no parece una API key válida (debe empezar con sk-ant-)")

print()
password  = getpass.getpass("  Contraseña que compartirás con usuarios: ").strip()
if not password:
    sys.exit("Error: la contraseña no puede estar vacía")
password2 = getpass.getpass("  Repite la contraseña: ").strip()
if password != password2:
    sys.exit("Error: las contraseñas no coinciden")

encrypted = encrypt(api_key, password)

# Reemplazar la línea ENCRYPTED_API_KEY en app.py
content = APP_FILE.read_text(encoding="utf-8")
new_content, n = re.subn(
    r'^ENCRYPTED_API_KEY\s*=\s*".*"',
    f'ENCRYPTED_API_KEY = "{encrypted}"',
    content,
    flags=re.MULTILINE
)

if n == 0:
    sys.exit("Error: no se encontró ENCRYPTED_API_KEY en app.py — ¿ya fue modificado?")

APP_FILE.write_text(new_content, encoding="utf-8")

print()
print("=" * 50)
print("  ✔  app.py actualizado correctamente.")
print(f"  ✔  No olvides compartir la contraseña {'*' * len(password)} con los usuarios.")
print("=" * 50)