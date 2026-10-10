"""Formulario de contacto simulado para el modo demo.

Imita el formulario de opensinergia.com: al enviarlo manda el mismo correo que recibe el Gmail
de la empresa, pero a un servidor de correo de prueba. De ahí lo toma n8n y lo registra en el CRM.
"""
import os
import random
import smtplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

STATIC = Path(__file__).resolve().parent.parent / "static"
env = os.environ.get
SERVICES = ("Desarrollo de software", "Infraestructura administrada", "Marketing digital", "Consultoría tecnológica")
EXAMPLES = (
    ("Paula Cárdenas", "Floristería Bella Flor", "Marketing digital", "Queremos vender más por Instagram y tener una página sencilla."),
    ("Mateo Ríos", "Ríos Logística", "Infraestructura administrada", "Necesitamos migrar nuestro servidor a la nube con respaldos diarios."),
    ("Lucía Gómez", "Clínica Vida", "Desarrollo de software", "Buscamos un sistema de citas en línea para los pacientes."),
    ("Andrés Peña", "", "Consultoría tecnológica", "Quiero saber qué tecnología conviene para mi negocio."),
)

PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo · CRM OpenSinergia</title><link rel="icon" href="/static/logo.png"><link rel="stylesheet" href="/static/styles.css"></head>
<body>
<header class="topbar"><a class="brand" href="/"><img class="brand-logo" src="/static/logo.png" alt="" width="38" height="38">
<span class="brand-name">OpenSinergia<span class="brand-tag">DEMO</span></span></a></header>
<main class="container">
  <div class="page-head"><div><span class="eyebrow">Modo demo del CRM</span>
  <h1>Pruebe el recorrido de un lead, <span class="gradient-text">de punta a punta</span></h1>
  <p class="muted">Este formulario imita el de opensinergia.com. Todo corre en este computador, con correos de prueba.</p></div></div>
  {notice}
  <div class="detail-grid">
    <section class="panel">
      <div class="panel-head"><h2>1. Llene el formulario como si fuera un cliente</h2><a class="btn btn-sm" href="/?ejemplo=1">Usar datos de ejemplo</a></div>
      <form method="post" action="/enviar" class="form-grid">
        <label class="field span-all"><span>Nombre completo <b class="req">*</b></span><input name="nombre" required value="{nombre}"></label>
        <label class="field span-2"><span>Correo electrónico <b class="req">*</b></span><input name="correo" type="email" required value="{correo}"></label>
        <label class="field"><span>Teléfono (opcional)</span><input name="telefono" type="tel" value="{telefono}"></label>
        <label class="field span-2"><span>Empresa (opcional)</span><input name="empresa" value="{empresa}"></label>
        <label class="field"><span>Servicio de interés</span><select name="servicio">{servicios}</select></label>
        <label class="field span-all"><span>Mensaje (¿Cómo podemos ayudarte?)</span><textarea name="mensaje" rows="4">{mensaje}</textarea></label>
        <div class="form-actions span-all"><button class="btn btn-primary">Enviar formulario</button></div>
      </form>
    </section>
    <aside class="stack">
      <section class="panel">
        <h2>2. Vea qué pasa después</h2>
        <ol class="demo-steps">
          <li>El correo llega al «Gmail» de prueba y <strong>n8n</strong> lo detecta en segundos.</li>
          <li>n8n lo envía al <strong>CRM</strong>: aparece en <em>Leads → Nuevo</em>, con origen Web.</li>
          <li>n8n avisa a gerencia: el correo «Nuevo lead desde la web» aparece en la <strong>bandeja</strong>.</li>
          <li>En el CRM, pulse <em>Enviar resumen</em> (pantalla Hoy): n8n arma y envía el resumen del día.</li>
        </ol>
      </section>
      <section class="panel">
        <h2>Accesos</h2>
        <ul class="rows">
          <li class="row"><div class="row-main"><a class="row-title" href="{crm_url}" target="_blank" rel="noopener">CRM</a>
            <span class="row-sub">Usuario <strong>{crm_user}</strong> · contraseña <strong>{crm_password}</strong></span></div></li>
          <li class="row"><div class="row-main"><a class="row-title" href="{n8n_url}" target="_blank" rel="noopener">n8n (flujos)</a>
            <span class="row-sub">{n8n_email} · contraseña <strong>{n8n_password}</strong></span></div></li>
          <li class="row"><div class="row-main"><a class="row-title" href="{inbox_url}" target="_blank" rel="noopener">Bandeja de correo</a>
            <span class="row-sub">Correos que envía n8n a gerencia (avisos, resumen y alertas)</span></div></li>
        </ul>
      </section>
    </aside>
  </div>
