"""Servidor web: entrega el front y expone la API JSON del CRM."""
import hmac
import json
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from config import BASE_DIR
from mailparse import email_bytes_from_n8n, parse_form_email_at
from notify import build_summary, record_summary_sent, summary_counts
from store import PERIOD_MONTHS, PLANS, SOURCES, STAGE_LABELS, Duplicate, Invalid, NotFound

STATIC_DIR = BASE_DIR / "static"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "logo.png": "image/png",
}
COOKIE_NAME = "crm_session"
MAX_BODY = 1_000_000
MAX_BODY_N8N = 8_000_000  # correos con HTML pesado
LOGIN_WINDOW, LOGIN_MAX_FAILURES = 15 * 60, 8
APP_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"


class HttpError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra or {}


class Raw:
    """Respuesta que no es JSON (CSV, HTML)."""

    def __init__(self, body, content_type, headers=None, csp=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.content_type, self.headers, self.csp = content_type, headers or {}, csp


class Ctx:
    def __init__(self, app, handler, query, body, user):
        self.app, self.handler, self.query, self.body, self.user = app, handler, query, body, user
        self.store, self.cfg, self.jobs = app.store, app.cfg, app.jobs

    @property
    def username(self):
        return self.user["username"] if self.user else ""

    def q(self, key, default=None):
        return self.query.get(key, [default])[0]


ROUTES = []


def route(method, pattern, auth=True):
    """auth: True = sesión del front, False = pública, "token" = flujos de n8n (Authorization: Bearer)."""
    def register(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, auth))
        return fn
    return register


# ---------------------------------------------------------------- autenticación

@route("POST", "/api/login", auth=False)
def login(ctx):
    ip = ctx.handler.client_ip()
    if ctx.app.login_blocked(ip):
        raise HttpError(429, "Demasiados intentos fallidos. Espere unos minutos.")
    user = ctx.store.check_login(str(ctx.body.get("username", "")), str(ctx.body.get("password", "")))
    if not user:
        ctx.app.login_failed(ip)
        raise HttpError(401, "Usuario o contraseña incorrectos")
    token = ctx.store.create_session(user["id"])
    ctx.handler.set_cookie(token, max_age=14 * 24 * 3600)
    return {"username": user["username"]}


@route("POST", "/api/logout")
def logout(ctx):
    ctx.store.delete_session(ctx.handler.session_token())
    ctx.handler.set_cookie("", max_age=0)
    return {"ok": True}


@route("GET", "/api/me")
def me(ctx):
    return {
        "username": ctx.username,
        "options": {"sources": SOURCES, "stages": STAGE_LABELS, "plans": PLANS, "periods": list(PERIOD_MONTHS)},
        "status": ctx.jobs.status(),
    }


# -------------------------------------------------------------------- consultas

@route("GET", "/api/today")
def today(ctx):
    data = ctx.store.today_summary()
    data["status"] = ctx.jobs.status()
    return data


@route("GET", "/api/search")
def search(ctx):
    return ctx.store.search(ctx.q("q", ""))


@route("GET", "/api/leads")
def leads(ctx):
    return ctx.store.list_leads()


@route("GET", "/api/clients")
def clients(ctx):
    return ctx.store.list_clients()


@route("GET", r"/api/contacts/(?P<cid>\d+)")
def contact_detail(ctx, cid):
    return ctx.store.get_contact(int(cid))


@route("GET", "/api/kpis")
def kpis(ctx):
    return ctx.store.kpis(ctx.q("desde") or None)


@route("GET", r"/api/export/(?P<name>contactos|servicios|pagos|eventos)\.csv")
def export(ctx, name):
    stamp = ctx.store.today().isoformat()
    return Raw(ctx.store.export_csv(name), "text/csv; charset=utf-8",
               {"Content-Disposition": f'attachment; filename="crm-{name}-{stamp}.csv"'})


@route("GET", "/api/summary/preview")
def summary_preview(ctx):
    _, _, html_body = build_summary(ctx.store.today_summary(), ctx.cfg.app_url)
    return Raw("<!doctype html><meta charset='utf-8'><title>Vista previa del resumen</title>" + html_body,
               "text/html; charset=utf-8", csp=PREVIEW_CSP)


# ---------------------------------------------------------------------- cambios

@route("POST", "/api/contacts")
def contact_create(ctx):
    contact_id = ctx.store.create_contact(ctx.body, ctx.username, force=bool(ctx.body.get("force")))
    return 201, {"id": contact_id}


@route("PATCH", r"/api/contacts/(?P<cid>\d+)")
def contact_update(ctx, cid):
    ctx.store.update_contact(int(cid), ctx.body, ctx.username)
    return ctx.store.get_contact(int(cid))


@route("DELETE", r"/api/contacts/(?P<cid>\d+)")
def contact_delete(ctx, cid):
    ctx.store.delete_contact(int(cid))
    return {"ok": True}


