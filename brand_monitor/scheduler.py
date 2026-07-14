from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta


class Scheduler:
    def __init__(self, service):
        self.service = service
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.next_run: datetime | None = None
        self.last_error = ""

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="brand-monitor-scheduler", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.wait(15):
            try:
                settings = self.service.settings()
                if not settings["schedule_enabled"]:
                    self.next_run = None
                    continue
                if self.next_run is None:
                    self.next_run = datetime.now().astimezone() + timedelta(minutes=settings["schedule_minutes"])
                if datetime.now().astimezone() >= self.next_run:
                    self.service.run_all(settings["schedule_mode"], trigger="schedule")
                    self.next_run = datetime.now().astimezone() + timedelta(minutes=settings["schedule_minutes"])
                    self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                self.next_run = datetime.now().astimezone() + timedelta(minutes=5)

    def status(self):
        return {
            "next_run": self.next_run.isoformat(timespec="seconds") if self.next_run else None,
            "last_error": self.last_error,
            "running": bool(self.thread and self.thread.is_alive()),
        }

