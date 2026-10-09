import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from config import get_tz
from store import Duplicate, Invalid, Store, add_months


def make_store():
    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / "crm.db", get_tz("America/Bogota"))
    store._tmp = tmp  # mantiene vivo el directorio mientras dure la prueba
    return store


def form_lead(**overrides):
    lead = {"message_id": "<m1@x>", "subject": "Formulario", "received_at": None, "name": "Ana",
            "email": "ana@x.co", "phone": "300 111 2233", "company": "", "interest": "Web",
            "message": "Hola", "raw_text": "Nombre: Ana", "needs_review": False}
    lead.update(overrides)
    return lead


class AddMonthsTest(unittest.TestCase):
    def test_keeps_billing_day_across_short_months(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 2, 28), 1, anchor_day=31), date(2026, 3, 31))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2026, 3, 10), 12), date(2027, 3, 10))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.addCleanup(self.store._tmp.cleanup)
        self.today = self.store.today()

    def day(self, offset):
        return (self.today + timedelta(days=offset)).isoformat()

    def test_duplicate_contacts_are_detected_by_email_or_phone(self):
        self.store.create_contact({"name": "Ana", "email": "Ana@X.co", "phone": "+57 300 111 2233"})
        with self.assertRaises(Duplicate):
            self.store.create_contact({"name": "Otra", "email": "ana@x.co"})
        with self.assertRaises(Duplicate):
            self.store.create_contact({"name": "Otra", "phone": "3001112233"})
        self.assertTrue(self.store.create_contact({"name": "Otra", "phone": "3001112233"}, force=True))

    def test_contact_needs_some_identifier(self):
        with self.assertRaises(Invalid):
            self.store.create_contact({"company": "Sin datos"})

    def test_moving_a_lead_to_ganado_converts_it_into_a_client(self):
        cid = self.store.create_contact({"name": "Ana", "source": "WhatsApp"})
        self.store.update_contact(cid, {"stage": "contactado", "next_action": "Llamar", "next_action_date": self.day(1)})
        self.store.update_contact(cid, {"stage": "ganado"})
        contact = self.store.get_contact(cid)
        self.assertEqual(contact["kind"], "cliente")
        self.assertIsNotNone(contact["converted_at"])
        types = [e["type"] for e in contact["events"]]
        self.assertIn("convertido", types)
        self.assertIn("seguimiento", types)

    def test_register_payment_moves_next_due_date_and_logs_lateness(self):
        cid = self.store.create_contact({"kind": "cliente", "name": "Cliente", "service": {
            "name": "VPS", "base_price": 500000, "discount_pct": 10, "period": "mensual",
            "next_due_date": "2026-01-31"}})
        service = self.store.get_contact(cid)["services"][0]
        self.assertEqual(service["final_price"], 450000)
        self.store.register_payment(service["id"], {"paid_date": "2026-02-03"})
        contact = self.store.get_contact(cid)
        service = contact["services"][0]
        self.assertEqual(service["next_due_date"], "2026-02-28")
        self.assertEqual(service["payments"][0]["amount"], 450000)
        self.assertIn("3 días tarde", contact["events"][0]["detail"])
        self.store.register_payment(service["id"], {"paid_date": "2026-02-27", "amount": "450000"})
        self.assertEqual(self.store.get_contact(cid)["services"][0]["next_due_date"], "2026-03-31")

    def test_single_payment_service_has_nothing_pending_after_paying(self):
        cid = self.store.create_contact({"kind": "cliente", "name": "Cliente", "service": {
            "name": "Landing", "base_price": 900000, "period": "unico", "next_due_date": self.day(5)}})
        service = self.store.get_contact(cid)["services"][0]
        self.store.register_payment(service["id"], {})
        service = self.store.get_contact(cid)["services"][0]
        self.assertIsNone(service["next_due_date"])
        self.assertEqual(service["status"], "sin_pendiente")

    def test_today_summary_groups_payments_and_follow_ups(self):
        for name, due in (("Vencido", -4), ("Pronto", 2), ("Lejos", 20)):
            self.store.create_contact({"kind": "cliente", "name": name, "service": {
                "name": "Servicio", "base_price": 100, "next_due_date": self.day(due)}}, force=True)
        late = self.store.create_contact({"name": "Lead atrasado", "next_action": "Llamar",
                                          "next_action_date": self.day(-1)})
        self.store.create_contact({"name": "Lead sin acción"})
        self.store.create_contact({"name": "Lead futuro", "next_action": "Enviar", "next_action_date": self.day(3)})
        summary = self.store.today_summary()
        self.assertEqual([s["client_name"] for s in summary["overdue"]], ["Vencido"])
        self.assertEqual([s["client_name"] for s in summary["due_soon"]], ["Pronto"])
        self.assertEqual([c["id"] for c in summary["actions"]], [late])
        self.assertEqual([c["name"] for c in summary["no_action"]], ["Lead sin acción"])
        self.assertEqual(summary["overdue_clients"], 1)

    def test_form_leads_are_idempotent_and_merge_with_known_contacts(self):
        status, cid = self.store.ingest_form_lead(form_lead(received_at=self.store.now_iso()))
        self.assertEqual(status, "creado")
        self.assertEqual(self.store.ingest_form_lead(form_lead())[0], "repetido")
        self.store.update_contact(cid, {"stage": "perdido"})
        status, same = self.store.ingest_form_lead(form_lead(message_id="<m2@x>", email="ANA@x.co", phone=""))
        self.assertEqual((status, same), ("fusionado", cid))
        contact = self.store.get_contact(cid)
        self.assertEqual(contact["stage"], "nuevo")
        self.assertEqual(contact["capture"], "auto")
        self.assertEqual(contact["source"], "Web")

    def test_kpis_compare_against_the_baseline(self):
        self.store.ingest_form_lead(form_lead(received_at=self.store.now_iso()))
        self.store.ingest_form_lead(form_lead(message_id="<old@x>", email="old@x.co", phone=""), capture="importado")
        self.store.create_contact({"name": "Manual", "source": "WhatsApp", "next_action": "Llamar",
                                   "next_action_date": self.day(1)})
        rows = {r["key"]: r for r in self.store.kpis()["rows"]}
        self.assertEqual(rows["registro_min"]["current"], 0.0)
        self.assertTrue(rows["registro_min"]["met"])
        self.assertEqual(rows["transcripcion_pct"]["current"], 0)
        self.assertEqual(rows["seguimiento_pct"]["current"], 33)  # 1 de 3 leads abiertos tiene próxima acción
        self.assertFalse(rows["seguimiento_pct"]["met"])
        self.assertEqual(rows["clientes_vencidos"]["current"], 0)
        self.store.set_manual_kpi("localizar_min", "2.5")
        rows = {r["key"]: r for r in self.store.kpis()["rows"]}
        self.assertEqual(rows["localizar_min"]["current"], 2.5)
        self.assertTrue(rows["localizar_min"]["met"])

    def test_summary_sent_before_due_date_counts_as_alerted(self):
        cid = self.store.create_contact({"kind": "cliente", "name": "Cliente", "service": {
            "name": "VPS", "base_price": 100, "next_due_date": self.day(2)}})
        self.store.log_event(None, "resumen_enviado", "test")
        service = self.store.get_contact(cid)["services"][0]
        self.store.register_payment(service["id"], {"paid_date": self.day(1)})
        row = {r["key"]: r for r in self.store.kpis()["rows"]}["alerta_previa"]
        self.assertEqual(row["current"], 100)

    def test_export_and_reset(self):
        self.store.create_contact({"name": "Ana Ñandú", "email": "ana@x.co"})
        csv_text = self.store.export_csv("contactos")
        self.assertIn("Ana Ñandú", csv_text)
        self.store.set_user("rafael", "una-clave-segura")
        self.store.reset_data()
        self.assertEqual(self.store.list_leads(), [])
        self.assertEqual(self.store.count_users(), 1)

    def test_login_and_sessions(self):
        self.store.set_user("Rafael", "una-clave-segura")
        self.assertIsNone(self.store.check_login("rafael", "otra-clave"))
        user = self.store.check_login("rafael", "una-clave-segura")
        token = self.store.create_session(user["id"])
        self.assertEqual(self.store.session_user(token)["username"], "rafael")
        self.store.delete_session(token)
        self.assertIsNone(self.store.session_user(token))
        with self.assertRaises(Invalid):
            self.store.set_user("x y", "corta")


if __name__ == "__main__":
    unittest.main()
