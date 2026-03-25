"""
app_window.py — Windows XP Luna-themed bridge window.

Titlebar  : blue gradient + animated cloud icon + min/max/close buttons
Body      : XP beige (#ece9d8), grouped controls, chunky progress bar
Statusbar : LEDs for Spotify/AIMP/sync state + expand arrow for log panel
Log panel : monospace activity log, hidden by default, revealed by arrow
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import math
import os
from PIL import Image, ImageTk

# ── Palette ────────────────────────────────────────────────────────────────────

XP_BEIGE      = "#ece9d8"
XP_WHITE      = "#ffffff"
XP_BLUE_DARK  = "#0a246a"
XP_BLUE_MID   = "#2355c5"
XP_BLUE_BTN   = "#3d7bd8"
XP_GROUP_BDR  = "#919191"
XP_BTN_FACE   = "#f0efe6"
XP_BTN_DARK   = "#dbd9cc"
XP_STATUS_BG  = "#d4d0c5"
XP_STATUS_BDR = "#888888"
XP_TEXT       = "#000000"
XP_BLUE_TEXT  = "#0054e3"
XP_LOG_BG     = "#ffffff"
XP_PROG_FILL  = "#2d6ec7"
XP_PROG_ALT   = "#4a8ae0"


class BridgeAppWindow:
    def __init__(self, bridge):
        self._bridge  = bridge
        self._events: queue.Queue[tuple] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root:   tk.Tk | None = None
        self._ready   = threading.Event()
        self._closed  = threading.Event()

        self._log_lines: list[tuple[str, str]] = []   # (text, tag)
        self._max_log   = 600
        self._log_open  = False
        self._march_offset = 0
        self._sync_pct     = 0.0
        self._march_job    = None

    # ── Public API (thread-safe) ───────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="BridgeAppWindow", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def wait_until_closed(self):
        self._closed.wait()

    def show(self):   self._events.put(("show",))
    def hide(self):   self._events.put(("hide",))
    def close(self):  self._events.put(("close",))

    def append_log(self, text: str):
        if text:
            self._events.put(("log", text))

    def update_progress(self, current, total, message: str):
        self._events.put(("progress", current, total, message or ""))

    # ── Tk main thread ─────────────────────────────────────────────────────────

    def _run(self):
        root = tk.Tk()
        self._root = root
        root.withdraw()

        root.title("Spotify AIMP Bridge")
        root.configure(bg=XP_BEIGE)
        root.resizable(False, False)

        # Remove default titlebar — we draw our own
        root.overrideredirect(False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Unmap>", self._on_unmap)
        self._set_taskbar_icon(root)

        # Center on screen
        w, h = 480, 210
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build_ui(root)
        self._bind_drag(root)

        root.deiconify()
        self._ready.set()
        root.after(60, self._process_events)
        root.after(80, self._animate_march)
        root.after(500, self._animate_cloud)
        root.mainloop()
        self._closed.set()

    def _set_taskbar_icon(self, root: tk.Tk):
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
            img = Image.open(icon_path)
            self._taskbar_icon = ImageTk.PhotoImage(img)
            root.iconphoto(True, self._taskbar_icon)
        except Exception:
            pass

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self, root: tk.Tk):
        self._build_titlebar(root)
        self._build_body(root)
        self._build_statusbar(root)
        self._log_frame = self._build_log_panel(root)

    def _build_titlebar(self, root: tk.Tk):
        bar = tk.Canvas(root, height=26, bg=XP_BLUE_MID,
                        highlightthickness=0, bd=0)
        bar.pack(fill="x", side="top")
        bar.bind("<Configure>", lambda e: self._redraw_titlebar(bar))
        self._titlebar_canvas = bar

        # Cloud canvas (animated)
        self._cloud_canvas = tk.Canvas(bar, width=20, height=20,
                                       bg=XP_BLUE_MID, highlightthickness=0)
        self._cloud_canvas.place(x=5, y=3)
        self._draw_cloud(self._cloud_canvas, 0)

        # Title label
        self._title_label = tk.Label(
            bar, text="Spotify AIMP Bridge",
            bg=XP_BLUE_MID, fg="#ffffff",
            font=("Tahoma", 9, "bold"),
        )
        self._title_label.place(x=28, y=5)

        # Window control buttons
        self._wbtn_x   = self._make_wbtn(bar, "✕", self._on_close,   "#c03030", "#e07070")
        self._wbtn_max = self._make_wbtn(bar, "□", lambda: None,     "#3070d8", "#5ca0f0")
        self._wbtn_min = self._make_wbtn(bar, "_", self._on_minimise, "#3070d8", "#5ca0f0")
        bar.bind("<Configure>", lambda e: self._reposition_wbtns(bar))

    def _make_wbtn(self, parent, text, cmd, dark, light):
        c = tk.Canvas(parent, width=21, height=19,
                      highlightthickness=0, bd=0, cursor="arrow")
        c.bind("<Button-1>", lambda e: cmd())
        c.bind("<Enter>",    lambda e: self._wbtn_hover(c, light))
        c.bind("<Leave>",    lambda e: self._wbtn_draw(c, dark, light, text))
        self._wbtn_draw(c, dark, light, text)
        return c

    def _wbtn_draw(self, c, dark, light, text):
        c.delete("all")
        c.configure(bg=light)
        c.create_rectangle(0, 0, 20, 18, fill=dark, outline="#333333", width=1)
        c.create_text(10, 9, text=text, fill="#ffffff",
                      font=("Tahoma", 8, "bold"), anchor="center")

    def _wbtn_hover(self, c, light):
        c.configure(bg=light)

    def _reposition_wbtns(self, bar):
        w = bar.winfo_width()
        self._wbtn_x.place(x=w-24, y=4)
        self._wbtn_max.place(x=w-47, y=4)
        self._wbtn_min.place(x=w-70, y=4)

    def _redraw_titlebar(self, bar):
        w = bar.winfo_width()
        h = bar.winfo_height()
        bar.delete("grad")
        steps = 20
        for i in range(steps):
            t   = i / steps
            r   = int(0x44 + (0x19 - 0x44) * t)
            g   = int(0x8d + (0x44 - 0x8d) * t)
            b   = int(0xe8 + (0x94 - 0xe8) * t)
            col = f"#{r:02x}{g:02x}{b:02x}"
            y0  = int(h * i / steps)
            y1  = int(h * (i+1) / steps) + 1
            bar.create_rectangle(0, y0, w, y1, fill=col, outline="", tags="grad")
        bar.tag_lower("grad")

    def _draw_cloud(self, c, phase):
        c.delete("all")
        dy = math.sin(phase) * 1.5
        y  = 10 + dy
        # Layers: dark base, mid, bright highlight
        c.create_oval(1, y+1, 11, y+9,  fill="#8ab0e8", outline="")
        c.create_oval(5, y-1, 17, y+9,  fill="#aac8f8", outline="")
        c.create_oval(11, y+2, 19, y+9, fill="#90b4ea", outline="")
        c.create_oval(2, y+3, 18, y+10, fill="#b8d4ff", outline="")
        c.create_oval(4, y,   14, y+8,  fill="#d0e4ff", outline="")
        c.create_oval(7, y-2, 14, y+5,  fill="#ffffff",  outline="")

    def _build_body(self, root: tk.Tk):
        body = tk.Frame(root, bg=XP_BEIGE)
        body.pack(fill="x", padx=0)

        pad = tk.Frame(body, bg=XP_BEIGE)
        pad.pack(fill="x", padx=8, pady=(6, 0))

        # ── Sync group ─────────────────────────────────────────────────────
        grp = tk.Frame(pad, bg=XP_WHITE,
                       relief="groove", bd=2,
                       highlightbackground=XP_GROUP_BDR,
                       highlightthickness=1)
        grp.pack(fill="x", pady=(0, 6))

        tk.Label(grp, text="Playlist sync", bg=XP_WHITE,
                 fg=XP_BLUE_TEXT, font=("Tahoma", 8, "bold")
                 ).pack(anchor="w", padx=6, pady=(4, 2))

        # Progress bar — custom canvas for XP marching stripes
        self._prog_canvas = tk.Canvas(
            grp, height=18, bg="#888888",
            highlightthickness=0, relief="sunken", bd=2,
        )
        self._prog_canvas.pack(fill="x", padx=6, pady=(0, 2))
        self._prog_canvas.bind("<Configure>", lambda e: self._redraw_bar())

        self._prog_label_var = tk.StringVar(value="Ready")
        tk.Label(grp, textvariable=self._prog_label_var,
                 bg=XP_WHITE, fg="#444444",
                 font=("Tahoma", 8)).pack(anchor="w", padx=6, pady=(0, 4))

        # ── Control buttons ────────────────────────────────────────────────
        btn_row = tk.Frame(pad, bg=XP_BEIGE)
        btn_row.pack(fill="x", pady=(0, 6))

        self._btn_play = self._xp_btn(
            btn_row, "▶  Play / Pause",
            lambda: self._bridge.spotify.toggle(), blue=True
        )
        self._btn_play.pack(side="left", padx=(0, 4))

        self._xp_btn(btn_row, "◀◀  Prev",
                     lambda: self._bridge.spotify.prev_track()
                     ).pack(side="left", padx=(0, 4))

        self._xp_btn(btn_row, "Next  ▶▶",
                     lambda: self._bridge.spotify.next_track()
                     ).pack(side="left", padx=(0, 4))

        self._xp_btn(btn_row, "⟳  Sync playlist",
                     lambda: self._bridge.manual_sync_current_playlist()
                     ).pack(side="left")

        self._xp_btn(btn_row, "Clear cache/data",
                     lambda: self._bridge.clear_all_cache_data()
                     ).pack(side="left", padx=(6, 0))

    def _xp_btn(self, parent, text, cmd, blue=False):
        face  = "#4a8de8" if blue else XP_BTN_FACE
        dark  = "#2358c8" if blue else XP_BTN_DARK
        fg    = "#000000"

        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=face, fg=fg, activebackground=dark, activeforeground=fg,
            font=("Tahoma", 8, "bold"),
            relief="raised", bd=2, padx=8, pady=3,
            cursor="arrow",
        )
        return btn

    def _build_statusbar(self, root: tk.Tk):
        bar = tk.Frame(root, bg=XP_STATUS_BG,
                       relief="flat", bd=0,
                       highlightbackground=XP_STATUS_BDR,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")

        inner = tk.Frame(bar, bg=XP_STATUS_BG)
        inner.pack(fill="x", padx=6, pady=3)

        self._led_spotify = self._led_pane(inner, "Spotify", "gray")
        self._led_aimp    = self._led_pane(inner, "AIMP",    "gray")
        self._led_sync    = self._led_pane(inner, "Syncing…","gray")

        # Expand arrow — right side
        self._expand_canvas = tk.Canvas(
            inner, width=16, height=14,
            bg=XP_STATUS_BG, highlightthickness=0, cursor="hand2"
        )
        self._expand_canvas.pack(side="right")
        self._expand_canvas.bind("<Button-1>", lambda e: self._toggle_log())
        self._draw_expand_arrow(False)

    def _led_pane(self, parent, label, color):
        frame = tk.Frame(parent, bg=XP_STATUS_BG)
        frame.pack(side="left", padx=(0, 10))
        c = tk.Canvas(frame, width=10, height=10,
                      bg=XP_STATUS_BG, highlightthickness=0)
        c.pack(side="left", padx=(0, 3))
        self._draw_led(c, color)
        tk.Label(frame, text=label, bg=XP_STATUS_BG,
                 fg=XP_TEXT, font=("Tahoma", 8)).pack(side="left")
        return c

    def _draw_led(self, c, color):
        colors = {
            "green": ("#22bb22", "#88ff88"),
            "blue":  ("#2255cc", "#88bbff"),
            "gray":  ("#888888", "#cccccc"),
            "red":   ("#bb2222", "#ff8888"),
        }
        dark, light = colors.get(color, colors["gray"])
        c.delete("all")
        c.create_oval(1, 1, 9, 9, fill=dark, outline="#555555", width=1)
        c.create_oval(3, 2, 6, 5, fill=light, outline="")

    def _draw_expand_arrow(self, open_: bool):
        c = self._expand_canvas
        c.delete("all")
        c.create_rectangle(0, 0, 15, 13, fill=XP_STATUS_BG, outline=XP_STATUS_BDR)
        if open_:
            # up arrow
            c.create_polygon(7, 3, 2, 10, 12, 10, fill="#444", outline="")
        else:
            # down arrow
            c.create_polygon(7, 10, 2, 3, 12, 3, fill="#444", outline="")

    def _build_log_panel(self, root: tk.Tk):
        frame = tk.Frame(root, bg=XP_BEIGE)

        lbl = tk.Label(frame, text="Activity log",
                       bg=XP_BEIGE, fg=XP_BLUE_TEXT,
                       font=("Tahoma", 8, "bold"))
        lbl.pack(anchor="w", padx=8, pady=(4, 2))

        log_wrap = tk.Frame(frame, bg="#888888",
                            relief="sunken", bd=2)
        log_wrap.pack(fill="x", padx=8, pady=(0, 6))

        self._log_text = tk.Text(
            log_wrap,
            height=8,
            bg=XP_LOG_BG, fg=XP_TEXT,
            font=("Courier New", 8),
            relief="flat", bd=0,
            state="disabled",
            wrap="none",
        )
        scroll = tk.Scrollbar(log_wrap, orient="vertical",
                              command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scroll.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Colour tags
        self._log_text.tag_config("ok",   foreground="#005500")
        self._log_text.tag_config("warn", foreground="#885500")
        self._log_text.tag_config("info", foreground="#000088")
        self._log_text.tag_config("sync", foreground="#660066")
        self._log_text.tag_config("err",  foreground="#880000")
        self._log_text.tag_config("def",  foreground="#000000")

        return frame

    # ── Progress bar (marching stripes) ───────────────────────────────────────

    def _redraw_bar(self):
        c    = self._prog_canvas
        w    = c.winfo_width()
        h    = c.winfo_height()
        fill = int(w * self._sync_pct)
        c.delete("all")
        # Background
        c.create_rectangle(0, 0, w, h, fill="#c8c8c8", outline="")
        if fill <= 0:
            return
        # Marching stripes
        stripe = 18
        off    = self._march_offset % stripe
        x      = -stripe + off
        while x < fill:
            x0 = max(0, x)
            x1 = min(fill, x + stripe // 2)
            if x1 > x0:
                c.create_rectangle(x0, 0, x1, h,
                                   fill=XP_PROG_FILL, outline="")
            x0b = max(0, x + stripe // 2)
            x1b = min(fill, x + stripe)
            if x1b > x0b:
                c.create_rectangle(x0b, 0, x1b, h,
                                   fill=XP_PROG_ALT, outline="")
            x += stripe
        # Right edge bevel
        c.create_line(fill, 0, fill, h, fill="#1a4fa0", width=1)

    def _animate_march(self):
        if self._sync_pct > 0 and self._sync_pct < 1.0:
            self._march_offset += 2
            self._redraw_bar()
        if self._root:
            self._march_job = self._root.after(50, self._animate_march)

    # ── Cloud animation ────────────────────────────────────────────────────────

    def _animate_cloud(self, phase=0.0):
        if self._cloud_canvas and self._root:
            self._draw_cloud(self._cloud_canvas, phase)
            self._root.after(80, self._animate_cloud, phase + 0.08)

    # ── Log panel toggle ───────────────────────────────────────────────────────

    def _toggle_log(self):
        self._log_open = not self._log_open
        self._draw_expand_arrow(self._log_open)
        if self._log_open:
            self._log_frame.pack(fill="x", side="bottom")
            # Resize window
            if self._root:
                self._root.geometry(f"480x360")
        else:
            self._log_frame.pack_forget()
            if self._root:
                self._root.geometry(f"480x210")

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _bind_drag(self, root: tk.Tk):
        self._drag_x = 0
        self._drag_y = 0

        def start(e):
            self._drag_x = e.x_root - root.winfo_x()
            self._drag_y = e.y_root - root.winfo_y()

        def drag(e):
            root.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

        self._titlebar_canvas.bind("<Button-1>",   start)
        self._titlebar_canvas.bind("<B1-Motion>",  drag)
        self._title_label.bind("<Button-1>",       start)
        self._title_label.bind("<B1-Motion>",      drag)

    # ── Window controls ────────────────────────────────────────────────────────

    def _on_close(self):
        self._bridge.stop()
        if self._root:
            self._root.destroy()

    def _on_minimise(self):
        if self._root:
            self._root.withdraw()

    def _on_unmap(self, _event=None):
        if not self._root:
            return
        try:
            if self._root.state() == "iconic":
                self._root.after(0, self._root.withdraw)
        except Exception:
            pass

    # ── Event processing ───────────────────────────────────────────────────────

    def _process_events(self):
        if not self._root:
            return
        try:
            while True:
                event = self._events.get_nowait()
                kind  = event[0]

                if kind == "close":
                    self._root.destroy()
                    return

                elif kind == "show":
                    self._root.deiconify()
                    self._root.lift()

                elif kind == "hide":
                    self._root.withdraw()

                elif kind == "log":
                    self._push_log(event[1])

                elif kind == "progress":
                    _, current, total, message = event
                    if current is None or total is None:
                        self._sync_pct = 0.0
                        self._prog_label_var.set("Ready")
                        self._draw_led(self._led_sync, "gray")
                    else:
                        safe_total = max(1, int(total))
                        safe_cur   = max(0, min(int(current), safe_total))
                        self._sync_pct = safe_cur / safe_total
                        self._prog_label_var.set(
                            message or f"{safe_cur} / {safe_total} tracks"
                        )
                        if safe_cur >= safe_total:
                            self._draw_led(self._led_sync, "green")
                        else:
                            self._draw_led(self._led_sync, "blue")
                    self._redraw_bar()

        except queue.Empty:
            pass
        finally:
            if self._root:
                self._root.after(60, self._process_events)

    # ── LED helpers (can be called from other threads via events) ─────────────

    def set_spotify_led(self, color: str):
        self._events.put(("led_spotify", color))

    def set_aimp_led(self, color: str):
        self._events.put(("led_aimp", color))

    # ── Log ────────────────────────────────────────────────────────────────────

    def _push_log(self, text: str):
        tag = "def"
        tl  = text.lower()
        if any(k in tl for k in ("ok", "done", "ready", "connected", "sync complete", "in sync")):
            tag = "ok"
        elif any(k in tl for k in ("warn", "failed", "error", "mismatch")):
            tag = "warn" if "warn" in tl else "err"
        elif any(k in tl for k in ("spotify", "track", "artist")):
            tag = "info"
        elif any(k in tl for k in ("sync", "playlist", "aimp", "jump", "load")):
            tag = "sync"

        stamp = time.strftime("%H:%M:%S")
        line  = f"[{stamp}] {text}"
        self._log_lines.append((line, tag))
        if len(self._log_lines) > self._max_log:
            self._log_lines.pop(0)

        t = self._log_text
        t.configure(state="normal")
        t.insert("end", line + "\n", tag)
        # Trim if too long
        while float(t.index("end")) > self._max_log + 2:
            t.delete("1.0", "2.0")
        t.see("end")
        t.configure(state="disabled")

    # ── Safe toggle (from buttons on main tk thread) ───────────────────────────
    def _safe_toggle(self): self._bridge.spotify.toggle()
    def _safe_prev(self):   self._bridge.spotify.prev_track()
    def _safe_next(self):   self._bridge.spotify.next_track()
    def _safe_sync(self):   self._bridge.manual_sync_current_playlist()


class StreamMirror:
    """Mirrors prints into the app window log and optionally to the original stream."""

    def __init__(self, original_stream, window: BridgeAppWindow, passthrough: bool = True):
        self._original  = original_stream
        self._window    = window
        self._pass      = passthrough
        self._buffer    = ""
        self._lock      = threading.Lock()

    def write(self, data):
        if not isinstance(data, str):
            data = str(data)
        with self._lock:
            if self._pass:
                try:
                    self._original.write(data)
                    self._original.flush()
                except Exception:
                    pass
            self._buffer += data
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self._window.append_log(line)

    def flush(self):
        with self._lock:
            if self._pass:
                try:
                    self._original.flush()
                except Exception:
                    pass
