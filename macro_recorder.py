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
import re
from pathlib import Path
from typing import Any

from pynput import keyboard, mouse
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None  # type: ignore[assignment]

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError as error:
    if error.name != "_tkinter":
        raise
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]


class MacroRecorder:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Macro Recorder")
        self.root.geometry("720x700")
        self.root.minsize(620, 560)

        self.mouse_controller = mouse.Controller()
        self.keyboard_controller = keyboard.Controller()
        self.events: list[dict[str, Any]] = []
        self.recording = False
        self.playing = False
        self.stop_playback = threading.Event()
        self.last_event_time: float | None = None
        self.last_move_time: float | None = None
        self.playback_error: str | None = None
        self.automation_running = False
        self.automation_stop = threading.Event()
        self.background_pause = threading.Event()
        self.background_thread: threading.Thread | None = None
        self.background_held_keys: set[Key | KeyCode] = set()
        self.background_held_buttons: set[Button] = set()
        self.automation_backpack = 0
        self.automation_slot = 1
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
        self.threshold_var = tk.StringVar(value="80")
        self.slot_size_var = tk.StringVar(value="34 x 34")
        self.profile_status = tk.StringVar(value="Profile not calibrated")
        self.background_enabled = tk.BooleanVar(value=False)

        self.status = tk.StringVar(value="Ready")
        self.event_count = tk.StringVar(value="0 events")
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

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Macro Recorder", font=(".AppleSystemUIFont", 20, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Record actions across macOS apps and replay them later.",
        ).pack(anchor="w", pady=(2, 16))

        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        self.record_button = ttk.Button(controls, text="●  Record (F8)", command=self.toggle_recording)
        self.record_button.pack(side="left", padx=(0, 8))
        self.play_button = ttk.Button(controls, text="▶  Replay (F9)", command=self.start_playback)
        self.play_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="■  Stop (F11)", command=self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Clear (F10)", command=self.clear).pack(side="left")

        files = ttk.Frame(frame)
        files.pack(fill="x", pady=(10, 0))
        ttk.Button(files, text="Save Recording", command=self.save_recording).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Load Recording", command=self.load_recording).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Save Profile", command=self.save_profile).pack(side="left", padx=(0, 6))
        ttk.Button(files, text="Load Profile", command=self.load_profile).pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=18)
        status_row = ttk.Frame(frame)
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status, font=(".AppleSystemUIFont", 13, "bold")).pack(side="left")
        ttk.Label(status_row, textvariable=self.event_count).pack(side="right")

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
        ttk.Entry(automation, width=7, textvariable=self.threshold_var).grid(row=0, column=1, sticky="w", padx=(6, 14))
        ttk.Label(automation, textvariable=self.slot_size_var).grid(row=0, column=2, sticky="w")
        ttk.Button(automation, text="Start automation", command=self.start_automation).grid(row=0, column=3, padx=(12, 5))
        ttk.Button(automation, text="Stop automation", command=self.stop_automation).grid(row=0, column=4)
        ttk.Checkbutton(automation, text="Loop loaded fishing/eating recording while waiting", variable=self.background_enabled).grid(row=1, column=0, columnspan=5, sticky="w", pady=(7, 0))

        calibration = ttk.Frame(automation)
        calibration.grid(row=2, column=0, columnspan=5, sticky="w", pady=(10, 0))
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
        ttk.Label(automation, textvariable=self.profile_status, foreground="#666666").grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Button(automation, text="Test mana point", command=self.test_mana_point).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(automation, text="Reset held keys", command=self.release_stuck_inputs).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

    def _record_event(self, event: dict[str, Any]) -> None:
        if not self.recording or self.playing:
            return
        now = time.monotonic()
        event["delay"] = 0.0 if self.last_event_time is None else min(now - self.last_event_time, 30.0)
        self.last_event_time = now
        self.events.append(event)
        self.root.after(0, self._refresh_events)

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
        if key == Key.f1:
            self.root.after(0, lambda: self.calibrate_point("mana_point"))
            return
        if key == Key.f2:
            self.root.after(0, self.calibrate_mana_color)
            return
        if key == Key.f6:
            self.root.after(0, lambda: self.calibrate_point("bp1", delay_seconds=1.5))
            return
        if key == Key.f7:
            self.root.after(0, lambda: self.calibrate_point("bp2", delay_seconds=1.5))
            return
        if key == Key.f8:
            self.root.after(0, self.toggle_recording)
            return
        if key == Key.f9:
            self.root.after(0, self.start_playback)
            return
        if key == Key.f10:
            self.root.after(0, self.clear)
            return
        if key == Key.f11:
            self.root.after(0, self.stop)
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
            self.record_button.configure(text="●  Record (F8)")
        else:
            self.events.clear()
            self._refresh_events()
            self.last_event_time = None
            self.last_move_time = None
            self.recording = True
            self.status.set("Recording…")
            self.record_button.configure(text="■  Stop (F8)")

    def start_playback(self) -> None:
        if self.recording or self.playing or self.automation_running or not self.events:
            return
        self.playing = True
        self.stop_playback.clear()
        self.playback_error = None
        self.status.set("Repeating… (F11 to stop)")
        self.play_button.configure(state="disabled")
        threading.Thread(target=self._playback, daemon=True).start()

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
            self.root.after(0, self._playback_finished)

    def _playback_finished(self) -> None:
        self.playing = False
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
        self.events_view.delete(0, tk.END)
        for event in self.events[-500:]:
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
        self.event_count.set(f"{len(self.events)} event{'s' if len(self.events) != 1 else ''}")
        self.events_view.yview_moveto(1.0)

    def close(self) -> None:
        self.stop()
        self.mouse_listener.stop()
        self.keyboard_listener.stop()
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
            self.root.after(int(delay_seconds * 1000), lambda: self._capture_point(target))
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
        if ImageGrab is None:
            messagebox.showerror("Mana color", "Install Pillow first: pip install Pillow")
            return
        x, y = [int(round(value)) for value in self.mouse_controller.position]
        try:
            sample = ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).convert("RGB")
        except Exception as error:
            messagebox.showerror(
                "Mana color",
                "Could not capture the screen at that position. "
                "Check Screen Recording permission for Python/Terminal, then retry.\n\n"
                f"{type(error).__name__}: {error}",
            )
            return
        self.profile["mana_color"] = list(next(iter(sample.getdata())))
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
        if ImageGrab is None:
            messagebox.showerror("Rune automation", "Install Pillow first: pip install Pillow")
            return
        try:
            self._read_profile_fields()
        except ValueError as error:
            messagebox.showerror("Rune automation", str(error))
            return
        required = [self.profile[name].get("first_slot") for name in self.backpack_names]
        required.extend([self.profile.get("left_hand"), self.profile.get("mana_point"), self.profile.get("mana_color")])
        if any(value is None for value in required):
            messagebox.showwarning("Rune automation", "Calibrate every backpack, the left hand, mana point, and mana color first.")
            return
        self.automation_running = True
        self.automation_stop.clear()
        self.background_pause.clear()
        self.status.set("Automation waiting for mana…")
        if self.background_enabled.get() and self.events:
            self.background_thread = threading.Thread(target=self._background_loop, daemon=True)
            self.background_thread.start()
        threading.Thread(target=self._automation_loop, daemon=True).start()

    def test_mana_point(self) -> None:
        if ImageGrab is None:
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
        try:
            while not self.automation_stop.is_set():
                mana = self._read_mana_percent()
                if mana >= 100.0:
                    self.background_pause.set()
                    self.release_stuck_inputs()
                    if not self._rune_once():
                        self.automation_stop.set()
                        break
                    self.root.after(0, lambda value=mana: self.status.set(f"Rune cast at {value:.0f}% mana"))
                    if self.automation_stop.wait(float(self.profile.get("cast_delay", 1.0))):
                        break
                else:
                    self.background_pause.clear()
                    self.root.after(0, lambda value=mana: self.status.set(f"Waiting: mana {value:.0f}%"))
                    if self.automation_stop.wait(0.35):
                        break
        except Exception as error:
            self.root.after(0, lambda: messagebox.showerror("Rune automation", f"{type(error).__name__}: {error}"))
        finally:
            self.automation_running = False
            self.background_pause.set()
            self.root.after(0, lambda: self.status.set("Automation stopped"))

    def _background_loop(self) -> None:
        """Loop a separately recorded fishing/eating macro while mana is low."""
        while not self.automation_stop.is_set():
            for recorded_event in self.events:
                if self.automation_stop.is_set():
                    return
                while self.background_pause.is_set() and not self.automation_stop.is_set():
                    time.sleep(0.05)
                remaining = float(recorded_event.get("delay", 0.0))
                while remaining > 0 and not self.automation_stop.is_set():
                    if self.background_pause.is_set():
                        time.sleep(0.05)
                        continue
                    pause = min(remaining, 0.05)
                    time.sleep(pause)
                    remaining -= pause
                if not self.background_pause.is_set() and not self.automation_stop.is_set():
                    self._perform_event(recorded_event)

    def _perform_event(self, recorded_event: dict[str, Any]) -> None:
        kind = recorded_event["type"]
        if kind == "move":
            self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
        elif kind == "click":
            self.mouse_controller.position = (recorded_event["x"], recorded_event["y"])
            button = Button[recorded_event["button"]]
            action = self.mouse_controller.press if recorded_event["pressed"] else self.mouse_controller.release
            action(button)
            if recorded_event["pressed"]:
                self.background_held_buttons.add(button)
            else:
                self.background_held_buttons.discard(button)
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

    def release_stuck_inputs(self) -> None:
        """Release keys/buttons left down by a paused background recording."""
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

    def _read_mana_percent(self) -> float:
        point = self.profile.get("mana_point")
        if not point:
            raise RuntimeError("Mana point is not calibrated")
        x, y = map(int, point)
        try:
            image = ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).convert("RGB")
        except Exception as error:
            raise RuntimeError("Screen capture failed; enable Screen Recording permission for Python/Terminal") from error
        if not self.profile.get("mana_color"):
            raise RuntimeError("Mana color is not calibrated")
        current_color = tuple(next(iter(image.getdata())))
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
        self.root.after(0, lambda: self.status.set(f"Dragging {backpack_name} slot {self.automation_slot + 1}"))
        self.mouse_controller.position = (slot_x, slot_y)
        time.sleep(0.25)
        self.mouse_controller.press(Button.left)
        try:
            time.sleep(0.25)
            self.mouse_controller.move(hand_x - slot_x, hand_y - slot_y)
            time.sleep(0.25)
        finally:
            self.mouse_controller.release(Button.left)
        self.keyboard_controller.press(Key.f5)
        self.keyboard_controller.release(Key.f5)
        self.automation_slot += 1
        return True


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
