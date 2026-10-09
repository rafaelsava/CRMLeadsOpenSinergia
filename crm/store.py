"""Base de datos (SQLite) y reglas de negocio del CRM."""
import calendar
import csv
import hashlib
import hmac
import io
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

STAGES = ("nuevo", "contactado", "propuesta", "ganado", "perdido")
OPEN_STAGES = ("nuevo", "contactado", "propuesta")
STAGE_LABELS = {"nuevo": "Nuevo", "contactado": "Contactado", "propuesta": "Propuesta",
                "ganado": "Ganado", "perdido": "Perdido"}
SOURCES = ("Web", "WhatsApp", "Instagram", "Facebook", "Referido", "Otro")
PLANS = ("estandar", "especial")
PERIOD_MONTHS = {"mensual": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12, "unico": 0}
SESSION_DAYS = 14
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

# Línea base AS-IS (Entregable 1, sección 2.5) y metas del MVP (sección 5.4).
BASELINE = {
    "registro_min": 60, "horas_semana": 5, "localizar_min": 45,
    "transcripcion_pct": 100, "seguimiento_pct": 0, "clientes_vencidos": 4,
}
MANUAL_KPIS = ("localizar_min", "horas_semana")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'prospecto' CHECK (kind IN ('prospecto', 'cliente')),
    name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    phone_norm TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'Otro',
    stage TEXT NOT NULL DEFAULT 'nuevo' CHECK (stage IN ('nuevo', 'contactado', 'propuesta', 'ganado', 'perdido')),
    interest TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    raw_email TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    next_action_date TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    capture TEXT NOT NULL DEFAULT 'manual' CHECK (capture IN ('manual', 'auto', 'importado')),
    received_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    converted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts (email);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts (phone_norm);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'estandar' CHECK (plan IN ('estandar', 'especial')),
    base_price REAL NOT NULL DEFAULT 0,
    discount_pct REAL NOT NULL DEFAULT 0,
    conditions TEXT NOT NULL DEFAULT '',
    period TEXT NOT NULL DEFAULT 'mensual',
    billing_day INTEGER,
    next_due_date TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    due_date TEXT,
    paid_date TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_contact ON events (contact_id, created_at);
