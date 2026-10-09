"""Resumen diario para gerencia: se arma con los datos de "Hoy" y se envía por correo."""
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from html import escape

from store import STAGE_LABELS, fmt_date, fmt_money

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_LONG = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
               "octubre", "noviembre", "diciembre")
MAX_ROWS = 15


def _long_date(iso):
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day} de {MONTHS_LONG[d.month - 1]}"


def _days_text(days):
    if days is None:
        return ""
    if days < 0:
        return f"vencido hace {-days} día" + ("s" if days < -1 else "")
    if days == 0:
        return "vence hoy"
    return f"vence en {days} día" + ("s" if days > 1 else "")


def _contact_name(item):
    return item.get("name") or item.get("email") or item.get("phone") or "Sin nombre"


def build_summary(data, app_url=""):
    """Devuelve (asunto, texto plano, html) a partir de Store.today_summary()."""
    overdue, due_soon = data["overdue"], data["due_soon"]
    actions, no_action, review = data["actions"], data["no_action"], data["review"]

    subject = (f"CRM OpenSinergia · {_long_date(data['date']).capitalize()}: "
               f"{len(overdue)} pagos vencidos, {len(due_soon)} por vencer, "
               f"{len(actions)} seguimientos, {len(no_action)} leads sin acción")

    sections = [
        ("Pagos vencidos", "#b42318", [
            (f"{s['client_name']} — {s['name']}", f"{fmt_money(s['final_price'])} · {_days_text(s['days'])} "
             f"({fmt_date(s['next_due_date'])})", s["contact_id"]) for s in overdue]),
        (f"Pagos que vencen en los próximos {data['due_soon_days']} días", "#b54708", [
            (f"{s['client_name']} — {s['name']}", f"{fmt_money(s['final_price'])} · {_days_text(s['days'])} "
             f"({fmt_date(s['next_due_date'])})", s["contact_id"]) for s in due_soon]),
        ("Seguimientos para hoy o atrasados", "#175cd3", [
            (_contact_name(c), f"{c['next_action'] or 'Seguimiento'} · "
             + ("hoy" if c["action_days"] == 0 else f"atrasado {-c['action_days']} días")
             + (f" · {STAGE_LABELS[c['stage']]}" if c["kind"] == "prospecto" else " · cliente"), c["id"])
            for c in actions]),
        ("Leads sin próxima acción", "#475467", [
            (_contact_name(c), f"{c['source']} · llegó hace "
             + (f"{c['age_hours'] // 24} días" if c["age_hours"] >= 48 else f"{c['age_hours']} h"), c["id"])
            for c in no_action]),
    ]
    if review:
        sections.append(("Formularios por revisar (sin correo ni teléfono)", "#b54708", [
            (_contact_name(c), "Revise el correo original", c["id"]) for c in review]))

    def link(contact_id):
        return f"{app_url}/#/contacto/{contact_id}" if app_url else ""

    text_lines = [f"Resumen del CRM — {_long_date(data['date'])}", ""]
    html_parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#101828;'
        'max-width:640px;margin:0 auto;padding:8px">',
        f'<p style="margin:0 0 8px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#8dbb51"></span> '
        f'<span style="color:#52525b;font-size:13px">OpenSinergia · CRM</span></p>'
        f'<h1 style="font-size:22px;margin:0 0 4px;letter-spacing:-0.02em">Resumen del CRM</h1>'
        f'<p style="margin:0 0 20px;color:#475467">{escape(_long_date(data["date"]).capitalize())}'
        f' · {data["new_leads_24h"]} leads nuevos en las últimas 24 h</p>',
    ]
    for title, color, rows in sections:
        text_lines.append(f"{title.upper()} ({len(rows)})")
        html_parts.append(f'<h2 style="font-size:15px;margin:20px 0 8px;color:{color}">{escape(title)} '
                          f'<span style="color:#667085;font-weight:normal">({len(rows)})</span></h2>')
        if not rows:
            text_lines.append("  Nada pendiente.")
            html_parts.append('<p style="margin:0;color:#667085">Nada pendiente.</p>')
        else:
            html_parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
            for name, detail, contact_id in rows[:MAX_ROWS]:
                url = link(contact_id)
                text_lines.append(f"  • {name}: {detail}" + (f"  {url}" if url else ""))
                label = escape(name)
                if url:
                    label = f'<a href="{escape(url)}" style="color:#101828">{label}</a>'
                html_parts.append(
                    f'<tr><td style="padding:6px 0;border-bottom:1px solid #eaecf0"><strong>{label}</strong>'
                    f'<br><span style="color:#475467">{escape(detail)}</span></td></tr>')
            html_parts.append("</table>")
            if len(rows) > MAX_ROWS:
                text_lines.append(f"  … y {len(rows) - MAX_ROWS} más en el CRM")
                html_parts.append(f'<p style="color:#667085;margin:6px 0 0">… y {len(rows) - MAX_ROWS} más en el CRM</p>')
        text_lines.append("")
    if app_url:
        text_lines.append(f"Abrir el CRM: {app_url}")
        html_parts.append(f'<p style="margin-top:24px"><a href="{escape(app_url)}" style="background:#527f27;border-radius:999px;'
                          'color:#fff;padding:10px 16px;text-decoration:none">Abrir el CRM</a></p>')
    html_parts.append('<p style="color:#98a2b3;font-size:12px;margin-top:24px">Enviado automáticamente por el CRM '
                      'de OpenSinergia.</p></div>')
    return subject, "\n".join(text_lines), "".join(html_parts)


def send_email(cfg, subject, text, html_body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.summary_from
    msg["To"] = ", ".join(cfg.summary_to)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as smtp:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)


def summary_counts(data):
    return {"overdue": len(data["overdue"]), "due_soon": len(data["due_soon"]), "actions": len(data["actions"]),
            "no_action": len(data["no_action"]), "review": len(data["review"])}


def record_summary_sent(store, data, recipients, username):
    """Deja el envío en el historial: con eso se mide el KPI de vencimientos alertados a tiempo."""
    counts = summary_counts(data)
    store.log_event(None, "resumen_enviado",
                    f"Resumen enviado a {recipients}: {counts['overdue']} vencidos, {counts['due_soon']} por vencer, "
                    f"{counts['actions']} seguimientos, {counts['no_action']} sin acción", username)
    store.set_meta("summary_last_date", data["date"])
    store.set_meta("summary_last_error", "")


def send_daily_summary(store, cfg, username="sistema"):
    """Envía el resumen por SMTP desde el propio CRM (alternativa a n8n)."""
    data = store.today_summary()
    subject, text, html_body = build_summary(data, cfg.app_url)
    send_email(cfg, subject, text, html_body)
    record_summary_sent(store, data, ", ".join(cfg.summary_to), username)
    return {"to": cfg.summary_to, "subject": subject}
