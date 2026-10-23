#!/usr/bin/env python3
"""A small cross-app mouse and keyboard macro recorder for macOS.

Global hotkeys:
    F1   set mana check point
    F2   capture mana color
    F6   set BP1 first slot
    F7   set BP2 first slot
    F8   start/stop recording
    F9   replay the recorded macro
    F10  clear the recording
    F11  stop recording or playback

macOS will ask for Accessibility permission the first time this program is
used. Grant it to the terminal or Python application that launches it.
"""

from __future__ import annotations

import threading
import time
import json
import os
import re
import subprocess
import sys
import tempfile
import fcntl
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pynput import keyboard, mouse
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

try:
    import Quartz
except ImportError:
    Quartz = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageGrab = None  # type: ignore[assignment]

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError as error:
    if error.name != "_tkinter":
        raise
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]


@dataclass
class MacroSession:
    session_id: str
    name: str
    state: dict[str, Any] = field(default_factory=dict)


class MacroRecorder:
    SESSION_STATE_FIELDS = frozenset(
        {
            "events", "events_lock", "event_refresh_pending", "recording", "playing",
            "stop_playback", "last_event_time", "last_move_time", "playback_error",
            "automation_running", "automation_timed_runes", "automation_stop",
            "background_pause", "background_thread", "background_held_keys",
            "background_held_buttons", "background_button_positions", "automation_backpack",
            "automation_slot", "automation_target_pid", "automation_target_window_id",
            "automation_focus_interval", "last_focus_time", "backpack_names", "profile",
            "threshold_var", "slot_size_var", "profile_status", "background_enabled",
            "background_target_var", "focus_interval_var", "recording_loop_interval_var",
            "timed_runes_var", "rune_interval_var", "recording_loop_interval",
            "rune_interval", "status", "event_count",
        }
    )

    def __getattribute__(self, name: str) -> Any:
        if name in MacroRecorder.SESSION_STATE_FIELDS:
            try:
                sessions = object.__getattribute__(self, "_sessions")
                context = object.__getattribute__(self, "_session_context")
                session_id = getattr(context, "session_id", None)
                if session_id is None:
                    session_id = object.__getattribute__(self, "_active_session_id")
                session = sessions.get(session_id)
                if session is not None and name in session.state:
                    return session.state[name]
            except AttributeError:
                pass
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in MacroRecorder.SESSION_STATE_FIELDS:
            try:
                sessions = object.__getattribute__(self, "_sessions")
                context = object.__getattribute__(self, "_session_context")
                session_id = getattr(context, "session_id", None)
                if session_id is None:
                    session_id = object.__getattribute__(self, "_active_session_id")
                session = sessions.get(session_id)
                if session is not None:
                    session.state[name] = value
                    return
            except AttributeError:
                pass
        object.__setattr__(self, name, value)

    def __init__(self, root: tk.Tk) -> None:
        self._sessions: dict[str, MacroSession] = {}
        self._active_session_id = "session-1"
        self._next_session_number = 2
        self._session_context = threading.local()
        self._session_ui_switching = False
        self._sessions[self._active_session_id] = MacroSession(self._active_session_id, "Session 1")

        self.root = root
        self.root.title("Macro Recorder")
        self.root.geometry("820x760")
        self.root.minsize(520, 420)

        self.mouse_controller = mouse.Controller()
        self.keyboard_controller = keyboard.Controller()
        self.events: list[dict[str, Any]] = []
        self.events_lock = threading.Lock()
        self.event_refresh_pending = False
        self.recording = False
        self.playing = False
        self.stop_playback = threading.Event()
        self.last_event_time: float | None = None
        self.last_move_time: float | None = None
        self.playback_error: str | None = None
        self.automation_running = False
        self.automation_timed_runes = False
        self.automation_stop = threading.Event()
        self.background_pause = threading.Event()
        self.background_thread: threading.Thread | None = None
        self.background_held_keys: set[Key | KeyCode] = set()
        self.background_held_buttons: set[Button] = set()
        self.background_button_positions: dict[Button, tuple[int, int]] = {}
        self.automation_backpack = 0
        self.automation_slot = 1
        self.automation_target_pid: int | None = None
        self.automation_target_window_id: int | None = None
        self.automation_focus_interval = 10.0
        self.last_focus_time = 0.0
        self.focus_input_mutex = threading.RLock()
        self.focus_input_owner: int | None = None
        self.focus_lock_held = False
        self.focus_lock_file = Path(tempfile.gettempdir(), "macro_recorder_focus.lock").open("a+")
        self.background_targets: dict[str, tuple[int, int]] = {}
        self.backpack_names = ["bp1", "bp2"]
        self.profile: dict[str, Any] = {
            "mana_threshold_percent": 80,
            "mana_point": None,
            "mana_color": None,
            "mana_color_tolerance": 35,
            "slot_width": 34,
            "slot_height": 34,
            "start_slot": 1,
            "bp1": {"first_slot": None, "rows": 4, "columns": 5},
            "bp2": {"first_slot": None, "rows": 4, "columns": 5},
            "left_hand": None,
            "fish_slot": None,
            "cast_key": "f5",
            "cast_delay": 1.0,
        }
        self._default_profile = copy.deepcopy(self.profile)
        self.threshold_var = tk.StringVar(value="80")
        self.slot_size_var = tk.StringVar(value="34 x 34")
        self.profile_status = tk.StringVar(value="Profile not calibrated")
        self.background_enabled = tk.BooleanVar(value=False)
        self.background_target_var = tk.StringVar(value="Foreground input")
        self.focus_interval_var = tk.StringVar(value="10")
        self.recording_loop_interval_var = tk.StringVar(value="0")
        self.timed_runes_var = tk.BooleanVar(value=False)
        self.rune_interval_var = tk.StringVar(value="60")
        self.recording_loop_interval = 0.0
        self.rune_interval = 60.0

        self.status = tk.StringVar(value="Ready")
        self.event_count = tk.StringVar(value="0 events")
        self.session_selector_var = tk.StringVar(value="Session 1")
        self._build_ui()

        self.mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self.mouse_listener.start()
        self.keyboard_listener.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_background_targets()

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        shell.columnconfigure(0, weight=1)

        self.ui_canvas = tk.Canvas(shell, highlightthickness=0)
        vertical_scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.ui_canvas.yview)
        horizontal_scrollbar = ttk.Scrollbar(shell, orient="horizontal", command=self.ui_canvas.xview)
        self.ui_canvas.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.ui_canvas.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        frame = ttk.Frame(self.ui_canvas, padding=18)
        self.ui_content_frame = frame
        self.ui_content_window = self.ui_canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", self._update_ui_scroll_region)
        self.ui_canvas.bind("<Configure>", self._resize_ui_content)
        self.root.bind_all("<MouseWheel>", self._scroll_ui_with_wheel)
        self.root.bind_all("<Button-4>", lambda _event: self.ui_canvas.yview_scroll(-1, "units"))
        self.root.bind_all("<Button-5>", lambda _event: self.ui_canvas.yview_scroll(1, "units"))

        ttk.Label(frame, text="Macro Recorder", font=(".AppleSystemUIFont", 20, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Record actions across macOS apps and replay them later.",
        ).pack(anchor="w", pady=(2, 16))

        session_row = ttk.Frame(frame)
        session_row.pack(fill="x", pady=(0, 10))
        ttk.Label(session_row, text="Active macro session:").pack(side="left")
        self.session_selector = ttk.Combobox(
            session_row,
            width=18,
            textvariable=self.session_selector_var,
            state="readonly",
            values=("Session 1",),
        )
        self.session_selector.pack(side="left", padx=(6, 8))
        self.session_selector.bind("<<ComboboxSelected>>", self._on_session_selected)
        ttk.Button(session_row, text="New session", command=self.add_session).pack(side="left")

        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        self.record_button = ttk.Button(controls, text="●  Record (F8)", command=self.toggle_recording)
        self.record_button.pack(side="left", padx=(0, 8))
        self.play_button = ttk.Button(controls, text="▶  Replay (F9)", command=self.start_playback)
        self.play_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="■  Stop (F11)", command=self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Clear (F10)", command=self.clear).pack(side="left")
        ttk.Button(controls, text="Restart to load code", command=self.restart_program).pack(side="right")

        files = ttk.Frame(frame)
        files.pack(fill="x", pady=(10, 0))
        ttk.Button(files, text="Save Recording", command=self.save_recording).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Load Recording", command=self.load_recording).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Save Profile", command=self.save_profile).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Load Profile", command=self.load_profile).pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=18)
        status_row = ttk.Frame(frame)
        status_row.pack(fill="x")
        self.status_label = ttk.Label(
            status_row,
            textvariable=self.status,
            font=(".AppleSystemUIFont", 13, "bold"),
        )
        self.status_label.pack(side="left")
        self.event_count_label = ttk.Label(status_row, textvariable=self.event_count)
        self.event_count_label.pack(side="right")

        self.events_view = tk.Listbox(frame, height=10, activestyle="none")
        self.events_view.pack(fill="both", expand=True, pady=(10, 12))
        ttk.Label(
            frame,
            text="F1 mana point   •   F2 mana color   •   F6 BP1   •   F7 BP2   •   F8 record/stop   •   F9 replay   •   F10 clear   •   F11 stop",
            foreground="#666666",
        ).pack(anchor="w")

        automation = ttk.LabelFrame(frame, text="Rune automation", padding=10)
        automation.pack(fill="x", pady=(14, 0))
        ttk.Label(automation, text="Mana threshold (%):").grid(row=0, column=0, sticky="w")
        self.threshold_entry = ttk.Entry(automation, width=7, textvariable=self.threshold_var)
        self.threshold_entry.grid(row=0, column=1, sticky="w", padx=(6, 14))
        ttk.Label(automation, textvariable=self.slot_size_var).grid(row=0, column=2, sticky="w")
        ttk.Button(automation, text="Start automation", command=self.start_automation).grid(row=0, column=3, padx=(12, 5))
        ttk.Button(automation, text="Stop automation", command=self.stop_automation).grid(row=0, column=4)
        rune_timer_row = ttk.Frame(automation)
        rune_timer_row.grid(row=1, column=0, columnspan=5, sticky="w", pady=(7, 0))
        self.timed_runes_checkbutton = ttk.Checkbutton(
            rune_timer_row,
            text="Use timer instead of mana pixel",
            variable=self.timed_runes_var,
        )
        self.timed_runes_checkbutton.pack(side="left")
        ttk.Label(rune_timer_row, text="Rune interval (sec):").pack(side="left", padx=(12, 4))
        self.rune_interval_entry = ttk.Entry(rune_timer_row, width=7, textvariable=self.rune_interval_var)
        self.rune_interval_entry.pack(side="left")
        loop_row = ttk.Frame(automation)
        loop_row.grid(row=2, column=0, columnspan=5, sticky="w", pady=(7, 0))
        self.background_checkbutton = ttk.Checkbutton(
            loop_row,
            text="Loop loaded fishing/eating recording while waiting",
            variable=self.background_enabled,
        )
        self.background_checkbutton.pack(side="left")
        ttk.Label(loop_row, text="Pause between loops (sec):").pack(side="left", padx=(12, 4))
        self.recording_loop_interval_entry = ttk.Entry(
            loop_row,
            width=6,
            textvariable=self.recording_loop_interval_var,
        )
        self.recording_loop_interval_entry.pack(side="left")
        target_row = ttk.Frame(automation)
        target_row.grid(row=3, column=0, columnspan=5, sticky="w", pady=(7, 0))
        ttk.Label(target_row, text="Window to focus:").pack(side="left")
        self.background_target_menu = ttk.Combobox(
            target_row,
            width=42,
            textvariable=self.background_target_var,
            state="readonly",
            values=("Foreground input",),
        )
        self.background_target_menu.pack(side="left", padx=(6, 6))
        ttk.Button(target_row, text="Refresh windows", command=self._refresh_background_targets).pack(side="left")
        ttk.Label(target_row, text="Refocus every (sec):").pack(side="left", padx=(12, 4))
        self.focus_interval_entry = ttk.Entry(target_row, width=6, textvariable=self.focus_interval_var)
        self.focus_interval_entry.pack(side="left")
        ttk.Label(
            automation,
            text="Selected window receives normal foreground mouse and keyboard input.",
            foreground="#666666",
        ).grid(row=4, column=0, columnspan=5, sticky="w", pady=(4, 0))

        calibration = ttk.Frame(automation)
        calibration.grid(row=5, column=0, columnspan=5, sticky="w", pady=(10, 0))
        self.backpack_calibration_frame = ttk.Frame(calibration)
        self.backpack_calibration_frame.grid(row=0, column=0, columnspan=2, sticky="w")
        self._rebuild_backpack_buttons()
        ttk.Button(calibration, text="Add backpack", command=self.add_backpack).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        other_calibration_buttons = [
            ("Set left hand", lambda: self.calibrate_point("left_hand")),
            ("Set fish slot", lambda: self.calibrate_point("fish_slot")),
            ("Set mana check point", lambda: self.calibrate_point("mana_point")),
            ("Capture mana color", self.calibrate_mana_color),
        ]
        for index, (label, command) in enumerate(other_calibration_buttons):
            ttk.Button(calibration, text=label, command=command).grid(row=2 + index // 2, column=index % 2, sticky="w", padx=(0, 8), pady=2)
        self.profile_status_label = ttk.Label(
            automation,
            textvariable=self.profile_status,
            foreground="#666666",
        )
        self.profile_status_label.grid(row=6, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Button(automation, text="Test mana point", command=self.test_mana_point).grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Button(automation, text="Reset held keys", command=self.release_stuck_inputs).grid(row=7, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

    @property
    def active_session_id(self) -> str:
        return self._active_session_id

    @property
    def active_session(self) -> MacroSession:
        return self._sessions[self._active_session_id]

    def _new_session(self, name: str) -> MacroSession:
        session_id = f"session-{self._next_session_number}"
        self._next_session_number += 1
        session = MacroSession(session_id, name)
        session.state.update(
            {
                "events": [],
                "events_lock": threading.Lock(),
                "event_refresh_pending": False,
                "recording": False,
                "playing": False,
                "stop_playback": threading.Event(),
                "last_event_time": None,
                "last_move_time": None,
                "playback_error": None,
                "automation_running": False,
                "automation_timed_runes": False,
                "automation_stop": threading.Event(),
                "background_pause": threading.Event(),
                "background_thread": None,
                "background_held_keys": set(),
                "background_held_buttons": set(),
                "background_button_positions": {},
                "automation_backpack": 0,
                "automation_slot": 1,
                "automation_target_pid": None,
                "automation_target_window_id": None,
                "automation_focus_interval": 10.0,
                "last_focus_time": 0.0,
                "backpack_names": ["bp1", "bp2"],
                "profile": copy.deepcopy(self._default_profile),
                "threshold_var": tk.StringVar(value="80"),
                "slot_size_var": tk.StringVar(value="34 x 34"),
                "profile_status": tk.StringVar(value="Profile not calibrated"),
                "background_enabled": tk.BooleanVar(value=False),
                "background_target_var": tk.StringVar(value="Foreground input"),
                "focus_interval_var": tk.StringVar(value="10"),
                "recording_loop_interval_var": tk.StringVar(value="0"),
                "timed_runes_var": tk.BooleanVar(value=False),
                "rune_interval_var": tk.StringVar(value="60"),
                "recording_loop_interval": 0.0,
                "rune_interval": 60.0,
                "status": tk.StringVar(value="Ready"),
                "event_count": tk.StringVar(value="0 events"),
            }
        )
        self._sessions[session_id] = session
        return session

    def add_session(self) -> None:
        session = self._new_session(f"Session {self._next_session_number}")
        self.session_selector.configure(values=tuple(item.name for item in self._sessions.values()))
        self._switch_session(session.session_id)

    def _on_session_selected(self, _event: Any = None) -> None:
        name = self.session_selector_var.get()
        session = next((item for item in self._sessions.values() if item.name == name), None)
        if session is not None:
            self._switch_session(session.session_id)

    def _switch_session(self, session_id: str) -> None:
        if session_id == self._active_session_id or session_id not in self._sessions:
            return
        if self.recording:
            self.toggle_recording()
        self._active_session_id = session_id
        self._session_ui_switching = True
        try:
            session = self.active_session
            self.session_selector_var.set(session.name)
            self.threshold_entry.configure(textvariable=session.state["threshold_var"])
            self.rune_interval_entry.configure(textvariable=session.state["rune_interval_var"])
            self.recording_loop_interval_entry.configure(
                textvariable=session.state["recording_loop_interval_var"]
            )
            self.focus_interval_entry.configure(textvariable=session.state["focus_interval_var"])
            self.background_checkbutton.configure(variable=session.state["background_enabled"])
            self.timed_runes_checkbutton.configure(variable=session.state["timed_runes_var"])
            self.background_target_menu.configure(textvariable=session.state["background_target_var"])
            self.background_target_menu.set(session.state["background_target_var"].get())
            self.status_label.configure(textvariable=session.state["status"])
            self.event_count_label.configure(textvariable=session.state["event_count"])
            self.profile_status_label.configure(textvariable=session.state["profile_status"])
            self._rebuild_backpack_buttons()
            self.record_button.configure(
                text="■  Stop (F8)" if session.state["recording"] else "●  Record (F8)"
            )
            self.play_button.configure(
                state="disabled" if session.state["playing"] else "normal"
            )
            self.status.set(session.state["status"].get())
            self.profile_status.set(session.state["profile_status"].get())
            self.event_count.set(session.state["event_count"].get())
            self._refresh_events()
        finally:
            self._session_ui_switching = False

    def _run_in_session(
        self,
        session_id: str,
        callback: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        previous = getattr(self._session_context, "session_id", None)
        self._session_context.session_id = session_id
        try:
            return callback(*args, **kwargs)
        finally:
            if previous is None:
                del self._session_context.session_id
            else:
                self._session_context.session_id = previous

    def _schedule_in_session(
        self,
        session_id: str,
        callback: Any,
        *args: Any,
        delay_ms: int = 0,
        **kwargs: Any,
    ) -> None:
        self.root.after(
            delay_ms,
            lambda: self._run_in_session(session_id, callback, *args, **kwargs),
        )

    def _set_session_status(self, session_id: str, text: str) -> None:
        self._sessions[session_id].state["status"].set(text)
        if session_id == self._active_session_id:
            self.status.set(text)

    def _update_ui_scroll_region(self, _event: Any = None) -> None:
        self.ui_canvas.configure(scrollregion=self.ui_canvas.bbox("all"))

    def _resize_ui_content(self, event: Any) -> None:
        self.ui_canvas.itemconfigure(
            self.ui_content_window,
            width=max(event.width, self.ui_content_frame.winfo_reqwidth()),
        )
        self._update_ui_scroll_region()

    def _scroll_ui_with_wheel(self, event: Any) -> str:
        amount = -1 if event.delta > 0 else 1
        if event.state & 0x0001:
            self.ui_canvas.xview_scroll(amount, "units")
        else:
            self.ui_canvas.yview_scroll(amount, "units")
        return "break"

    def restart_program(self) -> None:
        if not messagebox.askyesno(
            "Restart to load code",
            "Restart the program now? Any unsaved recording or profile changes will be lost.",
        ):
            return
        try:
            subprocess.Popen(
                [sys.executable, *sys.argv],
                cwd=Path.cwd(),
                close_fds=True,
            )
        except OSError as error:
            messagebox.showerror("Restart failed", str(error))
            return
        self.close()

    def _refresh_background_targets(self) -> None:
        if Quartz is None:
            return
        try:
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
        except Exception as error:
            messagebox.showerror("Background target", f"Could not list windows: {error}")
            return

        targets: dict[str, tuple[int, int]] = {}
        for window in windows or []:
            pid = int(window.get(Quartz.kCGWindowOwnerPID, 0))
            window_id = int(window.get(Quartz.kCGWindowNumber, 0))
            if pid <= 0 or pid == os.getpid() or window_id <= 0 or int(window.get(Quartz.kCGWindowLayer, -1)) != 0:
                continue
            bounds = window.get(Quartz.kCGWindowBounds, {})
            if float(bounds.get("Width", 0)) <= 0 or float(bounds.get("Height", 0)) <= 0:
                continue
            owner = window.get(Quartz.kCGWindowOwnerName, "Application")
            title = window.get(Quartz.kCGWindowName) or "Window"
            label = f"{owner} — {title} (PID {pid}, window {window_id})"
            targets[label] = (pid, window_id)

        current = self.background_target_var.get()
        self.background_targets = targets
        values = ("Foreground input", *targets.keys())
        self.background_target_menu.configure(values=values)
        self.background_target_var.set(current if current in targets else "Foreground input")

    def _selected_background_target(self) -> tuple[int, int] | None:
        selection = self.background_target_var.get()
        return self.background_targets.get(selection)

    def _focus_target_window(self, pid: int, window_id: int) -> None:
        if Quartz is None:
            raise RuntimeError("Window focusing requires macOS Quartz")
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        window = next(
            (
                item
                for item in windows or []
                if int(item.get(Quartz.kCGWindowOwnerPID, 0)) == pid
                and int(item.get(Quartz.kCGWindowNumber, 0)) == window_id
            ),
            None,
        )
        if window is None:
            raise RuntimeError("The selected window is no longer available; refresh the window list")

        title = str(window.get(Quartz.kCGWindowName, ""))
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "System Events"\n'
            f"set targetProcess to first application process whose unix id is {pid}\n"
            "set frontmost of targetProcess to true\n"
            f'set targetWindow to first window of targetProcess whose name is "{escaped_title}"\n'
            'perform action "AXRaise" of targetWindow\n'
            'set value of attribute "AXMain" of targetWindow to true\n'
            'if frontmost of targetProcess is false then error "Target process did not become frontmost"\n'
            "end tell"
        )
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            details = getattr(error, "stderr", None) or str(error)
            raise RuntimeError(
                f"Could not focus the selected window. Enable Accessibility access for "
                f"System Events/osascript in macOS Privacy & Security settings. {details}"
            ) from error

    def _acquire_focus_file_lock(self) -> None:
        if self.focus_lock_held:
            return
        fcntl.flock(self.focus_lock_file.fileno(), fcntl.LOCK_EX)
        self.focus_lock_held = True

    def _release_focus_file_lock(self) -> None:
        if self.focus_lock_held:
            fcntl.flock(self.focus_lock_file.fileno(), fcntl.LOCK_UN)
            self.focus_lock_held = False

    def _ensure_target_focused(self, force: bool = False) -> None:
        if self.automation_target_pid is None or self.automation_target_window_id is None:
            return
        self._acquire_focus_file_lock()
        self.focus_lock_file.seek(0)
        try:
            state = json.loads(self.focus_lock_file.read() or "{}")
        except json.JSONDecodeError:
            state = {}
        target = [self.automation_target_pid, self.automation_target_window_id]
        now = time.monotonic()
        last_focused = float(state.get("focused_at", 0.0))
        if force or state.get("target") != target or now - last_focused >= self.automation_focus_interval:
            self._focus_target_window(*target)
            state = {
                "target": target,
                "focused_at": now,
                "lease_until": now + self.automation_focus_interval,
            }
            self.focus_lock_file.seek(0)
            self.focus_lock_file.truncate()
            json.dump(state, self.focus_lock_file)
            self.focus_lock_file.flush()
            self.last_focus_time = now

    def _begin_focused_input(self) -> None:
        if self.automation_target_pid is None:
            return
        owner = threading.get_ident()
        if self.focus_input_owner == owner:
            return
        while True:
            self.focus_input_mutex.acquire()
            self.focus_input_owner = owner
            try:
                self._acquire_focus_file_lock()
                self.focus_lock_file.seek(0)
                try:
                    state = json.loads(self.focus_lock_file.read() or "{}")
                except json.JSONDecodeError:
                    state = {}
                target = [self.automation_target_pid, self.automation_target_window_id]
                now = time.monotonic()
                lease_until = float(state.get("lease_until", 0.0))
                if (
                    state.get("target") != target
                    and lease_until > now
                    and not self.automation_stop.is_set()
                ):
                    remaining = min(lease_until - now, 0.25)
                    self._release_focus_file_lock()
                    self.focus_input_owner = None
                    self.focus_input_mutex.release()
                    self.automation_stop.wait(remaining)
                    continue
                should_refocus = (
                    state.get("target") != target
                    or lease_until <= now
                    or now - self.last_focus_time >= self.automation_focus_interval
                )
                self._ensure_target_focused(force=should_refocus)
                return
            except Exception:
                self._release_focus_file_lock()
                self.focus_input_owner = None
                self.focus_input_mutex.release()
                raise

    def _release_focused_input(self) -> None:
        if self.focus_input_owner == threading.get_ident():
            self._release_focus_file_lock()
            self.focus_input_owner = None
            self.focus_input_mutex.release()

    def _focus_target_if_due(self) -> None:
        if (
            self.automation_target_pid is None
            or time.monotonic() - self.last_focus_time < self.automation_focus_interval
        ):
            return
        self._begin_focused_input()
        self._release_focused_input()

    def _target_window_image(self, window_id: int) -> tuple[Any, dict[str, float]]:
        if Quartz is None or Image is None:
            raise RuntimeError("Background window capture requires macOS Quartz and Pillow")
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        window = next(
            (item for item in windows or [] if int(item.get(Quartz.kCGWindowNumber, 0)) == window_id),
            None,
        )
        if window is None:
            raise RuntimeError("The selected background window is no longer available; refresh the window list")
        bounds = window.get(Quartz.kCGWindowBounds, {})
        image_ref = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window_id,
            Quartz.kCGWindowImageBestResolution,
        )
        if image_ref is None:
            raise RuntimeError("Could not capture the selected window; check Screen Recording permission")
        width = Quartz.CGImageGetWidth(image_ref)
        height = Quartz.CGImageGetHeight(image_ref)
        row_bytes = Quartz.CGImageGetBytesPerRow(image_ref)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image_ref))
        bitmap = Image.frombytes("RGBA", (width, height), bytes(data), "raw", "BGRA", row_bytes)
        return bitmap.convert("RGB"), bounds

    def _sample_pixel(self, point: tuple[int, int] | list[int]) -> tuple[int, int, int]:
        target = (
            (self.automation_target_pid, self.automation_target_window_id)
            if self.automation_target_pid is not None and self.automation_target_window_id is not None
            else self._selected_background_target()
        )
        if target is None:
            if ImageGrab is None:
                raise RuntimeError("Install Pillow first: pip install Pillow")
            x, y = map(int, point)
            image = ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).convert("RGB")
            return tuple(next(iter(image.getdata())))

        _, window_id = target
        image, bounds = self._target_window_image(window_id)
        scale_x = image.width / float(bounds["Width"])
        scale_y = image.height / float(bounds["Height"])
        x = int((float(point[0]) - float(bounds["X"])) * scale_x)
        y = int((float(point[1]) - float(bounds["Y"])) * scale_y)
        if not (0 <= x < image.width and 0 <= y < image.height):
            raise RuntimeError("Mana calibration point is outside the selected background window")
        return image.getpixel((x, y))

    def _record_event(self, event: dict[str, Any]) -> None:
        if not self.recording or self.playing:
            return
        now = time.monotonic()
        event["delay"] = 0.0 if self.last_event_time is None else min(now - self.last_event_time, 30.0)
        self.last_event_time = now
        with self.events_lock:
            self.events.append(event)
            if self.event_refresh_pending:
                return
            self.event_refresh_pending = True
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        self._schedule_in_session(session_id, self._refresh_events, delay_ms=200)

    def _on_move(self, x: int, y: int) -> None:
        # Sample movement rather than recording every OS mouse event.
        now = time.monotonic()
        if self.last_move_time is not None and now - self.last_move_time < 0.02:
            return
        self.last_move_time = now
        self._record_event({"type": "move", "x": x, "y": y})

    def _on_click(self, x: int, y: int, button: Button, pressed: bool) -> None:
        self._record_event({"type": "click", "x": x, "y": y, "button": button.name, "pressed": pressed})

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._record_event({"type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

    def _on_press(self, key: Key | KeyCode) -> None:
        session_id = self._active_session_id
        if key == Key.f1:
            self._schedule_in_session(session_id, self.calibrate_point, "mana_point")
            return
        if key == Key.f2:
            self._schedule_in_session(session_id, self.calibrate_mana_color)
            return
        if key == Key.f6:
            self._schedule_in_session(
                session_id,
                self.calibrate_point,
                "bp1",
                delay_seconds=1.5,
            )
            return
        if key == Key.f7:
            self._schedule_in_session(
                session_id,
                self.calibrate_point,
                "bp2",
                delay_seconds=1.5,
            )
            return
        if key == Key.f8:
            self._schedule_in_session(session_id, self.toggle_recording)
            return
        if key == Key.f9:
            self._schedule_in_session(session_id, self.start_playback)
            return
        if key == Key.f10:
            self._schedule_in_session(session_id, self.clear)
            return
        if key == Key.f11:
            self._schedule_in_session(session_id, self.stop)
            return
        if self.recording and not self.playing:
            self._record_event({"type": "key_down", "key": self._serialize_key(key)})

    def _on_release(self, key: Key | KeyCode) -> None:
        if key in (Key.f1, Key.f2, Key.f6, Key.f7, Key.f8, Key.f9, Key.f10, Key.f11):
            return
        if self.recording and not self.playing:
            self._record_event({"type": "key_up", "key": self._serialize_key(key)})

    @staticmethod
    def _serialize_key(key: Key | KeyCode) -> dict[str, str]:
        if isinstance(key, Key):
            return {"kind": "special", "value": key.name}
        return {"kind": "char", "value": key.char or ""}

    @staticmethod
    def _deserialize_key(data: dict[str, str]) -> Key | KeyCode:
        if data["kind"] == "special":
            return Key[data["value"]]
        return KeyCode(char=data["value"])

    def toggle_recording(self) -> None:
        if self.playing:
            return
        if self.recording:
            self.recording = False
            self.last_event_time = None
            self.status.set("Recording stopped")
            if getattr(self._session_context, "session_id", self._active_session_id) == self._active_session_id:
                self.record_button.configure(text="●  Record (F8)")
        else:
            self.events.clear()
            self._refresh_events()
            self.last_event_time = None
            self.last_move_time = None
            self.recording = True
            self.status.set("Recording…")
            if getattr(self._session_context, "session_id", self._active_session_id) == self._active_session_id:
                self.record_button.configure(text="■  Stop (F8)")

    def start_playback(self) -> None:
        if self.recording or self.playing or self.automation_running or not self.events:
            return
        self.playing = True
        self.stop_playback.clear()
        self.playback_error = None
        self.status.set("Repeating… (F11 to stop)")
        if getattr(self._session_context, "session_id", self._active_session_id) == self._active_session_id:
            self.play_button.configure(state="disabled")
        threading.Thread(
            target=self._run_in_session,
            args=(self._active_session_id, self._playback),
            daemon=True,
        ).start()

    def _playback(self) -> None:
        try:
            # Replay the same recording over and over until F11 is pressed.
            while not self.stop_playback.is_set():
                for recorded_event in self.events:
                    if self.stop_playback.wait(recorded_event["delay"]):
                        return
                    kind = recorded_event["type"]
                    if kind == "move":
                        self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
                    elif kind == "click":
                        self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
                        button = Button[recorded_event["button"]]
                        action = self.mouse_controller.press if recorded_event["pressed"] else self.mouse_controller.release
                        action(button)
                    elif kind == "scroll":
                        self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
                        self.mouse_controller.scroll(recorded_event["dx"], recorded_event["dy"])
                    elif kind == "key_down":
                        self.keyboard_controller.press(self._deserialize_key(recorded_event["key"]))
                    elif kind == "key_up":
                        self.keyboard_controller.release(self._deserialize_key(recorded_event["key"]))
        except Exception as error:
            self.playback_error = f"{type(error).__name__}: {error}"
        finally:
            session_id = getattr(self._session_context, "session_id", self._active_session_id)
            self._schedule_in_session(session_id, self._playback_finished)

    def _playback_finished(self) -> None:
        self.playing = False
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        if session_id == self._active_session_id:
            self.play_button.configure(state="normal")
        if self.playback_error:
            self.status.set(f"Replay error: {self.playback_error}")
        else:
            self.status.set("Replay stopped")

    def stop(self) -> None:
        if self.recording:
            self.toggle_recording()
        if self.playing:
            self.stop_playback.set()
        self.stop_automation()

    def clear(self) -> None:
        if self.recording or self.playing or self.automation_running:
            return
        self.events.clear()
        self._refresh_events()
        self.status.set("Ready")

    def _refresh_events(self) -> None:
        with self.events_lock:
            self.event_refresh_pending = False
            events = self.events[-200:]
            event_count = len(self.events)
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        if session_id != self._active_session_id:
            return
        self.events_view.delete(0, tk.END)
        for event in events:
            kind = event["type"]
            if kind in ("key_down", "key_up"):
                label = f"{kind}: {event['key']['value']}"
            elif kind == "click":
                label = f"{kind}: {event['button']} {'down' if event['pressed'] else 'up'} @ ({event['x']}, {event['y']})"
            elif kind == "move":
                label = f"move: ({event['x']}, {event['y']})"
            else:
                label = f"scroll: ({event['dx']}, {event['dy']})"
            self.events_view.insert(tk.END, label)
        self.event_count.set(f"{event_count} event{'s' if event_count != 1 else ''}")
        self.events_view.yview_moveto(1.0)

    def close(self) -> None:
        for session_id in tuple(self._sessions):
            self._run_in_session(session_id, self.stop)
        self.mouse_listener.stop()
        self.keyboard_listener.stop()
        self.focus_lock_file.close()
        self.root.destroy()

    # ------------------------------------------------------------------
    # Persistent recordings and calibration profile

    def save_recording(self) -> None:
        if not self.events:
            messagebox.showinfo("Save recording", "There are no recorded events yet.")
            return
        filename = filedialog.asksaveasfilename(
            title="Save recording",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        if not filename:
            return
        Path(filename).write_text(json.dumps({"version": 1, "events": self.events}, indent=2), encoding="utf-8")
        self.status.set(f"Saved recording: {Path(filename).name}")

    def load_recording(self) -> None:
        filename = filedialog.askopenfilename(
            title="Load recording",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            events = data.get("events", data) if isinstance(data, dict) else data
            if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
                raise ValueError("recording must contain an events array")
            self.events = events
            self._refresh_events()
            self.status.set(f"Loaded recording: {Path(filename).name}")
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            messagebox.showerror("Load recording", str(error))

    def save_profile(self) -> None:
        try:
            self._read_profile_fields()
        except ValueError as error:
            messagebox.showerror("Save profile", str(error))
            return
        filename = filedialog.asksaveasfilename(
            title="Save automation profile",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        if not filename:
            return
        Path(filename).write_text(json.dumps(self.profile, indent=2), encoding="utf-8")
        self.status.set(f"Saved profile: {Path(filename).name}")

    def load_profile(self) -> None:
        filename = filedialog.askopenfilename(
            title="Load automation profile",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("profile must be a JSON object")
            self.profile.update(data)
            discovered = sorted(
                (key for key in self.profile if re.fullmatch(r"bp\d+", key)),
                key=lambda key: int(key[2:]),
            )
            self.backpack_names = discovered or ["bp1", "bp2"]
            for name in ("bp1", "bp2"):
                self.profile.setdefault(name, {"first_slot": None, "rows": 4, "columns": 5})
            self._rebuild_backpack_buttons()
            self.threshold_var.set(str(self.profile.get("mana_threshold_percent", 80)))
            self.slot_size_var.set(f"{self.profile.get('slot_width', 34)} x {self.profile.get('slot_height', 34)}")
            self.automation_slot = int(self.profile.get("start_slot", 1))
            self.automation_backpack = 0
            self._update_profile_status()
            self.status.set(f"Loaded profile: {Path(filename).name}")
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            messagebox.showerror("Load profile", str(error))

    def _read_profile_fields(self) -> None:
        try:
            threshold = float(self.threshold_var.get())
        except ValueError as error:
            raise ValueError("Mana threshold must be a number") from error
        if not 0 <= threshold <= 100:
            raise ValueError("Mana threshold must be between 0 and 100")
        self.profile["mana_threshold_percent"] = threshold

    def calibrate_point(self, target: str, delay_seconds: float = 0.0) -> None:
        if delay_seconds > 0:
            self.status.set(f"Move the cursor to {target.upper()} first slot… capturing in {delay_seconds:.1f}s")
            session_id = getattr(self._session_context, "session_id", self._active_session_id)
            self._schedule_in_session(
                session_id,
                self._capture_point,
                target,
                delay_ms=int(delay_seconds * 1000),
            )
            return
        self.status.set(f"Capturing {target.replace('_', ' ')} from the current cursor position")
        self._capture_point(target)

    def _capture_point(self, target: str) -> None:
        point = [int(round(value)) for value in self.mouse_controller.position]
        if target in self.backpack_names:
            self.profile[target]["first_slot"] = point
            self.status.set(f"{target.upper()} first slot set to {tuple(point)}")
        else:
            self.profile[target] = point
            self.status.set(f"{target.replace('_', ' ').title()} set to {tuple(point)}")
        self._update_profile_status()

    def _rebuild_backpack_buttons(self) -> None:
        if not hasattr(self, "backpack_calibration_frame"):
            return
        for child in self.backpack_calibration_frame.winfo_children():
            child.destroy()
        for index, name in enumerate(self.backpack_names):
            ttk.Button(
                self.backpack_calibration_frame,
                text=f"Set {name.upper()} first slot",
                command=lambda backpack=name: self.calibrate_point(backpack, delay_seconds=1.5),
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 8), pady=2)

    def add_backpack(self) -> None:
        next_number = len(self.backpack_names) + 1
        name = f"bp{next_number}"
        self.profile[name] = {"first_slot": None, "rows": 4, "columns": 5}
        self.backpack_names.append(name)
        self._rebuild_backpack_buttons()
        self._update_profile_status()
        self.status.set(f"Added {name.upper()}; calibrate its first slot")

    def calibrate_mana_color(self) -> None:
        self.status.set("Capturing the exact filled-mana pixel under the cursor")
        self._capture_mana_color()

    def _capture_mana_color(self) -> None:
        if ImageGrab is None and (self._selected_background_target() is None or Image is None):
            messagebox.showerror("Mana color", "Install Pillow first: pip install Pillow")
            return
        x, y = [int(round(value)) for value in self.mouse_controller.position]
        try:
            color = self._sample_pixel((x, y))
        except Exception as error:
            messagebox.showerror(
                "Mana color",
                "Could not capture the selected pixel. Check Screen Recording permission, "
                "then retry.\n\n"
                f"{type(error).__name__}: {error}",
            )
            return
        self.profile["mana_color"] = list(color)
        self.status.set(f"Mana color captured: {tuple(self.profile['mana_color'])}")
        self._update_profile_status()

    def _update_profile_status(self) -> None:
        missing = []
        for name in self.backpack_names:
            if not self.profile[name].get("first_slot"):
                missing.append(name.upper())
        if not self.profile.get("left_hand"):
            missing.append("left hand")
        if not self.profile.get("mana_point"):
            missing.append("mana point")
        if not self.profile.get("mana_color"):
            missing.append("mana color")
        self.profile_status.set("Ready" if not missing else "Calibrate: " + ", ".join(missing))

    # ------------------------------------------------------------------
    # Rune automation

    def start_automation(self) -> None:
        if self.recording or self.playing or self.automation_running:
            return
        timed_runes = self.timed_runes_var.get()
        target = self._selected_background_target()
        if not timed_runes and target is None and ImageGrab is None:
            messagebox.showerror("Rune automation", "Install Pillow first: pip install Pillow")
            return
        if target is not None and (Quartz is None or (not timed_runes and Image is None)):
            messagebox.showerror("Rune automation", "Window targeting requires macOS Quartz and Pillow")
            return
        try:
            self._read_profile_fields()
            focus_interval = float(self.focus_interval_var.get())
            if not 1 <= focus_interval <= 3600:
                raise ValueError("Refocus interval must be between 1 and 3600 seconds")
            loop_interval = self._read_recording_loop_interval()
            rune_interval = self._read_rune_interval() if timed_runes else 0.0
        except ValueError as error:
            messagebox.showerror("Rune automation", str(error))
            return
        required = [self.profile[name].get("first_slot") for name in self.backpack_names]
        required.append(self.profile.get("left_hand"))
        if not timed_runes:
            required.extend([self.profile.get("mana_point"), self.profile.get("mana_color")])
        if any(value is None for value in required):
            calibration = "every backpack and the left hand"
            if not timed_runes:
                calibration += ", mana point, and mana color"
            messagebox.showwarning("Rune automation", f"Calibrate {calibration} first.")
            return
        self.automation_target_pid, self.automation_target_window_id = target or (None, None)
        self.automation_focus_interval = focus_interval
        self.recording_loop_interval = loop_interval
        self.automation_timed_runes = timed_runes
        self.rune_interval = rune_interval
        self.last_focus_time = 0.0
        self.automation_running = True
        self.automation_stop.clear()
        self.background_pause.clear()
        self.status.set("Automation waiting for mana…")
        session_id = self._active_session_id
        if self.background_enabled.get() and self.events:
            self.background_thread = threading.Thread(
                target=self._run_in_session,
                args=(session_id, self._background_loop),
                daemon=True,
            )
            self.background_thread.start()
        threading.Thread(
            target=self._run_in_session,
            args=(session_id, self._automation_loop),
            daemon=True,
        ).start()

    def _read_recording_loop_interval(self) -> float:
        try:
            interval = float(self.recording_loop_interval_var.get())
        except ValueError as error:
            raise ValueError("Recording loop interval must be a number") from error
        if not 0 <= interval <= 3600:
            raise ValueError("Recording loop interval must be between 0 and 3600 seconds")
        return interval

    def _read_rune_interval(self) -> float:
        try:
            interval = float(self.rune_interval_var.get())
        except ValueError as error:
            raise ValueError("Rune interval must be a number") from error
        if not 1 <= interval <= 86400:
            raise ValueError("Rune interval must be between 1 and 86400 seconds")
        return interval

    def test_mana_point(self) -> None:
        if ImageGrab is None and (self._selected_background_target() is None or Image is None):
            messagebox.showerror("Mana test", "Install Pillow first: pip install Pillow")
            return
        if not self.profile.get("mana_point") or not self.profile.get("mana_color"):
            messagebox.showwarning("Mana test", "Set the mana check point and capture the mana color first.")
            return
        try:
            value = self._read_mana_percent()
            self.status.set(f"Mana point test: {'FILLED' if value >= 100.0 else 'not filled'} ({value:.0f}%)")
        except Exception as error:
            messagebox.showerror("Mana test", str(error))

    def stop_automation(self) -> None:
        if self.automation_running:
            self.automation_stop.set()
            self.background_pause.set()
            self.release_stuck_inputs()

    def _automation_loop(self) -> None:
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        try:
            if self.automation_timed_runes:
                self._timed_automation_loop()
                return
            while not self.automation_stop.is_set():
                self._focus_target_if_due()
                mana = self._read_mana_percent()
                if mana >= 100.0:
                    self.background_pause.set()
                    self.release_stuck_inputs()
                    if not self._rune_once():
                        self.automation_stop.set()
                        break
                    self._schedule_in_session(
                        session_id,
                        self.status.set,
                        f"Rune cast at {mana:.0f}% mana",
                    )
                    if self.automation_stop.wait(float(self.profile.get("cast_delay", 1.0))):
                        break
                else:
                    self.background_pause.clear()
                    self._schedule_in_session(
                        session_id,
                        self.status.set,
                        f"Waiting: mana {mana:.0f}%",
                    )
                    if self.automation_stop.wait(0.35):
                        break
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"
            self.root.after(0, lambda text=error_message: messagebox.showerror("Rune automation", text))
        finally:
            self.automation_running = False
            self.background_pause.set()
            self._schedule_in_session(session_id, self.status.set, "Automation stopped")

    def _timed_automation_loop(self) -> None:
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        self._focus_target_if_due()
        while not self.automation_stop.is_set():
            self._schedule_in_session(
                session_id,
                self.status.set,
                f"Next rune in {self.rune_interval:g} seconds",
            )
            if not self._wait_for_next_rune():
                return
            self.background_pause.set()
            self.release_stuck_inputs()
            if not self._rune_once():
                self.automation_stop.set()
                return
            self._schedule_in_session(session_id, self.status.set, "Rune made (timer mode)")
            self.background_pause.clear()

    def _wait_for_next_rune(self) -> bool:
        remaining = self.rune_interval
        while remaining > 0 and not self.automation_stop.is_set():
            self._focus_target_if_due()
            wait_interval = min(0.25, remaining)
            if self.automation_stop.wait(wait_interval):
                return False
            remaining -= wait_interval
        return not self.automation_stop.is_set()

    def _background_loop(self) -> None:
        """Loop a separately recorded fishing/eating macro while mana is low."""
        try:
            while not self.automation_stop.is_set():
                for recorded_event in self.events:
                    if self.automation_stop.is_set():
                        return
                    while (
                        self.background_pause.is_set()
                        and not self.automation_stop.is_set()
                        and not self.background_held_buttons
                        and not self.background_held_keys
                    ):
                        time.sleep(0.05)
                    remaining = float(recorded_event.get("delay", 0.0))
                    while remaining > 0 and not self.automation_stop.is_set():
                        if (
                            self.background_pause.is_set()
                            and not self.background_held_buttons
                            and not self.background_held_keys
                        ):
                            time.sleep(0.05)
                            continue
                        pause = min(remaining, 0.05)
                        time.sleep(pause)
                        remaining -= pause
                    if (
                        (not self.background_pause.is_set() or self.background_held_buttons or self.background_held_keys)
                        and not self.automation_stop.is_set()
                    ):
                        self._perform_event(recorded_event)
                self._wait_recording_loop_interval()
        finally:
            if self.background_held_buttons or self.background_held_keys:
                self.release_stuck_inputs()

    def _wait_recording_loop_interval(self) -> None:
        remaining = self.recording_loop_interval
        while remaining > 0 and not self.automation_stop.is_set():
            if (
                self.background_pause.is_set()
                and not self.background_held_buttons
                and not self.background_held_keys
            ):
                time.sleep(0.05)
                continue
            pause = min(remaining, 0.05)
            time.sleep(pause)
            remaining -= pause

    def _perform_event(self, recorded_event: dict[str, Any]) -> None:
        kind = recorded_event["type"]
        self._begin_focused_input()
        try:
            if kind == "move":
                self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
            elif kind == "click":
                self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
                button = Button[recorded_event["button"]]
                action = self.mouse_controller.press if recorded_event["pressed"] else self.mouse_controller.release
                action(button)
                if recorded_event["pressed"]:
                    self.background_held_buttons.add(button)
                    self.background_button_positions[button] = (
                        recorded_event["x"],
                        recorded_event["y"],
                    )
                else:
                    self.background_held_buttons.discard(button)
                    self.background_button_positions.pop(button, None)
            elif kind == "scroll":
                self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
                self.mouse_controller.scroll(recorded_event["dx"], recorded_event["dy"])
            elif kind == "key_down":
                key = self._deserialize_key(recorded_event["key"])
                self.keyboard_controller.press(key)
                self.background_held_keys.add(key)
            elif kind == "key_up":
                key = self._deserialize_key(recorded_event["key"])
                self.keyboard_controller.release(key)
                self.background_held_keys.discard(key)
        finally:
            if not self.background_held_buttons and not self.background_held_keys:
                self._release_focused_input()

    def release_stuck_inputs(self) -> None:
        """Release keys/buttons left down by a paused background recording."""
        self._begin_focused_input()
        try:
            keys = set(self.background_held_keys)
            for name in ("ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r", "alt", "alt_l", "alt_r", "cmd", "cmd_l", "cmd_r"):
                key = getattr(Key, name, None)
                if key is not None:
                    keys.add(key)
            for key in keys:
                try:
                    self.keyboard_controller.release(key)
                except Exception:
                    pass
            for button in set(self.background_held_buttons) | {Button.left, Button.right, Button.middle}:
                try:
                    self.mouse_controller.release(button)
                except Exception:
                    pass
            self.background_held_keys.clear()
            self.background_held_buttons.clear()
            self.background_button_positions.clear()
        finally:
            self._release_focused_input()

    def _read_mana_percent(self) -> float:
        point = self.profile.get("mana_point")
        if not point:
            raise RuntimeError("Mana point is not calibrated")
        try:
            current_color = self._sample_pixel(point)
        except Exception as error:
            raise RuntimeError(f"Could not read mana pixel: {error}") from error
        if not self.profile.get("mana_color"):
            raise RuntimeError("Mana color is not calibrated")
        target_color = tuple(self.profile["mana_color"])
        return 100.0 if current_color == target_color else 0.0

    def _rune_once(self) -> bool:
        # Ctrl can be held by the fishing/eating recording. Release it before
        # the drag so the client moves the rune instead of opening item look.
        self.release_stuck_inputs()
        backpack_name = self.backpack_names[self.automation_backpack]
        backpack = self.profile[backpack_name]
        max_slots = int(backpack.get("rows", 4)) * int(backpack.get("columns", 5))

        # Slot 0 is the backpack/container position; start at slot 1 and
        # advance through every remaining position without revisiting slots.
        if self.automation_slot >= max_slots:
            self.automation_backpack = (self.automation_backpack + 1) % len(self.backpack_names)
            self.automation_slot = int(self.profile.get("start_slot", 1))
            backpack_name = self.backpack_names[self.automation_backpack]
            backpack = self.profile[backpack_name]

        first_x, first_y = backpack["first_slot"]
        columns = int(backpack.get("columns", 5))
        slot_x = int(round(first_x + (self.automation_slot % columns) * int(self.profile.get("slot_width", 34))))
        slot_y = int(round(first_y + (self.automation_slot // columns) * int(self.profile.get("slot_height", 34))))
        hand_x, hand_y = [int(round(value)) for value in self.profile["left_hand"]]
        session_id = getattr(self._session_context, "session_id", self._active_session_id)
        self._schedule_in_session(
            session_id,
            self.status.set,
            f"Dragging {backpack_name} slot {self.automation_slot + 1}",
        )
        self._begin_focused_input()
        try:
            self._move_mouse(slot_x, slot_y)
            time.sleep(0.25)
            self._mouse_button("left", True, slot_x, slot_y)
            time.sleep(0.25)
            self.mouse_controller.move(hand_x - slot_x, hand_y - slot_y)
            time.sleep(0.25)
            self._mouse_button("left", False, hand_x, hand_y)
            self.keyboard_controller.press(Key.f5)
            self.keyboard_controller.release(Key.f5)
            self.automation_slot += 1
            return True
        finally:
            if Button.left in self.background_held_buttons:
                self.mouse_controller.release(Button.left)
                self.background_held_buttons.discard(Button.left)
                self.background_button_positions.pop(Button.left, None)
            self._release_focused_input()

    def _move_mouse(self, x: int, y: int) -> None:
        self.mouse_controller.position = (x, y)

    def _mouse_button(self, button: str, pressed: bool, x: int, y: int) -> None:
        self.mouse_controller.position = (x, y)
        mouse_button = Button[button]
        action = self.mouse_controller.press if pressed else self.mouse_controller.release
        action(mouse_button)
        if pressed:
            self.background_held_buttons.add(mouse_button)
            self.background_button_positions[mouse_button] = (x, y)
        else:
            self.background_held_buttons.discard(mouse_button)
            self.background_button_positions.pop(mouse_button, None)


def main() -> None:
    if tk is None:
        raise SystemExit(
            "Tkinter is missing. For Homebrew Python 3.13, run "
            "'brew install python-tk@3.13', then run this program again."
        )
    root = tk.Tk()
    MacroRecorder(root)
    root.mainloop()


if __name__ == "__main__":
    main()
