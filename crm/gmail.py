"""Lee los correos del formulario web desde Gmail (IMAP, solo lectura) y los convierte en leads."""
import imaplib
import re

from mailparse import parse_form_email_at

IMAP_HOST = "imap.gmail.com"
LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def _find_all_mail(conn):
    """Busca la carpeta "Todos" de Gmail (su nombre cambia según el idioma de la cuenta)."""
    typ, boxes = conn.list()
    if typ != "OK":
        return "INBOX"
    for raw in boxes or []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        match = LIST_RE.match(line)
        if match and "\\All" in match.group("flags"):
            return match.group("name")
    return "INBOX"


def _imap_quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ingest_from_gmail(store, cfg, log=print):
    """Trae los correos del formulario y registra los leads. Devuelve un resumen de lo procesado."""
    if not cfg.gmail_enabled:
        raise RuntimeError("Gmail no está configurado (GMAIL_USER, GMAIL_APP_PASSWORD y GMAIL_SEARCH)")
    first_run = store.get_meta("gmail_backfill_done") != "1"
    days = cfg.gmail_backfill_days if first_run else 3
    # Los enviados y borradores se excluyen: solo interesan los correos que llegan del formulario.
    query = f"({cfg.gmail_search}) newer_than:{days}d -in:sent -in:drafts"
    result = {"found": 0, "created": 0, "merged": 0, "repeated": 0, "errors": 0, "backfill": first_run}

    conn = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=60)
    try:
        conn.login(cfg.gmail_user, cfg.gmail_app_password)
        mailbox = _find_all_mail(conn)
        typ, _ = conn.select(mailbox, readonly=True)  # readonly: no marca los correos como leídos
        if typ != "OK":
            raise RuntimeError(f"No se pudo abrir la carpeta {mailbox}")
        if query.isascii():
            typ, data = conn.uid("SEARCH", "X-GM-RAW", _imap_quote(query))
        else:
            conn.literal = query.encode("utf-8")
            typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        if typ != "OK":
            raise RuntimeError("Gmail rechazó la búsqueda; revise GMAIL_SEARCH")
        uids = data[0].split() if data and data[0] else []
        result["found"] = len(uids)
        for uid in uids:
            try:
                typ, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if typ != "OK" or raw is None:
                    raise RuntimeError("respuesta vacía")
                lead = parse_form_email_at(raw, cfg.own_addresses, cfg.tz)
                status, _ = store.ingest_form_lead(lead, capture="importado" if first_run else "auto")
                result[{"creado": "created", "fusionado": "merged", "repetido": "repeated"}[status]] += 1
            except Exception as exc:  # un correo malo no detiene a los demás
                result["errors"] += 1
                log(f"[gmail] error procesando el correo {uid!r}: {exc}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    if first_run:
        store.set_meta("gmail_backfill_done", "1")
    return result
