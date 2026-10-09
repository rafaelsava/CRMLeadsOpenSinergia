"""Extrae los datos de un lead a partir del correo que envía el formulario web.

Los plugins de formularios (Contact Form 7, Elementor, WPForms, etc.) mandan los
campos con alguno de estos formatos, y todos se reconocen:

    Nombre: Ana Pérez                 <- etiqueta y valor en la misma línea
    <td>Nombre</td><td>Ana</td>       <- tabla HTML
    Nombre                            <- etiqueta sola y el valor en la línea siguiente
    Ana Pérez

Si el formulario de OpenSinergia usa etiquetas distintas, basta con agregarlas
en FIELD_ALIASES. El texto completo del correo se guarda siempre, así que
nunca se pierde información aunque un campo no se reconozca.
"""
import base64
import binascii
import email
import email.policy
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses, parsedate_to_datetime

# Etiquetas normalizadas: minúsculas, sin tildes ni signos.
FIELD_ALIASES = {
    "email": ("correo", "correo electronico", "email", "e mail", "mail", "tu correo",
              "su correo", "tu email", "your email", "email address"),
    "phone": ("telefono", "celular", "whatsapp", "movil", "phone", "tel", "telefono celular",
              "numero de contacto", "numero de telefono", "numero de celular", "telefono whatsapp",
              "celular whatsapp", "phone number"),
    "company": ("empresa", "compania", "company", "organizacion", "negocio", "nombre de la empresa",
                "razon social", "marca"),
    "name": ("nombre", "nombre completo", "nombres", "nombre y apellido", "nombres y apellidos",
             "tu nombre", "su nombre", "name", "full name", "your name"),
    "interest": ("servicio", "servicios", "servicio de interes", "tipo de servicio", "interes",
                 "asunto", "subject", "plan", "que necesitas", "en que te podemos ayudar",
                 "en que podemos ayudarte"),
    "message": ("mensaje", "tu mensaje", "su mensaje", "message", "your message", "comentario",
                "comentarios", "consulta", "descripcion", "detalles", "cuentanos",
                "cuentanos tu proyecto", "cuentanos sobre tu proyecto", "cuerpo del mensaje",
                "message body", "comentario o mensaje", "comment or message"),
    # Plantilla por defecto de Contact Form 7: "De: Ana Pérez <ana@correo.com>".
    "sender": ("de", "from", "remitente"),
}
# Si la etiqueta no coincide exacto, se busca por palabra clave (en este orden).
FIELD_KEYWORDS = (
    ("email", ("correo", "email", "e mail")),
    ("phone", ("telefono", "celular", "whatsapp", "movil", "phone")),
    ("company", ("empresa", "compania", "company", "organizacion")),
    ("name", ("nombre", "name")),
    ("interest", ("servicio", "asunto", "interes")),
    ("message", ("mensaje", "message", "comentario", "consulta")),
)
# Metadatos que agregan los plugins al final del correo; no son datos del lead.
METADATA_LABELS = {"date", "time", "fecha", "hora", "page url", "url", "url de la pagina", "user agent",
                   "remote ip", "ip", "powered by", "enviado desde", "form", "formulario", "pagina",
                   "page", "referer", "referrer"}
FOOTER_RE = re.compile(r"^(-{2,}|—+|_{3,})\s*$|^(this e-?mail was sent|este (correo|mensaje) (fue|se) envi)", re.I)

LABEL_RE = re.compile(r"^[*_]*([^:：\t]{1,40}?)[*_]*\s*[:：]\s*[*_]*\s*(.*)$")
TAB_RE = re.compile(r"^([^\t]{1,40}?)\s*\t+\s*(.*)$")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
GENERIC_SENDER_WORDS = ("wordpress", "formulario", "form", "website", "sitio", "web", "noreply", "no-reply",
                        "no reply", "notific", "contacto", "elementor", "wpforms", "mailer")

MAX_RAW_TEXT = 20000


def normalize_label(label):
    # "Teléfono (Opcional)" y "Mensaje (¿Cómo podemos ayudarte?)" se comparan sin el paréntesis.
    label = re.sub(r"\([^)]*\)", " ", label or "").strip() or (label or "")
    text = unicodedata.normalize("NFKD", label)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[¿?¡!*_()\[\]\"'.:]", " ", text).replace("-", " ").replace("/", " ")
    return re.sub(r"\s+", " ", text).strip()


_ALIAS_INDEX = {normalize_label(alias): key for key, aliases in FIELD_ALIASES.items() for alias in aliases}


def field_for_label(label, exact_only=False):
    norm = normalize_label(label)
    if not norm:
        return None
    if norm in _ALIAS_INDEX:
        return _ALIAS_INDEX[norm]
    if exact_only or len(norm.split()) > 5:
        return None
    for key, words in FIELD_KEYWORDS:
        if any(re.search(r"\b" + re.escape(w) + r"\b", norm) for w in words):
            return key
    return None