</main>
</body></html>"""


def render(values=None, notice=""):
    values = values or {}
    selected = values.get("servicio", SERVICES[0])
    options = "".join(f'<option{" selected" if s == selected else ""}>{escape(s)}</option>' for s in SERVICES)
    return PAGE.format(
        notice=notice, servicios=options,
        **{k: escape(values.get(k, "")) for k in ("nombre", "correo", "telefono", "empresa", "mensaje")},
        crm_url=escape(env("DEMO_CRM_URL", "http://localhost:8080")), n8n_url=escape(env("DEMO_N8N_URL", "http://localhost:5678")),
        inbox_url=escape(env("DEMO_INBOX_URL", "http://localhost:8025")),
        crm_user=escape(env("DEMO_CRM_USER", "demo")), crm_password=escape(env("DEMO_CRM_PASSWORD", "demo2026")),
        n8n_email=escape(env("DEMO_N8N_EMAIL", "demo@opensinergia.demo")),
        n8n_password=escape(env("DEMO_N8N_PASSWORD", "Demo2026!")))


def example():
    name, company, service, message = random.choice(EXAMPLES)
    stamp = time.strftime("%H%M%S")
    user = name.split()[0].lower().replace("í", "i").replace("á", "a").replace("é", "e")
    return {"nombre": f"{name} (demo {stamp})", "correo": f"{user}.{stamp}@ejemplo.co",
            "telefono": f"3{random.randint(0, 2)}{random.randint(0, 9)} 555 {random.randint(1000, 9999)}",
            "empresa": company, "servicio": service, "mensaje": message}


def send_form(values):
    """Arma el correo con las mismas etiquetas que usa el formulario real (según Fermin)."""
    msg = EmailMessage()
    msg["From"] = f"Formulario OpenSinergia <{env('FORM_FROM', 'formulario@opensinergia.demo')}>"
    msg["To"] = env("FORM_TO", "gerencia@opensinergia.demo")
    msg["Subject"] = "Nuevo mensaje desde el sitio web"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="opensinergia.demo")
    msg.set_content(
        f"Nombre completo: {values['nombre']}\n"
        f"Correo Electronico: {values['correo']}\n"
        f"Telefono (Opcional): {values.get('telefono', '')}\n"
        f"Empresa (Opcional): {values.get('empresa', '')}\n"
        f"Servicio de interes: {values.get('servicio', '')}\n"
        f"Mensaje (¿Como podemos ayudarte?): {values.get('mensaje', '')}\n")
    with smtplib.SMTP(env("SMTP_HOST", "correo-entrante"), int(env("SMTP_PORT", "3025")), timeout=15) as smtp:
        smtp.send_message(msg)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/static/styles.css", "/static/logo.png"):
            kind = "text/css; charset=utf-8" if url.path.endswith(".css") else "image/png"
            return self._send(200, (STATIC / url.path.rsplit("/", 1)[1]).read_bytes(), kind)
        if url.path != "/":
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        query = parse_qs(url.query)
        notice = ""
        if "enviado" in query:
            notice = ('<div class="notice ok"><strong>Formulario enviado.</strong> En unos segundos n8n lo '
                      f'registra en el CRM como lead nuevo: <strong>{escape(query["enviado"][0])}</strong>. '
                      'Revise también la bandeja de correo.</div>')
        elif "error" in query:
            notice = f'<div class="notice danger">{escape(query["error"][0])}</div>'
        self._send(200, render(example() if "ejemplo" in query else None, notice))

    def do_POST(self):
        if urlparse(self.path).path != "/enviar":
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        length = min(int(self.headers.get("Content-Length") or 0), 100_000)
        values = {k: v[0].strip() for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        if not values.get("nombre") or "@" not in values.get("correo", ""):
            return self._send(200, render(values, '<div class="notice danger">Escriba el nombre y un correo válido.</div>'))
        try:
            send_form(values)
        except OSError as exc:
            return self._send(303, "", headers={"Location": "/?error=" + quote(f"No se pudo enviar: {exc}")})
        self._send(303, "", headers={"Location": "/?enviado=" + quote(values["nombre"])})


if __name__ == "__main__":
    port = int(env("PORT", "8090"))
    print(f"Formulario de demo en http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