CREATE TABLE IF NOT EXISTS inbound_messages (
    message_id TEXT PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    subject TEXT NOT NULL DEFAULT '',
    received_at TEXT,
    status TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Invalid(ValueError):
    """Dato inválido enviado por el usuario (HTTP 400)."""


class NotFound(LookupError):
    """Registro inexistente (HTTP 404)."""


class Duplicate(Exception):
    """Ya existe un contacto con el mismo correo o teléfono (HTTP 409)."""

    def __init__(self, contact):
        super().__init__("Ya existe un contacto con ese correo o teléfono")
        self.contact = contact


# ----------------------------------------------------------------- utilidades

def norm_email(value):
    return (value or "").strip().lower()


def norm_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def parse_date(value, field="fecha", required=False):
    if value in (None, ""):
        if required:
            raise Invalid(f"Falta la {field}")
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise Invalid(f"La {field} no es válida (use AAAA-MM-DD)")


def parse_money(value, field="valor"):
    if value in (None, ""):
        return 0.0
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        raise Invalid(f"El {field} no es un número")
    if number < 0:
        raise Invalid(f"El {field} no puede ser negativo")
    return round(number, 2)


def add_months(day, months, anchor_day=None):
    """Suma meses respetando el día de cobro (31 → 30/28 sin correrse en los meses siguientes)."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    wanted = anchor_day or day.day
    return date(year, month, min(wanted, calendar.monthrange(year, month)[1]))


def final_price(service):
    return round(service["base_price"] * (1 - service["discount_pct"] / 100.0), 2)


def fmt_money(value):
    return "$" + f"{value:,.0f}".replace(",", ".")


def fmt_date(value):
    if not value:
        return "sin fecha"
    d = date.fromisoformat(str(value)[:10])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _clean(value, limit=500):
    return str(value or "").strip()[:limit]


def _row(row):
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------- store

class Store:
    def __init__(self, db_path, tz, due_soon_days=3, stale_lead_hours=48):
        self.db_path = str(db_path)
        self.tz = tz
        self.due_soon_days = due_soon_days
        self.stale_lead_hours = stale_lead_hours
        with self.tx() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    # --- conexión y tiempo -------------------------------------------------

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def tx(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def now(self):
        return datetime.now(self.tz).replace(microsecond=0)

    def now_iso(self):
        return self.now().isoformat()

    def today(self):
        return self.now().date()

    def parse_dt(self, value):
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo else moment.replace(tzinfo=self.tz)

    # --- meta ----------------------------------------------------------------

    def get_meta(self, key, default=None):
        with self.tx() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        with self.tx() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    # --- usuarios y sesiones -------------------------------------------------

    @staticmethod
    def _hash_password(password, salt=None, iterations=240_000):
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"

    def set_user(self, username, password):
        username = _clean(username, 60).lower()
        if not re.fullmatch(r"[a-z0-9._-]{2,60}", username):
            raise Invalid("Usuario inválido: use letras, números, punto o guion")
        if len(password) < 8:
            raise Invalid("La contraseña debe tener al menos 8 caracteres")
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash",
                (username, self._hash_password(password), self.now_iso()))

    def count_users(self):
        with self.tx() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def check_login(self, username, password):
        with self.tx() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?",
                                (_clean(username, 60).lower(),)).fetchone()
        if not user:
            self._hash_password(password)  # mismo costo para no revelar si el usuario existe
            return None
        _, iterations, salt_hex, _ = user["password_hash"].split("$")
        candidate = self._hash_password(password, bytes.fromhex(salt_hex), int(iterations))
        return _row(user) if hmac.compare_digest(candidate, user["password_hash"]) else None

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = (self.now() + timedelta(days=SESSION_DAYS)).isoformat()
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (self.now_iso(),))
            conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                         (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        return token

    def session_user(self, token):
        if not token:
            return None
        with self.tx() as conn:
            row = conn.execute(
                "SELECT users.id, users.username, sessions.expires_at FROM sessions "
                "JOIN users ON users.id = sessions.user_id WHERE token_hash = ?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row["expires_at"] < self.now_iso():
            return None
        return {"id": row["id"], "username": row["username"]}

    def delete_session(self, token):
        with self.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                         (hashlib.sha256((token or "").encode()).hexdigest(),))

    # --- eventos (historial y fuente de los KPIs) ----------------------------

    def _event(self, conn, contact_id, type_, detail, username=""):
        conn.execute("INSERT INTO events (contact_id, type, detail, username, created_at) VALUES (?, ?, ?, ?, ?)",
                     (contact_id, type_, detail, username or "", self.now_iso()))

    def log_event(self, contact_id, type_, detail, username=""):
        with self.tx() as conn:
            self._event(conn, contact_id, type_, detail, username)

    # --- contactos -----------------------------------------------------------

    def _get_contact(self, conn, contact_id):
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            raise NotFound("El contacto no existe")
        return row

    def _find_duplicate(self, conn, email, phone, exclude_id=None):
        email, phone = norm_email(email), norm_phone(phone)
        clauses, params = [], []
        if email:
            clauses.append("email = ?")
            params.append(email)
        if len(phone) >= 7:
            clauses.append("phone_norm = ?")
            params.append(phone)
        if not clauses:
            return None
        sql = f"SELECT id, name, company, kind, stage FROM contacts WHERE ({' OR '.join(clauses)})"
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        return _row(conn.execute(sql + " ORDER BY id LIMIT 1", params).fetchone())

    def _contact_values(self, data, kind):
        values = {
            "name": _clean(data.get("name"), 200),
            "company": _clean(data.get("company"), 200),
            "phone": _clean(data.get("phone"), 50),
            "email": norm_email(data.get("email"))[:200],
            "source": data.get("source") if data.get("source") in SOURCES else "Otro",
            "interest": _clean(data.get("interest"), 200),
            "message": _clean(data.get("message"), 8000),
            "next_action": _clean(data.get("next_action"), 300),
            "next_action_date": parse_date(data.get("next_action_date"), "fecha de la próxima acción"),
        }
        if values["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"]):
            raise Invalid("El correo no es válido")
        if not (values["name"] or values["email"] or values["phone"]):
            raise Invalid("Escriba al menos el nombre, el correo o el teléfono")
        if kind == "cliente" and not values["name"]:
            raise Invalid("El cliente necesita un nombre")
        return values

    def create_contact(self, data, username="", force=False):
        kind = data.get("kind") if data.get("kind") in ("prospecto", "cliente") else "prospecto"
        values = self._contact_values(data, kind)
        now = self.now_iso()
        with self.tx() as conn:
            if not force:
                existing = self._find_duplicate(conn, values["email"], values["phone"])
                if existing:
                    raise Duplicate(existing)
            cur = conn.execute(
                """INSERT INTO contacts (kind, name, company, phone, phone_norm, email, source, stage, interest,
                       message, next_action, next_action_date, capture, received_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)""",
                (kind, values["name"], values["company"], values["phone"], norm_phone(values["phone"]),
                 values["email"], values["source"], "ganado" if kind == "cliente" else "nuevo",
                 values["interest"], values["message"], values["next_action"], values["next_action_date"],
                 now, now, now))
            contact_id = cur.lastrowid
            if kind == "cliente":
                self._event(conn, contact_id, "cliente_creado", "Cliente registrado en el CRM", username)
            else:
                self._event(conn, contact_id, "lead_creado",
                            f"Lead registrado manualmente (origen: {values['source']})", username)
            if values["next_action"] or values["next_action_date"]:
                self._event(conn, contact_id, "seguimiento", self._action_text(values), username)
            service = data.get("service")
            if service and _clean(service.get("name")):
                self._insert_service(conn, contact_id, service, username)
        return contact_id

    @staticmethod
    def _action_text(values):
        action = values.get("next_action") or "Seguimiento"
        when = values.get("next_action_date")
        return f"Próxima acción: {action}" + (f" ({fmt_date(when)})" if when else "")

    def update_contact(self, contact_id, data, username=""):
        with self.tx() as conn:
            current = self._get_contact(conn, contact_id)
            updates, changed = {}, []
            labels = {"name": "nombre", "company": "empresa", "phone": "teléfono", "email": "correo",
                      "source": "origen", "interest": "servicio de interés"}
            if any(k in data for k in labels):
                merged = {k: data.get(k, current[k]) for k in labels}
                merged.update(next_action=current["next_action"], next_action_date=current["next_action_date"])
                values = self._contact_values(merged, current["kind"])
                for key, label in labels.items():
                    if values[key] != current[key]:
                        updates[key] = values[key]
                        changed.append(label)
                if "phone" in updates:
                    updates["phone_norm"] = norm_phone(updates["phone"])

            if "next_action" in data or "next_action_date" in data:
                action = _clean(data.get("next_action", current["next_action"]), 300)
                when = parse_date(data.get("next_action_date", current["next_action_date"]),
                                  "fecha de la próxima acción")
                if data.get("action_done") and current["next_action"]:
                    self._event(conn, contact_id, "accion_hecha", f"Hecho: {current['next_action']}", username)
                if (action, when) != (current["next_action"], current["next_action_date"]):
                    updates["next_action"], updates["next_action_date"] = action, when
                    if action or when:
                        self._event(conn, contact_id, "seguimiento",
                                    self._action_text({"next_action": action, "next_action_date": when}), username)

            if "needs_review" in data and bool(data["needs_review"]) != bool(current["needs_review"]):
                updates["needs_review"] = 1 if data["needs_review"] else 0
                if not data["needs_review"]:
                    self._event(conn, contact_id, "revisado", "Datos del formulario revisados", username)

            stage = data.get("stage")
            if stage and stage != current["stage"] and current["kind"] == "prospecto":
                if stage not in STAGES:
                    raise Invalid("Estado inválido")
                updates["stage"] = stage
                self._event(conn, contact_id, "estado",
                            f"{STAGE_LABELS[current['stage']]} → {STAGE_LABELS[stage]}", username)
                if stage == "ganado":
                    updates.update(kind="cliente", converted_at=self.now_iso())
                    self._event(conn, contact_id, "convertido", "Convertido en cliente", username)

            if changed:
                self._event(conn, contact_id, "datos", "Actualizó " + ", ".join(changed), username)
            if updates:
                updates["updated_at"] = self.now_iso()
                assignments = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(f"UPDATE contacts SET {assignments} WHERE id = ?", (*updates.values(), contact_id))

    def delete_contact(self, contact_id):
        with self.tx() as conn:
            self._get_contact(conn, contact_id)
            conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    def add_note(self, contact_id, text, username=""):
        text = _clean(text, 4000)
        if not text:
            raise Invalid("La nota está vacía")
        with self.tx() as conn:
            self._get_contact(conn, contact_id)
            self._event(conn, contact_id, "nota", text, username)
            conn.execute("UPDATE contacts SET updated_at = ? WHERE id = ?", (self.now_iso(), contact_id))

    # --- servicios y pagos ---------------------------------------------------

    def _service_values(self, data, partial=False, current=None):
        current = dict(current) if current else {}

        def pick(key, default=None):
            return data.get(key, current.get(key, default)) if partial else data.get(key, default)

        name = _clean(pick("name"), 200)
        if not name:
            raise Invalid("Escriba el nombre del servicio")
        plan = pick("plan", "estandar")
        if plan not in PLANS:
            raise Invalid("Plan inválido")
        period = pick("period", "mensual")
        if period not in PERIOD_MONTHS:
            raise Invalid("Periodicidad inválida")
        discount = parse_money(pick("discount_pct", 0), "descuento")
        if discount > 100:
            raise Invalid("El descuento no puede superar el 100%")
        due = parse_date(pick("next_due_date"), "fecha del próximo pago")
        return {
            "name": name, "plan": plan, "period": period, "discount_pct": discount,
            "base_price": parse_money(pick("base_price", 0), "valor"),
            "conditions": _clean(pick("conditions", ""), 2000),
            "next_due_date": due,
            "billing_day": date.fromisoformat(due).day if due else current.get("billing_day"),
            "active": 1 if pick("active", 1) in (1, True, "1", "true") else 0,
        }

    def _service_summary(self, values):
        text = f"{values['name']} — {fmt_money(final_price(values))} {values['period']}"
        if values["discount_pct"]:
            text += f" (descuento {values['discount_pct']:g}% sobre {fmt_money(values['base_price'])})"
        if values["plan"] == "especial":
            text += ", plan especial"
        if values["next_due_date"]:
            text += f"; próximo pago {fmt_date(values['next_due_date'])}"
        return text

    def _insert_service(self, conn, contact_id, data, username):
        values = self._service_values(data)
        cur = conn.execute(
            """INSERT INTO services (contact_id, name, plan, base_price, discount_pct, conditions, period,
                   billing_day, next_due_date, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (contact_id, values["name"], values["plan"], values["base_price"], values["discount_pct"],
             values["conditions"], values["period"], values["billing_day"], values["next_due_date"],
             values["active"], self.now_iso()))
        self._event(conn, contact_id, "servicio", "Servicio agregado: " + self._service_summary(values), username)
        return cur.lastrowid

    def create_service(self, contact_id, data, username=""):
        with self.tx() as conn:
            contact = self._get_contact(conn, contact_id)
            if contact["kind"] == "prospecto":
                conn.execute("UPDATE contacts SET kind = 'cliente', stage = 'ganado', converted_at = ?, "
                             "updated_at = ? WHERE id = ?", (self.now_iso(), self.now_iso(), contact_id))
                self._event(conn, contact_id, "estado", f"{STAGE_LABELS[contact['stage']]} → Ganado", username)
                self._event(conn, contact_id, "convertido", "Convertido en cliente", username)
            return self._insert_service(conn, contact_id, data, username)

    def _get_service(self, conn, service_id):
        row = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
        if not row:
            raise NotFound("El servicio no existe")
        return row

    def update_service(self, service_id, data, username=""):
        with self.tx() as conn:
            current = self._get_service(conn, service_id)
            values = self._service_values(data, partial=True, current=current)
            conn.execute(
                """UPDATE services SET name = ?, plan = ?, base_price = ?, discount_pct = ?, conditions = ?,
                       period = ?, billing_day = ?, next_due_date = ?, active = ? WHERE id = ?""",
                (values["name"], values["plan"], values["base_price"], values["discount_pct"],
                 values["conditions"], values["period"], values["billing_day"], values["next_due_date"],
                 values["active"], service_id))
            detail = ("Servicio desactivado: " + values["name"]) if not values["active"] and current["active"] \
                else "Servicio actualizado: " + self._service_summary(values)
            self._event(conn, current["contact_id"], "servicio", detail, username)

    def register_payment(self, service_id, data, username=""):
        paid = parse_date(data.get("paid_date") or self.today().isoformat(), "fecha de pago")
        with self.tx() as conn:
            service = self._get_service(conn, service_id)
            amount = parse_money(data.get("amount"), "valor pagado") if data.get("amount") not in (None, "") \
                else final_price(service)
            due = service["next_due_date"]
            conn.execute("INSERT INTO payments (service_id, due_date, paid_date, amount, note, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (service_id, due, paid, amount, _clean(data.get("note"), 500), self.now_iso()))
            months = PERIOD_MONTHS.get(service["period"], 1)
            next_due = None
            if months:
                base = date.fromisoformat(due) if due else date.fromisoformat(paid)
                next_due = add_months(base, months, service["billing_day"]).isoformat()
            conn.execute("UPDATE services SET next_due_date = ? WHERE id = ?", (next_due, service_id))
            detail = f"Pago de {fmt_money(amount)} por {service['name']}"
            if due:
                late = (date.fromisoformat(paid) - date.fromisoformat(due)).days
                detail += f" (vencía {fmt_date(due)}; " + (f"{late} días tarde)" if late > 0 else "a tiempo)")
            if next_due:
                detail += f". Próximo pago: {fmt_date(next_due)}"
            self._event(conn, service["contact_id"], "pago", detail, username)

    def _payment_status(self, service, today):
        if not service["active"]:
            return "inactivo", None
        if not service["next_due_date"]:
            return "sin_pendiente", None
        days = (date.fromisoformat(service["next_due_date"]) - today).days
        if days < 0:
            return "vencido", days
        if days <= self.due_soon_days:
            return "por_vencer", days
        return "al_dia", days

    def _service_dict(self, service, today):
        item = dict(service)
        item["final_price"] = final_price(service)
        item["status"], item["days"] = self._payment_status(service, today)
        return item

    # --- consultas para las pantallas ----------------------------------------

    def _lead_flags(self, contact, today, now):
        item = dict(contact)
        item.pop("raw_email", None)
        when = contact["next_action_date"]
        if when:
            delta = (date.fromisoformat(when) - today).days
            item["action_status"] = "atrasada" if delta < 0 else ("hoy" if delta == 0 else "programada")
            item["action_days"] = delta
        else:
            item["action_status"] = "sin_accion"
            item["action_days"] = None
        arrived = self.parse_dt(contact["received_at"] or contact["created_at"])
        item["age_hours"] = int((now - arrived).total_seconds() // 3600)
        item["stale"] = (contact["kind"] == "prospecto" and contact["stage"] in OPEN_STAGES
                         and not when and item["age_hours"] >= self.stale_lead_hours)
        return item

    def get_contact(self, contact_id):
        today, now = self.today(), self.now()
        with self.tx() as conn:
            contact = self._lead_flags(self._get_contact(conn, contact_id), today, now)
            contact["raw_email"] = conn.execute("SELECT raw_email FROM contacts WHERE id = ?",
                                                (contact_id,)).fetchone()[0]
            services = []
            for service in conn.execute("SELECT * FROM services WHERE contact_id = ? ORDER BY active DESC, id",
                                        (contact_id,)):
                item = self._service_dict(service, today)
                item["payments"] = [dict(p) for p in conn.execute(
                    "SELECT * FROM payments WHERE service_id = ? ORDER BY paid_date DESC, id DESC", (service["id"],))]
                services.append(item)
            events = [dict(e) for e in conn.execute(
                "SELECT * FROM events WHERE contact_id = ? ORDER BY created_at DESC, id DESC", (contact_id,))]
        contact["services"], contact["events"] = services, events
        return contact

    def list_leads(self):
        today, now = self.today(), self.now()
        with self.tx() as conn:
            rows = conn.execute("SELECT * FROM contacts WHERE kind = 'prospecto' OR converted_at IS NOT NULL "
                                "ORDER BY COALESCE(received_at, created_at) DESC").fetchall()
        return [self._lead_flags(row, today, now) for row in rows]

    def list_clients(self):
        today = self.today()
        with self.tx() as conn:
            clients = conn.execute("SELECT * FROM contacts WHERE kind = 'cliente' ORDER BY name COLLATE NOCASE").fetchall()
            services = conn.execute("SELECT * FROM services ORDER BY active DESC, next_due_date IS NULL, next_due_date").fetchall()
        by_client = {}
        for service in services:
            by_client.setdefault(service["contact_id"], []).append(self._service_dict(service, today))
        result = []
        for client in clients:
            item = {k: client[k] for k in ("id", "name", "company", "phone", "email", "source", "next_action",
                                           "next_action_date", "converted_at", "created_at")}
            item["services"] = by_client.get(client["id"], [])
            result.append(item)
        return result

    def search(self, query, limit=12):
        query = _clean(query, 100)
        if len(query) < 2:
            return []
        like = f"%{query.lower()}%"
        digits = re.sub(r"\D", "", query)
        sql = ("SELECT id, kind, name, company, email, phone, stage, source FROM contacts WHERE "
               "lower(name) LIKE ? OR lower(company) LIKE ? OR email LIKE ? OR lower(interest) LIKE ?")
        params = [like, like, like, like]
        if len(digits) >= 4:
            sql += " OR phone_norm LIKE ?"
            params.append(f"%{digits}%")
        sql += " ORDER BY kind = 'cliente' DESC, updated_at DESC LIMIT ?"
        with self.tx() as conn:
            return [dict(r) for r in conn.execute(sql, (*params, limit))]

    def today_summary(self):
        today, now = self.today(), self.now()
        overdue, due_soon = [], []
        with self.tx() as conn:
            rows = conn.execute(
                """SELECT services.*, contacts.name AS client_name, contacts.company AS client_company,
                          contacts.phone AS client_phone
                   FROM services JOIN contacts ON contacts.id = services.contact_id
                   WHERE services.active = 1 AND services.next_due_date IS NOT NULL
                   ORDER BY services.next_due_date""").fetchall()
            contacts = conn.execute(
                "SELECT * FROM contacts WHERE (kind = 'prospecto' AND stage IN ('nuevo', 'contactado', 'propuesta')) "
                "OR (kind = 'cliente' AND next_action_date IS NOT NULL)").fetchall()
            review = [self._lead_flags(r, today, now) for r in conn.execute(
                "SELECT * FROM contacts WHERE needs_review = 1 ORDER BY created_at DESC")]
            since = (now - timedelta(hours=24)).isoformat()
            new_24h = conn.execute("SELECT COUNT(*) FROM contacts WHERE kind = 'prospecto' AND created_at >= ?",
                                   (since,)).fetchone()[0]
        for row in rows:
            item = self._service_dict(row, today)
            for key in ("client_name", "client_company", "client_phone"):
                item[key] = row[key]
            if item["status"] == "vencido":
                overdue.append(item)
            elif item["status"] == "por_vencer":
                due_soon.append(item)
        actions, no_action = [], []
        for row in contacts:
            item = self._lead_flags(row, today, now)
            if item["action_status"] in ("atrasada", "hoy"):
                actions.append(item)
            elif item["action_status"] == "sin_accion" and row["kind"] == "prospecto":
                no_action.append(item)
        actions.sort(key=lambda c: c["next_action_date"])
        no_action.sort(key=lambda c: -c["age_hours"])
        return {
            "date": today.isoformat(),
            "overdue": overdue, "due_soon": due_soon, "actions": actions, "no_action": no_action,
            "review": review, "new_leads_24h": new_24h,
            "overdue_clients": len({s["contact_id"] for s in overdue}),
            "due_soon_days": self.due_soon_days, "stale_lead_hours": self.stale_lead_hours,
        }

    # --- ingreso automático de leads (Gmail) ---------------------------------

    def is_message_processed(self, message_id):
        with self.tx() as conn:
            return conn.execute("SELECT 1 FROM inbound_messages WHERE message_id = ?", (message_id,)).fetchone() is not None

    def ingest_form_lead(self, lead, capture="auto"):
        """Registra un lead que llegó por el formulario web. Devuelve 'creado', 'fusionado' o 'repetido'."""
        now = self.now_iso()
        with self.tx() as conn:
            if conn.execute("SELECT 1 FROM inbound_messages WHERE message_id = ?", (lead["message_id"],)).fetchone():
                return "repetido", None
            existing = self._find_duplicate(conn, lead.get("email"), lead.get("phone"))
            if existing:
                contact_id = existing["id"]
                summary = lead.get("interest") or lead.get("subject") or "formulario web"
                body = (lead.get("message") or "")[:1500]
                self._event(conn, contact_id, "formulario", f"Volvió a escribir por el formulario web: {summary}"
                            + (f"\n\n{body}" if body else ""), "gmail")
                if existing["kind"] == "prospecto" and existing["stage"] == "perdido":
                    conn.execute("UPDATE contacts SET stage = 'nuevo', updated_at = ? WHERE id = ?", (now, contact_id))
                    self._event(conn, contact_id, "estado", "Perdido → Nuevo (volvió a escribir)", "gmail")
                status = "fusionado"
            else:
                cur = conn.execute(
                    """INSERT INTO contacts (kind, name, company, phone, phone_norm, email, source, stage, interest,
                           message, raw_email, needs_review, capture, received_at, created_at, updated_at)
                       VALUES ('prospecto', ?, ?, ?, ?, ?, 'Web', 'nuevo', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (lead.get("name", ""), lead.get("company", ""), lead.get("phone", ""),
                     norm_phone(lead.get("phone")), norm_email(lead.get("email")), lead.get("interest", ""),
                     lead.get("message", ""), lead.get("raw_text", ""), 1 if lead.get("needs_review") else 0,
                     capture, lead.get("received_at") or now, now, now))
                contact_id = cur.lastrowid
                origin = "importado del historial de Gmail" if capture == "importado" else \
                    "creado automáticamente desde el formulario web (Gmail)"
                self._event(conn, contact_id, "lead_creado", f"Lead {origin}", "gmail")
                if lead.get("needs_review"):
                    self._event(conn, contact_id, "revisar", "No se encontró correo ni teléfono en el formulario: "
                                "revise el correo original", "gmail")
                status = "creado"
            conn.execute("INSERT INTO inbound_messages (message_id, contact_id, subject, received_at, status, processed_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (lead["message_id"], contact_id, lead.get("subject", "")[:300], lead.get("received_at"),
                          status, now))
        return status, contact_id

    # --- indicadores ---------------------------------------------------------

    def set_manual_kpi(self, key, value):
        if key not in MANUAL_KPIS:
            raise Invalid("Indicador desconocido")
        if value in (None, ""):
            with self.tx() as conn:
                conn.execute("DELETE FROM meta WHERE key = ?", (f"kpi:{key}",))
            return
        number = parse_money(value, "valor medido")
        self.set_meta(f"kpi:{key}", f"{number:g}|{self.today().isoformat()}")

    def kpis(self, since=None):
        since = parse_date(since, "fecha de inicio")
        today = self.today()
        with self.tx() as conn:
            leads = [dict(r) for r in conn.execute(
                "SELECT * FROM contacts WHERE kind = 'prospecto' OR converted_at IS NOT NULL")]
            services = [dict(r) for r in conn.execute("SELECT * FROM services")]
            payments = [dict(r) for r in conn.execute("SELECT * FROM payments")]
            summaries = {r[0][:10] for r in conn.execute("SELECT created_at FROM events WHERE type = 'resumen_enviado'")}
            manual = {r["key"][4:]: r["value"] for r in conn.execute("SELECT * FROM meta WHERE key LIKE 'kpi:%'")}

        def lead_day(lead):
            return (lead["received_at"] or lead["created_at"])[:10]

        in_period = [l for l in leads if not since or lead_day(l) >= since]
        processed = [l for l in in_period if l["capture"] != "importado"]  # lo importado no pasó por el proceso

        auto = [l for l in processed if l["capture"] == "auto" and l["received_at"]]
        minutes = [max(0.0, (self.parse_dt(l["created_at"]) - self.parse_dt(l["received_at"])).total_seconds() / 60)
                   for l in auto]
        avg_minutes = round(sum(minutes) / len(minutes), 1) if minutes else None

        web = [l for l in processed if l["source"] == "Web"]
        web_manual_pct = round(100 * sum(l["capture"] == "manual" for l in web) / len(web)) if web else None
        all_manual_pct = round(100 * sum(l["capture"] == "manual" for l in processed) / len(processed)) if processed else None

        open_leads = [l for l in leads if l["kind"] == "prospecto" and l["stage"] in OPEN_STAGES]
        followed_pct = round(100 * sum(1 for l in open_leads if l["next_action_date"]) / len(open_leads)) \
            if open_leads else None

        overdue_clients = {s["contact_id"] for s in services
                           if s["active"] and s["next_due_date"] and s["next_due_date"] < today.isoformat()}

        due_dates = [p["due_date"] for p in payments if p["due_date"]]
        due_dates += [s["next_due_date"] for s in services
                      if s["active"] and s["next_due_date"] and s["next_due_date"] < today.isoformat()]
        if since:
            due_dates = [d for d in due_dates if d >= since]
        if summaries:
            first_summary = min(summaries)
            due_dates = [d for d in due_dates if d >= first_summary]
        else:
            due_dates = []

        def alerted(due):
            day = date.fromisoformat(due)
            return any((day - timedelta(days=n)).isoformat() in summaries for n in range(self.due_soon_days + 1))

        alerted_pct = round(100 * sum(alerted(d) for d in due_dates) / len(due_dates)) if due_dates else None

        timed = [p for p in payments if p["due_date"] and (not since or p["due_date"] >= since)]
        on_time_pct = round(100 * sum(p["paid_date"] <= p["due_date"] for p in timed) / len(timed)) if timed else None

        def manual_value(key):
            raw = manual.get(key)
            if not raw:
                return None, None
            value, measured = raw.split("|")
            return float(value), measured

        localizar, localizar_date = manual_value("localizar_min")
        horas, horas_date = manual_value("horas_semana")

        def row(key, name, baseline, current, target, unit, met, note="", manual_entry=False, measured=None):
            return {"key": key, "name": name, "baseline": baseline, "current": current, "target": target,
                    "unit": unit, "met": met, "note": note, "manual": manual_entry, "measured_on": measured}

        b = BASELINE
        rows = [
            row("registro_min", "Tiempo para registrar un lead (canal web)", b["registro_min"], avg_minutes,
                "≤ 30 (−50%)", "min", None if avg_minutes is None else avg_minutes <= b["registro_min"] * 0.5,
                f"Promedio de {len(auto)} leads: desde que llega el correo hasta que el lead queda en el CRM"
                if auto else "Aún no hay leads automáticos en el periodo"),
            row("transcripcion_pct", "Transcripción manual (canal web)", b["transcripcion_pct"], web_manual_pct,
                "≤ 20%", "%", None if web_manual_pct is None else web_manual_pct <= 20,
                (f"{len(web)} leads web en el periodo. Todos los canales: {all_manual_pct}% "
                 "(WhatsApp y redes aún se registran a mano)") if web else "Aún no hay leads web en el periodo"),
            row("seguimiento_pct", "Leads abiertos con próxima acción registrada", b["seguimiento_pct"], followed_pct,
                "≥ 90%", "%", None if followed_pct is None else followed_pct >= 90,
                f"Foto actual de {len(open_leads)} leads abiertos" if open_leads else "No hay leads abiertos"),
            row("clientes_vencidos", "Clientes con pagos vencidos", b["clientes_vencidos"], len(overdue_clients),
                "< 4", "clientes", len(overdue_clients) < b["clientes_vencidos"], "Foto actual"),
            row("alerta_previa", "Vencimientos alertados antes de la fecha", None, alerted_pct, "100%", "%",
                None if alerted_pct is None else alerted_pct == 100,
                f"{len(due_dates)} vencimientos desde el primer resumen enviado" if due_dates
                else "Se mide cuando se empiecen a enviar los resúmenes diarios"),
            row("pagos_a_tiempo", "Pagos registrados a tiempo", None, on_time_pct, "Aumentar", "%", None,
                f"{len(timed)} pagos con fecha de vencimiento" if timed else "Aún no hay pagos registrados"),
            row("localizar_min", "Tiempo para localizar la información de un cliente", b["localizar_min"], localizar,
                "≤ 13,5 (−70%)", "min", None if localizar is None else localizar <= b["localizar_min"] * 0.3,
                "Medición manual: cronometrar una búsqueda completa en el CRM", True, localizar_date),
            row("horas_semana", "Horas semanales de gestión manual", b["horas_semana"], horas,
                "≤ 3 (−40%)", "h", None if horas is None else horas <= b["horas_semana"] * 0.6,
                "Medición manual: registro semanal de gerencia", True, horas_date),
        ]

        by_source = {}
        for lead in in_period:
            by_source[lead["source"]] = by_source.get(lead["source"], 0) + 1
        return {
            "since": since, "rows": rows,
            "totals": {
                "leads": len(in_period), "auto": sum(l["capture"] == "auto" for l in in_period),
                "imported": sum(l["capture"] == "importado" for l in in_period),
                "manual": sum(l["capture"] == "manual" for l in in_period),
                "won": sum(1 for l in in_period if l["converted_at"]),
                "lost": sum(1 for l in in_period if l["stage"] == "perdido"),
                "summaries_sent": len([d for d in summaries if not since or d >= since]),
            },
            "by_source": sorted(by_source.items(), key=lambda kv: -kv[1]),
        }

    # --- exportación ---------------------------------------------------------

    EXPORTS = {
        "contactos": "SELECT id, kind AS tipo, name AS nombre, company AS empresa, phone AS telefono, email AS correo, "
                     "source AS origen, stage AS estado, interest AS interes, message AS mensaje, "
                     "next_action AS proxima_accion, next_action_date AS fecha_proxima_accion, "
                     "capture AS captura, needs_review AS por_revisar, received_at AS recibido, "
                     "created_at AS creado, converted_at AS convertido FROM contacts ORDER BY id",
        "servicios": "SELECT services.id, contacts.name AS cliente, services.name AS servicio, plan, "
                     "base_price AS valor_base, discount_pct AS descuento_pct, "
                     "ROUND(base_price * (1 - discount_pct / 100.0), 2) AS valor_final, period AS periodicidad, "
                     "next_due_date AS proximo_pago, conditions AS condiciones, active AS activo "
                     "FROM services JOIN contacts ON contacts.id = services.contact_id ORDER BY services.id",
        "pagos": "SELECT payments.id, contacts.name AS cliente, services.name AS servicio, due_date AS vencia, "
                 "paid_date AS pagado, amount AS valor, "
                 "CAST(julianday(paid_date) - julianday(due_date) AS INTEGER) AS dias_tarde, note AS nota "
                 "FROM payments JOIN services ON services.id = payments.service_id "
                 "JOIN contacts ON contacts.id = services.contact_id ORDER BY payments.id",
        "eventos": "SELECT events.id, events.created_at AS fecha, contacts.name AS contacto, type AS tipo, "
                   "detail AS detalle, username AS usuario FROM events "
                   "LEFT JOIN contacts ON contacts.id = events.contact_id ORDER BY events.id",
    }

    def export_csv(self, name):
        if name not in self.EXPORTS:
            raise NotFound("Exportación desconocida")
        with self.tx() as conn:
            cur = conn.execute(self.EXPORTS[name])
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow([c[0] for c in cur.description])
            writer.writerows(cur.fetchall())
        return "﻿" + out.getvalue()  # BOM para que Excel abra bien las tildes

    def reset_data(self):
        """Borra contactos, servicios, pagos y eventos (para pasar de datos de prueba a datos reales)."""
        with self.tx() as conn:
            for table in ("payments", "services", "events", "inbound_messages", "contacts"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM meta WHERE key NOT LIKE 'kpi:%'")
