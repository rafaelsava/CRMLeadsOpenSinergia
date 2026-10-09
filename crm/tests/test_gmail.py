import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gmail
from config import Config
from store import Store

SAMPLE = (Path(__file__).resolve().parent.parent / "samples" / "formulario_ejemplo.eml").read_bytes()


class FakeImap:
    """Imita las respuestas de imap.gmail.com que usa gmail.py."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.calls = []
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))
        return "OK", [b"ok"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/Todos"']

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b"101 102"]
        if args[0] == b"101":
            return "OK", [(b"101 (UID 101 BODY[] {1})", SAMPLE), b")"]
        return "OK", [(b"102 (UID 102 BODY[] {1})", b"Subject: roto\r\n\r\nhola"), b")"]

    def logout(self):
        return "BYE", []


class GmailIngestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = Config(data_dir=Path(tmp.name), gmail_user="gerencia@opensinergia.com",
                          gmail_app_password="abcd", gmail_search="from:formulario@opensinergia.com")
        self.store = Store(self.cfg.db_path, self.cfg.tz)
        FakeImap.instances = []

    def test_first_run_imports_history_then_only_new_mail(self):
        with mock.patch.object(gmail.imaplib, "IMAP4_SSL", FakeImap):
            first = gmail.ingest_from_gmail(self.store, self.cfg, log=lambda *_: None)
            second = gmail.ingest_from_gmail(self.store, self.cfg, log=lambda *_: None)

        self.assertEqual(first, {"found": 2, "created": 2, "merged": 0, "repeated": 0, "errors": 0, "backfill": True})
        self.assertEqual(second["repeated"], 2)
        self.assertFalse(second["backfill"])

        calls = FakeImap.instances[0].calls
        self.assertIn(("select", '"[Gmail]/Todos"', True), calls)  # carpeta "Todos", en solo lectura
        search = next(c for c in calls if c[:2] == ("uid", "SEARCH"))
        self.assertEqual(search[2], "X-GM-RAW")
        self.assertIn("from:formulario@opensinergia.com", search[3])
        self.assertIn("newer_than:30d", search[3])
        fetch = next(c for c in calls if c[:2] == ("uid", "FETCH"))
        self.assertEqual(fetch[3], "(BODY.PEEK[])")  # PEEK: no marca los correos como leídos
        later_search = next(c for c in FakeImap.instances[1].calls if c[:2] == ("uid", "SEARCH"))
        self.assertIn("newer_than:3d", later_search[3])

        leads = {c["email"]: c for c in self.store.list_leads()}
        lead = leads["daniela.moreno@ejemplo.co"]
        self.assertEqual(lead["name"], "Daniela Moreno (ejemplo)")
        self.assertEqual(lead["phone"], "312 555 0199")
        self.assertEqual(lead["company"], "Panadería La Espiga")
        self.assertEqual(lead["interest"], "Desarrollo web")
        self.assertEqual(lead["capture"], "importado")
        self.assertIn("Google Maps", lead["message"])
        broken = leads[""]
        self.assertTrue(broken["needs_review"])  # sin correo ni teléfono: queda marcado para revisar


if __name__ == "__main__":
    unittest.main()