@route("POST", r"/api/contacts/(?P<cid>\d+)/notes")
def contact_note(ctx, cid):
    ctx.store.add_note(int(cid), ctx.body.get("text", ""), ctx.username)
    return ctx.store.get_contact(int(cid))


@route("POST", r"/api/contacts/(?P<cid>\d+)/services")
def service_create(ctx, cid):
    ctx.store.create_service(int(cid), ctx.body, ctx.username)
    return 201, ctx.store.get_contact(int(cid))


@route("PATCH", r"/api/services/(?P<sid>\d+)")
def service_update(ctx, sid):
    ctx.store.update_service(int(sid), ctx.body, ctx.username)
    return {"ok": True}


@route("POST", r"/api/services/(?P<sid>\d+)/payments")
def payment_create(ctx, sid):
    ctx.store.register_payment(int(sid), ctx.body, ctx.username)
    return 201, {"ok": True}


@route("PUT", "/api/kpis/manual")
def kpi_manual(ctx):
    ctx.store.set_manual_kpi(ctx.body.get("key"), ctx.body.get("value"))
    return ctx.store.kpis(ctx.body.get("desde") or None)


@route("POST", "/api/jobs/gmail")
def job_gmail(ctx):
    if not ctx.cfg.gmail_enabled:
        raise HttpError(400, "Gmail no está configurado. Revise GMAIL_USER, GMAIL_APP_PASSWORD y GMAIL_SEARCH en .env")
    try:
        return ctx.jobs.run_gmail()
    except Exception as exc:
        raise HttpError(502, f"No se pudo leer Gmail: {exc}")


