"""Prepara el modo demo (lo ejecutan los contenedores de docker-compose.demo.yml).

    python demo/preparar.py datos        datos ficticios + usuario del CRM + flujos de n8n listos para importar
    python demo/preparar.py cuenta-n8n   espera a n8n y crea la cuenta de demo para entrar al editor
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "n8n"))

from app import open_store, seed_demo  # noqa: E402
from config import Config  # noqa: E402

env = os.environ.get


def prepare_data():
    cfg = Config()
    store = open_store(cfg)
    with store.tx() as conn:
        empty = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
    if empty:
        seed_demo(store)
        print("Datos ficticios cargados en el CRM")
    store.set_user(env("DEMO_CRM_USER", "demo"), env("DEMO_CRM_PASSWORD", "demo2026"))

    import instalar

    destination = cfg.data_dir / "n8n-demo"
    instalar.generate(destination, instalar.settings(cfg))
    print(f"Flujos y credenciales de demo listos en {destination}")


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as res:
        return json.loads(res.read())


def create_n8n_account():
    base = env("N8N_URL", "http://n8n:5678").rstrip("/")
    # n8n responde /healthz antes de cargar su API: se espera a que /rest/settings conteste.
    settings = None
    for _ in range(120):
        try:
            settings = _get_json(f"{base}/rest/settings")["data"]
            break
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            time.sleep(3)
    if settings is None:
        sys.exit("n8n no respondió a tiempo")

    account = {"email": env("DEMO_N8N_EMAIL", "demo@opensinergia.demo"), "firstName": "Demo",
               "lastName": "OpenSinergia", "password": env("DEMO_N8N_PASSWORD", "Demo2026!")}
    if settings.get("userManagement", {}).get("showSetupOnFirstLoad"):
        request = urllib.request.Request(f"{base}/rest/owner/setup", data=json.dumps(account).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15):
            print(f"Cuenta de n8n creada: {account['email']}")
    else:
        print("La cuenta de n8n ya existía")  # la demo ya se había levantado antes

    print("\n  Demo lista:")
    print("    Formulario y guía ... http://localhost:8090")
    print(f"    CRM ................. http://localhost:8080  ({env('DEMO_CRM_USER', 'demo')} / {env('DEMO_CRM_PASSWORD', 'demo2026')})")
    print(f"    n8n ................. http://localhost:5678  ({account['email']} / {account['password']})")
    print("    Bandeja de correo ... http://localhost:8025\n")


if __name__ == "__main__":
    commands = {"datos": prepare_data, "cuenta-n8n": create_n8n_account}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