def html_to_text(markup):
    text = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1\s*>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|blockquote|section)\s*>", "\n", text)
    text = re.sub(r"(?i)</t[dh]\s*>", "\t", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r", "")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \xa0]+", " ", line)
        line = re.sub(r"\s*\t\s*", "\t", line).strip(" \t\xa0")
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


EMPTY_VALUES = {"", "empty", "vacio", "n a", "na", "ninguno", "no aplica"}


def _value(value):
    """Los plugins marcan los campos opcionales vacíos con "(Empty)", "-", "N/A", etc."""
    value = value.strip().strip("*_").strip()
    return "" if normalize_label(value) in EMPTY_VALUES or set(value) <= set("-—_ ") else value


def _split_label(line):
    """Si la línea es una etiqueta devuelve (etiqueta, campo, valor, exacta); si no, None.

    "exacta" indica que la etiqueta es una de las conocidas (o un metadato del plugin)
    y no una coincidencia por palabra clave.
    """
    if not line:
        return None
    match = LABEL_RE.match(line) or TAB_RE.match(line)
    if match:
        label, value = match.group(1).strip(), _value(match.group(2))
        norm = normalize_label(label)
        if norm in ("http", "https", "mailto") or value.startswith("//"):
            return None
        return label, field_for_label(label), value, (norm in _ALIAS_INDEX or norm in METADATA_LABELS)
    key = field_for_label(line.strip("*_ "), exact_only=True)
    if key:
        return line, key, "", True
    return None


def extract_fields(text):
    """Recorre el texto del correo y arma {campo: valor} + campos no reconocidos."""
    fields, extras = {}, []
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if FOOTER_RE.match(line):
            current = None
            if line.startswith(("-", "—", "_")):
                # Todo lo que sigue a un separador es pie de página del plugin.
                break
            continue
        parsed = _split_label(line)
        if parsed and current == "message" and not parsed[3]:
            parsed = None  # dentro del mensaje, "algo: algo" es texto del cliente
        if parsed:
            label, key, value, _ = parsed
            if key is None:
                current = None
                if value and normalize_label(label) not in METADATA_LABELS and len(label.split()) <= 4:
                    extras.append(f"{label}: {value}")
                continue
            if key in fields and fields[key]:
                current = None  # el campo ya se llenó; no se sobrescribe
                continue
            fields[key] = value
            current = key if (key == "message" or not value) else None
            continue
        if current is None:
            continue
        if current == "message":
            fields["message"] = (fields["message"] + "\n" + line) if fields["message"] else line
        elif line:
            fields[current] = _value(line)
            current = None
    if "message" in fields:
        fields["message"] = re.sub(r"\n{3,}", "\n\n", fields["message"]).strip()
    return fields, extras


def _decode_parts(msg):
    plain = html_part = None
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and html_part is None:
            html_part = content
    return plain, html_part


def _is_generic_name(name):
    lowered = (name or "").lower()
    return not lowered or "@" in lowered or any(w in lowered for w in GENERIC_SENDER_WORDS)


