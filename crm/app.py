"""CRM OpenSinergia — punto de entrada.

    python3 app.py serve                  inicia el CRM (front + tareas automáticas)
    python3 app.py adduser <usuario>      crea un usuario o cambia su contraseña
    python3 app.py seed                   carga datos ficticios para probar y hacer la demo
    python3 app.py reset --yes            borra los datos (deja los usuarios) para pasar a datos reales
    python3 app.py ingest-eml <archivo>   procesa un correo .eml como si hubiera llegado a Gmail
    python3 app.py gmail                  revisa Gmail una vez y muestra el resultado
    python3 app.py summary [--preview]    envía el resumen diario (o lo guarda en HTML sin enviarlo)
"""
import argparse
import getpass
import sys
from datetime import timedelta
from pathlib import Path

from config import Config
from jobs import Jobs
from mailparse import parse_form_email_at
from notify import build_summary
from server import App
from store import Invalid, Store


def open_store(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return Store(cfg.db_path, cfg.tz, cfg.due_soon_days, cfg.stale_lead_hours)


def cmd_serve(cfg, args):
    store = open_store(cfg)
    if not store.count_users():
        print("No hay usuarios. Cree uno con:  python3 app.py adduser <usuario>", file=sys.stderr)
    jobs = Jobs(store, cfg)
    jobs.start()
    server = App(store, cfg, jobs, verbose=args.verbose).make_server()
    print(f"CRM OpenSinergia en http://{cfg.host}:{cfg.port}")
    print(f"  Gmail: {'activo, cada ' + str(cfg.gmail_poll_minutes) + ' min' if cfg.gmail_enabled else 'sin configurar'}")
    print(f"  Resumen diario: {'a las ' + str(cfg.summary_hour) + ':00 para ' + ', '.join(cfg.summary_to) if cfg.smtp_enabled else 'sin configurar'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.stop()
        server.server_close()


def cmd_adduser(cfg, args):
    store = open_store(cfg)
    password = getpass.getpass("Contraseña (mínimo 8 caracteres): ")
    if password != getpass.getpass("Repita la contraseña: "):
        sys.exit("Las contraseñas no coinciden")
    try:
        store.set_user(args.username, password)
    except Invalid as exc:
        sys.exit(str(exc))
    print(f"Usuario '{args.username.lower()}' listo")


def cmd_seed(cfg, args):
    store = open_store(cfg)
    with store.tx() as conn:
        if conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] and not args.force:
            sys.exit("La base ya tiene datos. Use --force para agregar los datos de prueba igual.")
    seed_demo(store)
    print("Datos de prueba cargados (todos son ficticios). Para borrarlos: python3 app.py reset --yes")


def cmd_reset(cfg, args):
    if not args.yes:
        sys.exit("Esto borra contactos, servicios, pagos e historial. Confirme con: python3 app.py reset --yes")
    open_store(cfg).reset_data()
    print("Datos borrados. Los usuarios se conservan.")


def cmd_ingest_eml(cfg, args):
    store = open_store(cfg)
    for name in args.files:
        lead = parse_form_email_at(Path(name).read_bytes(), cfg.own_addresses, cfg.tz)
        status, contact_id = store.ingest_form_lead(lead, capture="auto")
        print(f"{name}: {status} (contacto {contact_id}) — {lead['name'] or '(sin nombre)'} "
              f"<{lead['email'] or 'sin correo'}> {lead['phone']}")


def cmd_gmail(cfg, args):
    store = open_store(cfg)
    print(Jobs(store, cfg).run_gmail())


def cmd_summary(cfg, args):
    store = open_store(cfg)
    if args.preview:
        subject, _, html_body = build_summary(store.today_summary(), cfg.app_url)
        out = cfg.data_dir / "resumen-preview.html"
        out.write_text("<!doctype html><meta charset='utf-8'>" + html_body, encoding="utf-8")
        print(f"{subject}\nVista previa guardada en {out}")
        return
    print(Jobs(store, cfg).run_summary())


def seed_demo(store):
    """Datos ficticios para probar el flujo completo (criterio 4 del documento: probar antes con datos de prueba)."""
    today = store.today()

    def day(offset):
        return (today + timedelta(days=offset)).isoformat()

    def backdate(contact_id, hours):
        moment = (store.now() - timedelta(hours=hours)).isoformat()
        with store.tx() as conn:
            conn.execute("UPDATE contacts SET created_at = ?, received_at = ?, updated_at = ? WHERE id = ?",
                         (moment, moment, moment, contact_id))
            conn.execute("UPDATE events SET created_at = ? WHERE contact_id = ?", (moment, contact_id))

    clients = [
        ("Ferretería El Tornillo (demo)", "Ferretería El Tornillo", "300 555 0101", "compras@demo-tornillo.co", "Referido",
         {"name": "Infraestructura administrada (VPS)", "plan": "especial", "base_price": 450000, "discount_pct": 10,
          "period": "mensual", "next_due_date": day(-6), "conditions": "10% de descuento por pago anticipado de 12 meses en 2025"}),
        ("Clínica Dental Sonrisa (demo)", "Clínica Sonrisa", "300 555 0102", "admin@demo-sonrisa.co", "WhatsApp",
         {"name": "Marketing digital", "plan": "estandar", "base_price": 1200000, "discount_pct": 0,
          "period": "mensual", "next_due_date": day(2), "conditions": ""}),
        ("Café La Molienda (demo)", "La Molienda SAS", "300 555 0103", "gerencia@demo-molienda.co", "Instagram",
         {"name": "Mantenimiento web", "plan": "especial", "base_price": 300000, "discount_pct": 20,
          "period": "mensual", "next_due_date": day(-2), "conditions": "Tarifa congelada hasta diciembre"}),
        ("Transportes Andinos (demo)", "Transportes Andinos", "300 555 0104", "ti@demo-andinos.co", "Web",
         {"name": "Desarrollo de software a la medida", "plan": "estandar", "base_price": 2500000, "discount_pct": 0,
          "period": "mensual", "next_due_date": day(12), "conditions": "Pago por hitos; este es el hito 2 de 4"}),
        ("Inmobiliaria Horizonte (demo)", "Horizonte", "300 555 0105", "contacto@demo-horizonte.co", "Facebook",
         {"name": "Hosting administrado", "plan": "estandar", "base_price": 900000, "discount_pct": 0,
          "period": "anual", "next_due_date": day(0), "conditions": ""}),
    ]
    for name, company, phone, email, source, service in clients:
        contact_id = store.create_contact({"kind": "cliente", "name": name, "company": company, "phone": phone,
                                           "email": email, "source": source, "service": service}, "demo", force=True)
        backdate(contact_id, 24 * 60)

    leads = [
        ("Laura Gómez (demo)", "Estudio Creativo LG", "301 555 0201", "laura@demo-lg.co", "Web", "Desarrollo web",
         "Necesito una página web para mi estudio con portafolio y formulario.", "nuevo", None, None, 3, "auto"),
        ("Andrés Ruiz (demo)", "", "301 555 0202", "", "WhatsApp", "Marketing digital",
         "Quiere pauta en redes para su restaurante.", "nuevo", None, None, 70, "manual"),
        ("Marcela Pinto (demo)", "Pinto & Asociados", "301 555 0203", "marcela@demo-pinto.co", "Web",
         "Infraestructura administrada", "Migrar el servidor de la firma a la nube.", "contactado",
         "Enviar propuesta de migración", day(0), 30, "auto"),
        ("Jorge Castaño (demo)", "Distribuidora JC", "301 555 0204", "jorge@demo-jc.co", "Instagram",
         "Tienda en línea", "Tienda virtual con pasarela de pagos.", "propuesta",
         "Llamar para resolver dudas de la propuesta", day(-1), 120, "manual"),
        ("Sofía Herrera (demo)", "", "301 555 0205", "sofia@demo-herrera.co", "Facebook", "Mantenimiento web",
         "Su página actual está caída.", "contactado", "Revisar acceso al hosting actual", day(3), 50, "manual"),
        ("Camilo Vargas (demo)", "Gimnasio Fuerza", "301 555 0206", "camilo@demo-fuerza.co", "Web",
         "Desarrollo de app", "App para reservas de clases.", "perdido", None, None, 400, "auto"),
    ]
    for name, company, phone, email, source, interest, message, stage, action, action_date, hours, capture in leads:
        if capture == "auto":
            lead = {"message_id": f"<demo-{phone}@demo>", "subject": "Nuevo mensaje desde el formulario web",
                    "received_at": (store.now() - timedelta(hours=hours, minutes=4)).isoformat(),
                    "name": name, "email": email, "phone": phone, "company": company, "interest": interest,
                    "message": message, "raw_text": f"Nombre: {name}\nCorreo: {email}\nTeléfono: {phone}\n"
                                                    f"Servicio: {interest}\nMensaje: {message}", "needs_review": False}
            _, contact_id = store.ingest_form_lead(lead, capture="auto")
            moment = (store.now() - timedelta(hours=hours)).isoformat()
            with store.tx() as conn:
                conn.execute("UPDATE contacts SET created_at = ?, updated_at = ? WHERE id = ?",
                             (moment, moment, contact_id))
                conn.execute("UPDATE events SET created_at = ? WHERE contact_id = ?", (moment, contact_id))
        else:
            contact_id = store.create_contact({"name": name, "company": company, "phone": phone, "email": email,
                                               "source": source, "interest": interest, "message": message},
                                              "demo", force=True)
            backdate(contact_id, hours)
        if stage != "nuevo" or action:
            store.update_contact(contact_id, {"stage": stage, "next_action": action or "",
                                              "next_action_date": action_date}, "demo")

    # Un lead que llegó por la web y ya se convirtió en cliente: muestra el recorrido completo.
    lead = {"message_id": "<demo-convertido@demo>", "subject": "Nuevo mensaje desde el formulario web",
            "received_at": (store.now() - timedelta(days=9, minutes=3)).isoformat(),
            "name": "Valentina Ríos (demo)", "email": "valentina@demo-rios.co", "phone": "301 555 0207",
            "company": "Ríos Arquitectura", "interest": "Infraestructura administrada",
            "message": "Necesitamos un servidor para los planos y respaldos diarios.",
            "raw_text": "Nombre: Valentina Ríos (demo)\nCorreo: valentina@demo-rios.co", "needs_review": False}
    _, contact_id = store.ingest_form_lead(lead, capture="auto")
    store.update_contact(contact_id, {"stage": "contactado"}, "demo")
    store.add_note(contact_id, "Llamada de 20 min: necesitan 500 GB y respaldo diario. Se envía propuesta.", "demo")
    store.update_contact(contact_id, {"stage": "propuesta"}, "demo")
    store.create_service(contact_id, {"name": "Infraestructura administrada (VPS)", "plan": "especial",
                                      "base_price": 600000, "discount_pct": 15, "period": "mensual",
                                      "next_due_date": day(-9),
                                      "conditions": "15% de descuento los primeros 6 meses"}, "demo")
    service_id = store.get_contact(contact_id)["services"][0]["id"]
    store.register_payment(service_id, {"paid_date": day(-9), "amount": 510000, "note": "Primer pago (demo)"}, "demo")
    moment = (store.now() - timedelta(days=9)).isoformat()
    with store.tx() as conn:
        conn.execute("UPDATE contacts SET created_at = ?, converted_at = ? WHERE id = ?", (moment, moment, contact_id))


def main():
    parser = argparse.ArgumentParser(description="CRM OpenSinergia")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="inicia el CRM")
    serve.add_argument("-v", "--verbose", action="store_true", help="muestra cada solicitud HTTP")
    adduser = sub.add_parser("adduser", help="crea un usuario o cambia su contraseña")
    adduser.add_argument("username")
    seed = sub.add_parser("seed", help="carga datos ficticios de prueba")
    seed.add_argument("--force", action="store_true")
    reset = sub.add_parser("reset", help="borra todos los datos menos los usuarios")
    reset.add_argument("--yes", action="store_true")
    ingest = sub.add_parser("ingest-eml", help="procesa correos .eml del formulario")
    ingest.add_argument("files", nargs="+")
    sub.add_parser("gmail", help="revisa Gmail una vez")
    summary = sub.add_parser("summary", help="envía el resumen diario")
    summary.add_argument("--preview", action="store_true", help="solo genera el HTML, no lo envía")
    args = parser.parse_args()

    commands = {"serve": cmd_serve, "adduser": cmd_adduser, "seed": cmd_seed, "reset": cmd_reset,
                "ingest-eml": cmd_ingest_eml, "gmail": cmd_gmail, "summary": cmd_summary}
    commands[args.command](Config(), args)


if __name__ == "__main__":
    main()
