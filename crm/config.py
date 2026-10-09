"""Configuración del CRM: variables de entorno y, si existe, el archivo .env."""
import os
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path):
    """Carga KEY=VALUE desde un archivo .env sin pisar variables ya definidas."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def get_tz(name):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        # Sin base de zonas horarias (p. ej. imágenes Docker mínimas): Colombia es UTC-5 todo el año.
        return timezone(timedelta(hours=-5), "COT")


def _csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


class Config:
    def __init__(self, **overrides):
        load_dotenv(BASE_DIR / ".env")
        env = os.environ.get

        self.host = env("HOST", "127.0.0.1")
        self.port = int(env("PORT", "8080"))
        self.data_dir = Path(env("DATA_DIR", str(BASE_DIR / "data")))
        self.tz_name = env("CRM_TZ", "America/Bogota")
        self.cookie_secure = env("COOKIE_SECURE", "0") == "1"
        self.trust_proxy = env("TRUST_PROXY", "0") == "1"
        self.app_url = env("APP_URL", "").rstrip("/")

        # Gmail (lectura por IMAP con contraseña de aplicación).
        self.gmail_user = env("GMAIL_USER", "")
        self.gmail_app_password = env("GMAIL_APP_PASSWORD", "").replace(" ", "")
        self.gmail_search = env("GMAIL_SEARCH", "")
        self.gmail_poll_minutes = int(env("GMAIL_POLL_MINUTES", "5"))
        self.gmail_backfill_days = int(env("GMAIL_BACKFILL_DAYS", "30"))

        # Resumen diario por correo (SMTP; por defecto la misma cuenta de Gmail).
        self.smtp_host = env("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(env("SMTP_PORT", "465"))
        self.smtp_user = env("SMTP_USER", self.gmail_user)
        self.smtp_password = env("SMTP_PASSWORD", self.gmail_app_password).replace(" ", "")
        self.summary_to = _csv(env("SUMMARY_TO", self.gmail_user))
        self.summary_from = env("SUMMARY_FROM", self.smtp_user)
        self.summary_hour = int(env("SUMMARY_HOUR", "7"))

        # Integración con n8n: los flujos llaman al CRM con este token (Authorization: Bearer ...).
        self.n8n_token = env("N8N_API_TOKEN", "")
        # Webhook del flujo "Resumen diario" para el botón "Enviar resumen" del CRM.
        self.n8n_summary_webhook = env("N8N_SUMMARY_WEBHOOK", "")
        # Un correo que llega al CRM con más de N horas de antigüedad se toma como historial importado.
        self.n8n_import_after_hours = int(env("N8N_IMPORT_AFTER_HOURS", "24"))

        # Reglas de alerta.
        self.due_soon_days = int(env("DUE_SOON_DAYS", "3"))
        self.stale_lead_hours = int(env("STALE_LEAD_HOURS", "48"))

        for key, value in overrides.items():
            setattr(self, key, value)
        self.tz = get_tz(self.tz_name)

    @property
    def db_path(self):
        return self.data_dir / "crm.db"

    @property
    def gmail_enabled(self):
        return bool(self.gmail_user and self.gmail_app_password and self.gmail_search)

    @property
    def smtp_enabled(self):
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.summary_to)

    @property
    def n8n_enabled(self):
        return bool(self.n8n_token)

    @property
    def own_addresses(self):
        return {a.lower() for a in (self.gmail_user, self.smtp_user, self.summary_from) if a}
