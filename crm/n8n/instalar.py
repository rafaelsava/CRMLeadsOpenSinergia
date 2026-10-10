"""Instala los flujos del CRM en n8n: crea las credenciales desde .env, importa los flujos y los activa.

Uso (desde la carpeta crm/, con `docker compose up -d` ya corriendo):

    python3 n8n/instalar.py

Se puede repetir sin problema: vuelve a escribir las mismas credenciales y flujos (mismos IDs).
"""
import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config  # noqa: E402

FLOWS_DIR = Path(__file__).resolve().parent / "flujos"
REMOTE_DIR = "/tmp/crm-n8n"
WORKFLOW_IDS = ("osCrmAlertaErr01", "osCrmLeadsWeb001", "osCrmResumenDia1")


def env_flag(value, default):
    return default if value in (None, "") else value.strip().lower() in ("1", "true", "si", "sí", "yes")


def settings(cfg):
    import os

    get = os.environ.get
    values = {
        "form_sender": get("N8N_FORM_SENDER", "").strip(),
        "crm_url": get("N8N_CRM_URL", "http://crm:8080").rstrip("/"),
        "token": cfg.n8n_token,
        "mail_user": get("N8N_MAIL_USER", cfg.gmail_user),
        "mail_password": get("N8N_MAIL_PASSWORD", cfg.gmail_app_password).replace(" ", ""),
        "imap_host": get("N8N_IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(get("N8N_IMAP_PORT", "993")),
        "imap_secure": env_flag(get("N8N_IMAP_SECURE"), True),
        "smtp_host": get("N8N_SMTP_HOST", cfg.smtp_host or "smtp.gmail.com"),
        "smtp_port": int(get("N8N_SMTP_PORT", str(cfg.smtp_port or 465))),
        "smtp_secure": env_flag(get("N8N_SMTP_SECURE"), int(get("N8N_SMTP_PORT", str(cfg.smtp_port or 465))) == 465),
        "summary_to": ", ".join(cfg.summary_to),
        "summary_from": cfg.summary_from or cfg.gmail_user,
        "summary_hour": cfg.summary_hour,
    }
    missing = [name for name, key in (("N8N_API_TOKEN", "token"), ("N8N_FORM_SENDER", "form_sender"),
                                      ("GMAIL_USER", "mail_user"), ("GMAIL_APP_PASSWORD", "mail_password"),
                                      ("SUMMARY_TO", "summary_to")) if not values[key]]
    if missing:
        sys.exit("Faltan valores en .env: " + ", ".join(missing))
    return values


def credentials(s):
    return [
        {"id": "osCrmImapGmail01", "name": "Gmail de la empresa (IMAP)", "type": "imap",
         "data": {"user": s["mail_user"], "password": s["mail_password"], "host": s["imap_host"],
                  "port": s["imap_port"], "secure": s["imap_secure"], "allowUnauthorizedCerts": False}},
        {"id": "osCrmSmtpGmail01", "name": "Gmail de la empresa (SMTP)", "type": "smtp",
         "data": {"user": s["mail_user"], "password": s["mail_password"], "host": s["smtp_host"],
                  "port": s["smtp_port"], "secure": s["smtp_secure"]}},
        {"id": "osCrmTokenApi001", "name": "CRM OpenSinergia (token)", "type": "httpHeaderAuth",
         "data": {"name": "Authorization", "value": f"Bearer {s['token']}"}},
    ]


def render_flow(path, s):
    replacements = {"__CRM_URL__": s["crm_url"], "__FORM_SENDER__": s["form_sender"],
                    "__ALERT_FROM__": s["summary_from"], "__ALERT_TO__": s["summary_to"]}

    def walk(value):
        if isinstance(value, str):
            for key, replacement in replacements.items():
                value = value.replace(key, replacement)
            return value
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    flow = walk(json.loads(path.read_text(encoding="utf-8")))
    for node in flow["nodes"]:
        if node["type"] == "n8n-nodes-base.scheduleTrigger":
            node["parameters"]["rule"]["interval"][0]["triggerAtHour"] = s["summary_hour"]
            node["name"] = f"Todos los días a las {s['summary_hour']}:00"
    # El nombre del disparador aparece también en las conexiones.
    flow["connections"] = {(f"Todos los días a las {s['summary_hour']}:00" if k.startswith("Todos los días") else k): v
                           for k, v in flow["connections"].items()}
    return flow


def generate(destination, s):
    """Escribe credenciales.json y flujos/*.json listos para `n8n import:*` (lo usa el modo demo)."""
    destination = Path(destination)
    (destination / "flujos").mkdir(parents=True, exist_ok=True)
    (destination / "credenciales.json").write_text(json.dumps(credentials(s), ensure_ascii=False), encoding="utf-8")
    for path in sorted(FLOWS_DIR.glob("*.json")):
        (destination / "flujos" / path.name).write_text(
            json.dumps(render_flow(path, s), ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Instala los flujos del CRM en n8n")
    parser.add_argument("--compose", default="docker compose", help='comando de compose (por defecto "docker compose")')
    parser.add_argument("--service", default="n8n", help="nombre del servicio de n8n en docker-compose.yml")
    parser.add_argument("--solo-generar", metavar="CARPETA",
                        help="solo escribe credenciales y flujos en CARPETA, sin tocar n8n")
    args = parser.parse_args()

    s = settings(Config())
    if args.solo_generar:
        generate(args.solo_generar, s)
        print(f"Credenciales y flujos generados en {args.solo_generar}")
        return
    compose = shlex.split(args.compose)

    def run(*command, stdin=None):
        result = subprocess.run(compose + ["exec", "-T", args.service, *command], input=stdin,
                                capture_output=True, text=True)
        if result.returncode != 0:
            sys.exit(f"Falló: {' '.join(command)}\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def put(remote_path, content):
        run("sh", "-c", f"cat > {shlex.quote(remote_path)}", stdin=content)

    print("Copiando credenciales y flujos a n8n…")
    run("sh", "-c", f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}/flujos && chmod 700 {REMOTE_DIR}")
    try:
        put(f"{REMOTE_DIR}/credenciales.json", json.dumps(credentials(s), ensure_ascii=False))
        for path in sorted(FLOWS_DIR.glob("*.json")):
            put(f"{REMOTE_DIR}/flujos/{path.name}", json.dumps(render_flow(path, s), ensure_ascii=False, indent=2))
        print(run("n8n", "import:credentials", f"--input={REMOTE_DIR}/credenciales.json").strip().splitlines()[-1])
        print(run("n8n", "import:workflow", "--separate", f"--input={REMOTE_DIR}/flujos").strip().splitlines()[-1])
        for workflow_id in WORKFLOW_IDS:
            run("n8n", "publish:workflow", f"--id={workflow_id}")
        print("Flujos activados:", ", ".join(WORKFLOW_IDS))
    finally:
        run("rm", "-rf", REMOTE_DIR)  # el archivo de credenciales tenía las contraseñas en claro

    print("Reiniciando n8n para que tome los flujos activos…")
    subprocess.run(compose + ["restart", args.service], check=True, capture_output=True)
    print("Listo. Abra n8n (http://127.0.0.1:5678 en el servidor) para ver los flujos.")


if __name__ == "__main__":
    main()
