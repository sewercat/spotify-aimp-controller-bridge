"""
Minimal sync progress popup window for playlist generation.
"""

from __future__ import annotations

import queue
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover - handled at runtime
    tk = None
    ttk = None


class SyncProgressWindow:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled and tk is not None and ttk is not None
        self._thread: threading.Thread | None = None
        self._events: queue.Queue[tuple] = queue.Queue()
        self._started = False

    def start(self):
        if not self._enabled or self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SyncProgressWindow",
            daemon=True,
        )
        self._thread.start()

    def close(self):
        if self._enabled:
            self._events.put(("close",))

    def update(self, current, total, message: str):
        if self._enabled:
            self._events.put(("update", current, total, message or ""))

    def _run_loop(self):
        root = tk.Tk()
        root.title("Spotify-AIMP Sync")
        root.geometry("420x120")
        root.resizable(False, False)
        root.withdraw()
        root.attributes("-topmost", True)

        container = ttk.Frame(root, padding=12)
        container.pack(fill="both", expand=True)

        self._title_var = tk.StringVar(value="Syncing playlist...")
        self._count_var = tk.StringVar(value="0/0 tracks")
        self._status_var = tk.StringVar(value="Preparing...")

        ttk.Label(container, textvariable=self._title_var).pack(anchor="w")
        self._bar = ttk.Progressbar(
            container,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
            length=390,
        )
        self._bar.pack(fill="x", pady=(8, 8))
        ttk.Label(container, textvariable=self._count_var).pack(anchor="w")
        ttk.Label(container, textvariable=self._status_var).pack(anchor="w", pady=(6, 0))

        self._last_activity = 0.0

        def process_events():
            try:
                while True:
                    event = self._events.get_nowait()
                    kind = event[0]
                    if kind == "close":
                        root.destroy()
                        return
                    if kind != "update":
                        continue

                    _, current, total, message = event
                    if current is None or total is None:
                        self._bar["value"] = 0
                        self._count_var.set("0/0 tracks")
                        self._status_var.set("")
                        root.withdraw()
                        continue

                    if total <= 0:
                        total = 1
                    if current < 0:
                        current = 0
                    if current > total:
                        current = total

                    self._bar["maximum"] = total
                    self._bar["value"] = current
                    self._count_var.set(f"{current}/{total} tracks")
                    self._status_var.set(message)
                    self._last_activity = time.monotonic()

                    if not root.winfo_viewable():
                        root.deiconify()
                        root.lift()
            except queue.Empty:
                pass
            finally:
                # Auto-hide after a brief idle period if nothing updates.
                if root.winfo_viewable() and self._last_activity:
                    if time.monotonic() - self._last_activity > 3.0:
                        root.withdraw()
                        self._last_activity = 0.0
                root.after(100, process_events)

        root.after(100, process_events)
        root.mainloop()