@route("POST", "/api/jobs/summary")
def job_summary(ctx):
    if ctx.cfg.n8n_summary_webhook:
        # El envío lo hace el flujo "Resumen diario" de n8n; el CRM solo lo dispara.
        request = urllib.request.Request(
            ctx.cfg.n8n_summary_webhook, data=json.dumps({"requested_by": ctx.username}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ctx.cfg.n8n_token}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as res:
                res.read()
        except urllib.error.HTTPError as exc:
            hint = " (¿el flujo está activo?)" if exc.code == 404 else ""
            raise HttpError(502, f"n8n respondió {exc.code}{hint}")
        except Exception as exc:
            raise HttpError(502, f"No se pudo contactar a n8n: {exc}")
        return {"via": "n8n", "to": ctx.cfg.summary_to}
    if not ctx.cfg.smtp_enabled:
        raise HttpError(400, "El envío del resumen no está configurado (n8n o SMTP en .env). Use la vista previa.")
    try:
        return {"via": "crm", **ctx.jobs.run_summary(ctx.username)}
    except Exception as exc:
        raise HttpError(502, f"No se pudo enviar el resumen: {exc}")


# -------------------------------------------------------------- integración n8n

@route("POST", "/api/n8n/form-email", auth="token")
def n8n_form_email(ctx):
    """Flujo "Leads del formulario web": recibe el correo leído por el nodo IMAP y crea o actualiza el lead."""
    try:
        raw = email_bytes_from_n8n(ctx.body)
    except ValueError as exc:
        raise HttpError(400, str(exc))
    lead = parse_form_email_at(raw, ctx.cfg.own_addresses, ctx.cfg.tz)
    capture = "auto"
    if ctx.store.now() - ctx.store.parse_dt(lead["received_at"]) > timedelta(hours=ctx.cfg.n8n_import_after_hours):
        capture = "importado"  # correo viejo: es historial, no cuenta para el tiempo de registro
    status, contact_id = ctx.store.ingest_form_lead(lead, capture=capture)
    result = {
        "status": status, "contact_id": contact_id, "capture": capture, "name": lead["name"],
        "email": lead["email"], "phone": lead["phone"], "interest": lead["interest"],
        "needs_review": lead["needs_review"],
        "url": f"{ctx.cfg.app_url}/#/contacto/{contact_id}" if ctx.cfg.app_url and contact_id else "",
        # Para el aviso que envía n8n: la configuración de destinatarios vive solo en el .env del CRM.
        "notify_to": ", ".join(ctx.cfg.summary_to), "notify_from": ctx.cfg.summary_from,
    }
    if status != "repetido":
        ctx.store.set_meta("n8n_last_ingest", json.dumps({"at": ctx.store.now_iso(), **result}, ensure_ascii=False))
    return result


@route("GET", "/api/n8n/summary", auth="token")
def n8n_summary(ctx):
    """Flujo "Resumen diario": entrega el correo ya armado para que n8n lo envíe."""
    data = ctx.store.today_summary()
    subject, text, html_body = build_summary(data, ctx.cfg.app_url)
    return {"date": data["date"], "to": ", ".join(ctx.cfg.summary_to), "from": ctx.cfg.summary_from,
            "subject": subject, "text": text, "html": html_body, "counts": summary_counts(data)}


@route("POST", "/api/n8n/summary-sent", auth="token")
def n8n_summary_sent(ctx):
    data = ctx.store.today_summary()
    record_summary_sent(ctx.store, data, str(ctx.body.get("to") or ", ".join(ctx.cfg.summary_to))[:300], "n8n")
    return {"ok": True, "date": data["date"]}


# ----------------------------------------------------------------- servidor HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "CRM"
    sys_version = ""
    app = None  # se asigna en App.make_server

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, fmt, *args):
        if self.app.verbose:
            super().log_message(fmt, *args)

    # --- utilidades ---

    def client_ip(self):
        if self.app.cfg.trust_proxy and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def session_token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return ""
        return jar[COOKIE_NAME].value if COOKIE_NAME in jar else ""

    def set_cookie(self, value, max_age):
        parts = [f"{COOKIE_NAME}={value}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
        if self.app.cfg.cookie_secure:
            parts.append("Secure")
        self._pending_headers.append(("Set-Cookie", "; ".join(parts)))

    def _read_json(self, limit=MAX_BODY):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise HttpError(413, "La solicitud es demasiado grande")
        if not length:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise HttpError(415, "Se esperaba JSON")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "JSON inválido")
        if not isinstance(data, dict):
            raise HttpError(400, "Se esperaba un objeto JSON")
        return data

    def _check_token(self):
        token = self.app.cfg.n8n_token
        if not token:
            raise HttpError(503, "La integración con n8n no está configurada (N8N_API_TOKEN en .env)")
        ip = "token:" + self.client_ip()
        if self.app.login_blocked(ip):
            raise HttpError(429, "Demasiados intentos con token inválido")
        header = self.headers.get("Authorization", "")
        given = header[7:].strip() if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
            self.app.login_failed(ip)
            raise HttpError(401, "Token inválido")

    def _send(self, status, body, content_type, extra_headers=None, csp=APP_CSP, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in self._pending_headers + list((extra_headers or {}).items()):
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_static(self, name):
        path = STATIC_DIR / name
        content_type = "text/html; charset=utf-8" if name == "index.html" else STATIC_FILES[name]
        self._send(200, path.read_bytes(), content_type, cache="no-cache")

    def _dispatch(self, method):
        self._pending_headers = []
        url = urlparse(self.path)
        path = url.path
        try:
            if method == "GET" and path in ("/", "/index.html"):
                return self._send_static("index.html")
            if method == "GET" and path.startswith("/static/"):
                name = path[len("/static/"):]
                if name in STATIC_FILES:
                    return self._send_static(name)
                raise HttpError(404, "No encontrado")
            for route_method, pattern, fn, auth in ROUTES:
                match = pattern.match(path)
                if not match or route_method != method:
                    continue
                body = {}
                if auth == "token":
                    self._check_token()
                    user = {"id": None, "username": "n8n"}
                    if method != "GET":
                        body = self._read_json(MAX_BODY_N8N)
                else:
                    if method != "GET":
                        # Cabecera propia: un formulario de otro sitio no la puede enviar sin permiso CORS (anti-CSRF).
                        if self.headers.get("X-Requested-With") != "crm":
                            raise HttpError(403, "Solicitud rechazada")
                        body = self._read_json()
                    user = self.app.store.session_user(self.session_token()) if auth else None
                    if auth and not user:
                        raise HttpError(401, "Inicie sesión")
                result = fn(Ctx(self.app, self, parse_qs(url.query), body, user), **match.groupdict())
                status = 200
                if isinstance(result, tuple):
                    status, result = result
                if isinstance(result, Raw):
                    return self._send(status, result.body, result.content_type, result.headers,
                                      csp=result.csp or APP_CSP)
                return self._send_json(status, result)
            raise HttpError(404, "No encontrado")
        except HttpError as exc:
            self._send_json(exc.status, {"error": exc.message, **exc.extra})
        except Duplicate as exc:
            self._send_json(409, {"error": str(exc), "duplicate": exc.contact})
        except Invalid as exc:
            self._send_json(400, {"error": str(exc)})
        except NotFound as exc:
            self._send_json(404, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._send_json(500, {"error": "Error interno del servidor"})
            except Exception:
                pass


class App:
    def __init__(self, store, cfg, jobs, verbose=False):
        self.store, self.cfg, self.jobs, self.verbose = store, cfg, jobs, verbose
        self._failures = defaultdict(deque)
        self._failures_lock = threading.Lock()

    def login_blocked(self, ip):
        with self._failures_lock:
            attempts = self._failures[ip]
            while attempts and attempts[0] < time.monotonic() - LOGIN_WINDOW:
                attempts.popleft()
            return len(attempts) >= LOGIN_MAX_FAILURES

    def login_failed(self, ip):
        with self._failures_lock:
            self._failures[ip].append(time.monotonic())

    def make_server(self, host=None, port=None):
        handler = type("BoundHandler", (Handler,), {"app": self})
        server = ThreadingHTTPServer((host or self.cfg.host, self.cfg.port if port is None else port), handler)
        server.daemon_threads = True
        return server
