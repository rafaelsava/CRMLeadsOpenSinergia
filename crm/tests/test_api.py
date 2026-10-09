import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from config import Config
from jobs import Jobs
from server import App
from store import Store


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Config(data_dir=Path(cls.tmp.name), gmail_user="", gmail_app_password="", gmail_search="",
                     smtp_password="", port=0)
        cls.store = Store(cfg.db_path, cfg.tz)
        cls.store.set_user("rafael", "una-clave-segura")
        cls.server = App(cls.store, cfg, Jobs(cls.store, cfg, log=lambda *_: None)).make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, csrf=True):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-Requested-With"] = "crm"
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.headers, err.read()

    def login(self):
        status, headers, _ = self.request("POST", "/api/login", {"username": "rafael", "password": "una-clave-segura"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";")[0]

    def test_front_is_served_with_security_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/static/../config.py")[0], 404)
        self.assertEqual(self.request("GET", "/static/%2e%2e/config.py")[0], 404)

    def test_api_requires_login(self):
        self.assertEqual(self.request("GET", "/api/today")[0], 401)
        self.assertEqual(self.request("GET", "/api/export/contactos.csv")[0], 401)
        status, _, _ = self.request("POST", "/api/login", {"username": "rafael", "password": "mala"})
        self.assertEqual(status, 401)

    def test_writes_without_the_custom_header_are_rejected(self):
        cookie = self.login()
        status, _, _ = self.request("POST", "/api/contacts", {"name": "X"}, cookie=cookie, csrf=False)
        self.assertEqual(status, 403)

    def test_full_flow_lead_to_paid_client(self):
        cookie = self.login()
        status, _, body = self.request("POST", "/api/contacts", {"name": "Lead <script>", "phone": "3105550000",
                                                                 "source": "WhatsApp"}, cookie=cookie)
        self.assertEqual(status, 201)
        cid = json.loads(body)["id"]
        status, _, body = self.request("POST", "/api/contacts", {"name": "Copia", "phone": "310 555 0000"}, cookie=cookie)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["duplicate"]["id"], cid)

        status, _, body = self.request("PATCH", f"/api/contacts/{cid}", {"stage": "propuesta", "next_action": "Llamar",
                                                                       "next_action_date": "2026-13-40"}, cookie=cookie)
        self.assertEqual(status, 400)
        status, _, body = self.request("POST", f"/api/contacts/{cid}/services", {
            "name": "VPS", "base_price": 300000, "discount_pct": 20, "next_due_date": "2026-10-01"}, cookie=cookie)
        self.assertEqual(status, 201)
        contact = json.loads(body)
        self.assertEqual(contact["kind"], "cliente")
        service = contact["services"][0]
        self.assertEqual(service["final_price"], 240000)
        status, _, _ = self.request("POST", f"/api/services/{service['id']}/payments", {"paid_date": "2026-10-02"},
                                    cookie=cookie)
        self.assertEqual(status, 201)
        _, _, body = self.request("GET", f"/api/contacts/{cid}", cookie=cookie)
        self.assertEqual(json.loads(body)["services"][0]["next_due_date"], "2026-11-01")

        status, headers, body = self.request("GET", "/api/export/pagos.csv", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("240000", body.decode("utf-8"))
        for path in ("/api/today", "/api/leads", "/api/clients", "/api/kpis", "/api/search?q=lead", "/api/me"):
            self.assertEqual(self.request("GET", path, cookie=cookie)[0], 200, path)

    def test_summary_preview_escapes_lead_data(self):
        cookie = self.login()
        self.request("POST", "/api/contacts", {"name": "<img src=x onerror=alert(1)>", "email": "xss@x.co"},
                     cookie=cookie)
        status, headers, body = self.request("GET", "/api/summary/preview", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body.decode("utf-8"))
        self.assertNotIn("script", headers["Content-Security-Policy"])

    def test_jobs_report_missing_configuration(self):
        cookie = self.login()
        status, _, body = self.request("POST", "/api/jobs/gmail", {}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("Gmail no está configurado", json.loads(body)["error"])
        self.assertEqual(self.request("POST", "/api/jobs/summary", {}, cookie=cookie)[0], 400)

    def test_logout_invalidates_the_session(self):
        cookie = self.login()
        self.assertEqual(self.request("POST", "/api/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/today", cookie=cookie)[0], 401)


if __name__ == "__main__":
    unittest.main()


class N8nApiTest(unittest.TestCase):
    """Endpoints que usan los flujos de n8n (token en Authorization: Bearer)."""

    TOKEN = "token-de-prueba-0123456789"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Config(data_dir=Path(cls.tmp.name), gmail_user="", gmail_app_password="", gmail_search="",
                     smtp_password="", port=0, n8n_token=cls.TOKEN, summary_to=["gerencia@opensinergia.com"],
                     summary_from="crm@opensinergia.com", app_url="https://crm.example")
        cls.store = Store(cfg.db_path, cfg.tz)
        cls.server = App(cls.store, cfg, Jobs(cls.store, cfg, log=lambda *_: None)).make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, token=TOKEN):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read())

    def resolved_email(self, **overrides):
        """Forma del item que entrega el nodo Email Trigger (IMAP) de n8n en formato "resolved"."""
        email = {
            "messageId": "<n8n-1@opensinergia.com>", "subject": "Nuevo mensaje desde el sitio web",
            "date": "2026-10-09T15:00:00.000Z",
            "from": {"value": [{"address": "formulario@opensinergia.com", "name": "Formulario"}],
                     "text": "Formulario <formulario@opensinergia.com>"},
            "text": "Nombre completo: Julián Pérez\nCorreo Electronico: julian@ejemplo.co\n"
                    "Telefono (Opcional): 315 555 0142\nServicio de interes: Marketing digital\n"
                    "Mensaje (¿Como podemos ayudarte?): Pauta en Instagram",
            "html": False, "attributes": {"uid": 7},
        }
        email.update(overrides)
        return email

    def test_requires_a_valid_token(self):
        self.assertEqual(self.call("GET", "/api/n8n/summary", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/n8n/summary", token="otro")[0], 401)
        self.assertEqual(self.call("GET", "/api/today", token=self.TOKEN)[0], 401)  # el token no abre el front

    def test_form_email_creates_the_lead_once(self):
        status, body = self.call("POST", "/api/n8n/form-email", self.resolved_email())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "creado")
        self.assertEqual((body["name"], body["email"], body["interest"]), ("Julián Pérez", "julian@ejemplo.co", "Marketing digital"))
        self.assertEqual(body["notify_to"], "gerencia@opensinergia.com")
        self.assertTrue(body["url"].startswith("https://crm.example/#/contacto/"))
        self.assertEqual(self.call("POST", "/api/n8n/form-email", self.resolved_email())[1]["status"], "repetido")
        lead = self.store.get_contact(body["contact_id"])
        self.assertEqual(lead["source"], "Web")
        self.assertIn(lead["capture"], ("auto", "importado"))

    def test_old_emails_are_marked_as_imported_history(self):
        _, body = self.call("POST", "/api/n8n/form-email", self.resolved_email(
            messageId="<viejo@opensinergia.com>", date="2026-01-05T15:00:00.000Z",
            text="Nombre completo: Ana Vieja\nCorreo Electronico: ana.vieja@ejemplo.co"))
        self.assertEqual(body["capture"], "importado")

    def test_form_email_without_body_is_rejected(self):
        status, body = self.call("POST", "/api/n8n/form-email", {"subject": "vacío", "text": "", "html": False})
        self.assertEqual(status, 400)

    def test_summary_and_confirmation(self):
        status, body = self.call("GET", "/api/n8n/summary")
        self.assertEqual(status, 200)
        self.assertEqual(body["to"], "gerencia@opensinergia.com")
        self.assertEqual(body["from"], "crm@opensinergia.com")
        self.assertIn("Resumen del CRM", body["html"])
        status, body = self.call("POST", "/api/n8n/summary-sent", {"to": "gerencia@opensinergia.com"})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get_meta("summary_last_date"), self.store.today().isoformat())
