"""Tareas automáticas: revisar Gmail cada pocos minutos y enviar el resumen diario."""
import json
import threading
import time
import traceback

from gmail import ingest_from_gmail
from notify import send_daily_summary


class Jobs:
    def __init__(self, store, cfg, log=print):
        self.store, self.cfg, self.log = store, cfg, log
        self._gmail_lock = threading.Lock()
        self._summary_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_gmail = 0.0

    def run_gmail(self):
        if not self._gmail_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            result = ingest_from_gmail(self.store, self.cfg, self.log)
            self.store.set_meta("gmail_last_run", self.store.now_iso())
            self.store.set_meta("gmail_last_result", json.dumps(result))
            self.store.set_meta("gmail_last_error", "")
            if result["created"] or result["merged"] or result["errors"]:
                self.log(f"[gmail] {result}")
            return result
        except Exception as exc:
            self.store.set_meta("gmail_last_run", self.store.now_iso())
            self.store.set_meta("gmail_last_error", str(exc)[:500])
            self.log(f"[gmail] error: {exc}")
            raise
        finally:
            self._last_gmail = time.monotonic()
            self._gmail_lock.release()

    def run_summary(self, username="sistema"):
        with self._summary_lock:
            try:
                result = send_daily_summary(self.store, self.cfg, username)
                self.store.set_meta("summary_last_error", "")
                return result
            except Exception as exc:
                self.store.set_meta("summary_last_error", str(exc)[:500])
                self.log(f"[resumen] error: {exc}")
                raise

    def status(self):
        get = self.store.get_meta
        last_result = get("gmail_last_result")
        return {
            "gmail": {
                "enabled": self.cfg.gmail_enabled, "account": self.cfg.gmail_user,
                "search": self.cfg.gmail_search, "every_minutes": self.cfg.gmail_poll_minutes,
                "last_run": get("gmail_last_run"), "last_error": get("gmail_last_error") or "",
                "last_result": json.loads(last_result) if last_result else None,
            },
            "summary": {
                "enabled": self.cfg.smtp_enabled or bool(self.cfg.n8n_summary_webhook),
                "via": "n8n" if self.cfg.n8n_summary_webhook else ("crm" if self.cfg.smtp_enabled else ""),
                "to": self.cfg.summary_to, "hour": self.cfg.summary_hour,
                "last_date": get("summary_last_date"), "last_error": get("summary_last_error") or "",
            },
            "n8n": {
                "enabled": self.cfg.n8n_enabled,
                "last_ingest": json.loads(get("n8n_last_ingest")) if get("n8n_last_ingest") else None,
            },
        }

    def _tick(self):
        if self.cfg.gmail_enabled and time.monotonic() - self._last_gmail >= self.cfg.gmail_poll_minutes * 60:
            try:
                self.run_gmail()
            except Exception:
                pass  # el error queda guardado y se muestra en la pantalla "Hoy"
        now = self.store.now()
        if (self.cfg.smtp_enabled and not self.cfg.n8n_summary_webhook and now.hour >= self.cfg.summary_hour
                and self.store.get_meta("summary_last_date") != now.date().isoformat()
                and self.store.get_meta("summary_failed_date") != now.date().isoformat()):
            try:
                self.run_summary()
            except Exception:
                # Un solo intento automático por día; se puede reenviar desde la pantalla "Hoy".
                self.store.set_meta("summary_failed_date", now.date().isoformat())

    def _loop(self):
        time.sleep(3)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                self.log(traceback.format_exc())
            self._stop.wait(30)

    def start(self):
        threading.Thread(target=self._loop, name="crm-jobs", daemon=True).start()

    def stop(self):
        self._stop.set()