def parse_form_email(raw_bytes, own_addresses=(), tz=timezone.utc):
    """Convierte un correo (bytes RFC 822) en un dict con los datos del lead."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    own = {a.lower() for a in own_addresses}
    subject = str(msg.get("Subject", "") or "").strip()

    plain, html_body = _decode_parts(msg)
    candidates = []
    if plain and plain.strip():
        candidates.append(plain.replace("\r", ""))
    if html_body:
        candidates.append(html_to_text(html_body))
    text, fields, extras = "", {}, []
    for candidate in candidates:
        cand_fields, cand_extras = extract_fields(candidate)
        if not text or len([v for v in cand_fields.values() if v]) > len([v for v in fields.values() if v]):
            text, fields, extras = candidate, cand_fields, cand_extras

    def usable(addr):
        addr = (addr or "").lower()
        return addr and addr not in own and "noreply" not in addr and "no-reply" not in addr

    reply_to = [(n, a) for n, a in getaddresses(msg.get_all("Reply-To", [])) if a]
    sender = [(n, a) for n, a in getaddresses(msg.get_all("From", [])) if a]
    # "De: Ana Pérez <ana@correo.com>" dentro del cuerpo vale más que los encabezados.
    body_sender = [(n, a) for n, a in getaddresses([fields.get("sender") or ""]) if a]
    if body_sender and not fields.get("email"):
        fields["email"] = body_sender[0][1]
    reply_to = body_sender + reply_to

    email_addr = (fields.get("email") or "").strip()
    found = EMAIL_RE.search(email_addr)
    email_addr = found.group(0) if found else ""
    if not email_addr:
        for _, addr in reply_to:
            if usable(addr):
                email_addr = addr
                break
    if not email_addr:
        for addr in EMAIL_RE.findall(text):
            if usable(addr):
                email_addr = addr
                break

    name = (fields.get("name") or "").strip()
    if not name:
        for display, addr in reply_to + sender:
            if display and not _is_generic_name(display) and addr.lower() not in own:
                name = display.strip()
                break

    message = (fields.get("message") or "").strip()
    if extras:
        message = (message + "\n\n" if message else "") + "\n".join(extras)
    if not message and not any(fields.get(k) for k in ("name", "email", "phone", "company", "interest")):
        message = text.strip()[:4000]

    phone = re.sub(r"[^\d+()\s-]", "", fields.get("phone") or "").strip()

    received_at = None
    if msg.get("Date"):
        try:
            received = parsedate_to_datetime(str(msg["Date"]))
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            received_at = received.astimezone(tz).replace(microsecond=0).isoformat()
        except (TypeError, ValueError):
            received_at = None

    message_id = str(msg.get("Message-ID", "") or "").strip()
    if not message_id:
        message_id = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    return {
        "message_id": message_id,
        "subject": subject,
        "received_at": received_at,
        "name": name[:200],
        "email": email_addr.lower()[:200],
        "phone": phone[:50],
        "company": (fields.get("company") or "").strip()[:200],
        "interest": (fields.get("interest") or "").strip()[:200],
        "message": message[:8000],
        "raw_text": text[:MAX_RAW_TEXT],
        "needs_review": not (email_addr or phone),
    }


def parse_form_email_at(raw_bytes, own_addresses, tz):
    """Igual que parse_form_email, pero si el correo no trae fecha usa la hora actual."""
    lead = parse_form_email(raw_bytes, own_addresses, tz)
    if not lead["received_at"]:
        lead["received_at"] = datetime.now(tz).replace(microsecond=0).isoformat()
    return lead


def _address_text(value):
    """n8n entrega remitentes como texto o como {"text": ..., "value": [{"address", "name"}]}."""
    if isinstance(value, dict):
        if value.get("text"):
            return str(value["text"])
        items = value.get("value") or []
        return ", ".join(f'{i.get("name", "")} <{i.get("address", "")}>'.strip() for i in items if i.get("address"))
    if isinstance(value, list):
        return ", ".join(_address_text(v) for v in value)
    return str(value or "")


def _header(value):
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


def email_bytes_from_n8n(data):
    """Reconstruye el correo a partir de lo que envía el nodo IMAP de n8n.

    Acepta el correo completo en "raw" (texto o base64) o los campos ya separados
    ("resolved"/"simple": subject, from, replyTo, date, messageId, text/textPlain, html/textHtml),
    para que el lead pase por el mismo parser que el resto.
    """
    raw = data.get("raw")
    if raw:
        if isinstance(raw, str):
            compact = re.sub(r"\s+", "", raw)
            try:
                decoded = base64.urlsafe_b64decode(compact.replace("+", "-").replace("/", "_") + "=" * (-len(compact) % 4))
                if re.search(rb"^[\w-]+:", decoded[:2000], re.M):
                    return decoded
            except (ValueError, binascii.Error):
                pass
            return raw.encode("utf-8")
        raise ValueError("El campo raw debe ser texto")

    msg = EmailMessage(policy=email.policy.default)
    for header, keys in (("Subject", ("subject",)), ("From", ("from",)), ("Reply-To", ("replyTo", "reply_to", "reply-to")),
                         ("To", ("to",)), ("Message-ID", ("messageId", "message_id", "message-id"))):
        value = next((data.get(k) for k in keys if data.get(k)), None)
        if value:
            msg[header] = _header(_address_text(value) if header in ("From", "Reply-To", "To") else value)
    date_value = data.get("date")
    if date_value:
        try:
            moment = datetime.fromisoformat(str(date_value).replace("Z", "+00:00"))
            msg["Date"] = format_datetime(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))
        except ValueError:
            msg["Date"] = _header(date_value)
    text = data.get("text") or data.get("textPlain") or ""
    html_body = data.get("html") or data.get("textHtml") or ""
    if not isinstance(text, str):
        text = ""
    if not isinstance(html_body, str):
        html_body = ""
    if not (text.strip() or html_body.strip()):
        raise ValueError("El correo llegó sin cuerpo (ni text ni html)")
    if "Message-ID" not in msg:
        # Sin Message-ID se genera uno estable con el contenido, para que reenviar el mismo correo no duplique.
        digest = hashlib.sha256("\x1f".join([str(msg.get("From", "")), str(msg.get("Subject", "")),
                                             str(msg.get("Date", "")), text, html_body]).encode("utf-8")).hexdigest()
        msg["Message-ID"] = f"<{digest[:32]}@n8n.local>"
    if text.strip():
        msg.set_content(text)
        if html_body.strip():
            msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(html_body, subtype="html")
    return msg.as_bytes()
