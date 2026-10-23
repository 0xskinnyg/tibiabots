import sys
import json
import tempfile
import threading
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

pynput = types.ModuleType("pynput")
keyboard_mod = types.ModuleType("pynput.keyboard")
mouse_mod = types.ModuleType("pynput.mouse")

class DummyKey:
    def __init__(self, name=None):
        self.name = name

class DummyKeyCode:
    def __init__(self, char=None):
        self.char = char

class DummyButton:
    left = "left"
    right = "right"
    middle = "middle"

class DummyController:
    def __init__(self, *args, **kwargs):
        pass

class DummyListener:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

keyboard_mod.Key = DummyKey
keyboard_mod.KeyCode = DummyKeyCode
keyboard_mod.Controller = DummyController
keyboard_mod.Listener = DummyListener
mouse_mod.Button = DummyButton
mouse_mod.Controller = DummyController
mouse_mod.Listener = DummyListener
pynput.keyboard = keyboard_mod
pynput.mouse = mouse_mod
sys.modules["pynput"] = pynput
sys.modules["pynput.keyboard"] = keyboard_mod
sys.modules["pynput.mouse"] = mouse_mod

import macro_recorder


class FakeGrab:
    def __init__(self, pixels):
        self.pixels = pixels

    def convert(self, _mode):
        return self

    def getdata(self):
        return iter(self.pixels)


class MacroRecorderColorTests(unittest.TestCase):
    def test_capture_uses_exact_pixel_not_block_average(self):
        original_grab = macro_recorder.ImageGrab
        original_mouse = macro_recorder.mouse

        pixels = [(20, 30, 40)]

        def fake_grab(bbox=None):
            return FakeGrab(pixels)

        macro_recorder.ImageGrab = SimpleNamespace(grab=fake_grab)
        macro_recorder.mouse = SimpleNamespace(Controller=lambda: None)

        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.profile = {"mana_point": (10, 10), "mana_color": None}
        recorder.mouse_controller = SimpleNamespace(position=(10, 10))
        recorder.automation_target_pid = None
        recorder.automation_target_window_id = None
        recorder.background_target_var = SimpleNamespace(get=lambda: "Foreground input")
        recorder.background_targets = {}
        recorder.status = SimpleNamespace(set=lambda *args, **kwargs: None)
        recorder._update_profile_status = lambda: None

        recorder._capture_mana_color()

        self.assertEqual(tuple(recorder.profile["mana_color"]), (20, 30, 40))
        self.assertEqual(recorder._read_mana_percent(), 100.0)

        macro_recorder.ImageGrab = original_grab
        macro_recorder.mouse = original_mouse


class MacroRecorderAutomationTests(unittest.TestCase):
    def test_recording_batches_ui_refreshes_and_limits_visible_history(self):
        callbacks = []
        inserted_rows = []
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.recording = True
        recorder.playing = False
        recorder.events = []
        recorder.events_lock = threading.Lock()
        recorder.event_refresh_pending = False
        recorder.last_event_time = None
        recorder.root = SimpleNamespace(after=lambda delay, callback: callbacks.append((delay, callback)))
        recorder.events_view = SimpleNamespace(
            delete=lambda *_args: inserted_rows.clear(),
            insert=lambda _index, value: inserted_rows.append(value),
            yview_moveto=lambda *_args: None,
        )
        recorder.event_count = SimpleNamespace(set=lambda value: setattr(recorder, "event_count_text", value))

        for index in range(1000):
            recorder._record_event({"type": "move", "x": index, "y": index})

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(callbacks[0][0], 200)
        callbacks.pop()[1]()

        self.assertEqual(len(inserted_rows), 200)
        self.assertEqual(inserted_rows[0], "move: (800, 800)")
        self.assertEqual(inserted_rows[-1], "move: (999, 999)")
        self.assertEqual(recorder.event_count_text, "1000 events")

    def test_start_automation_preserves_current_rune_slot(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.recording = False
        recorder.playing = False
        recorder.automation_running = False
        recorder.profile = {
            "bp1": {"first_slot": (10, 10)},
            "bp2": {"first_slot": (20, 20)},
            "left_hand": (30, 30),
            "mana_point": (40, 40),
            "mana_color": (1, 2, 3),
            "start_slot": 1,
        }
        recorder.backpack_names = ["bp1", "bp2"]
        recorder.background_target_var = SimpleNamespace(get=lambda: "Foreground input")
        recorder.background_targets = {}
        recorder.focus_interval_var = SimpleNamespace(get=lambda: "10")
        recorder.automation_slot = 7
        recorder.automation_backpack = 1
        recorder.automation_stop = threading.Event()
        recorder.background_pause = threading.Event()
        recorder.background_enabled = SimpleNamespace(get=lambda: False)
        recorder.events = []
        recorder.status = SimpleNamespace(set=lambda *_args: None)
        recorder._read_profile_fields = lambda: None
        recorder.root = SimpleNamespace()
        thread = Mock(return_value=SimpleNamespace(start=Mock()))

        with patch.object(macro_recorder, "ImageGrab", object()):
            with patch.object(macro_recorder, "threading", SimpleNamespace(Thread=thread)):
                recorder.start_automation()

        self.assertTrue(recorder.automation_running)
        self.assertEqual(recorder.automation_slot, 7)
        self.assertEqual(recorder.automation_backpack, 1)
        thread.return_value.start.assert_called_once()

    def test_window_selected_input_uses_normal_foreground_mouse(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.automation_target_pid = 123
        recorder.mouse_controller = SimpleNamespace(position=(12, 34))
        recorder.background_held_buttons = set()
        recorder.background_button_positions = {}
        recorder.background_held_keys = set()
        recorder._begin_focused_input = Mock()
        recorder._release_focused_input = Mock()

        recorder._perform_event({"type": "move", "x": 200, "y": 300})

        self.assertEqual(recorder.mouse_controller.position, (200, 300))
        recorder._begin_focused_input.assert_called_once()
        recorder._release_focused_input.assert_called_once()

    def test_focus_target_raises_selected_window_by_pid_and_title(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {"pid": 123, "window_id": 456, "name": 'Game "One"'}
            ],
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        with patch.object(macro_recorder, "Quartz", fake_quartz):
            with patch.object(macro_recorder.subprocess, "run") as run:
                recorder._focus_target_window(123, 456)

        script = run.call_args.args[0][2]
        self.assertIn("unix id is 123", script)
        self.assertIn('name is "Game \\"One\\""', script)
        self.assertIn('perform action "AXRaise"', script)

    def test_focus_is_reused_until_configured_interval_expires(self):
        with tempfile.TemporaryFile(mode="w+") as focus_state:
            recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
            recorder.automation_target_pid = 123
            recorder.automation_target_window_id = 456
            recorder.automation_focus_interval = 10
            recorder.last_focus_time = 0
            recorder.focus_lock_file = focus_state
            recorder.focus_lock_held = False
            recorder._focus_target_window = Mock()

            recorder._ensure_target_focused()
            recorder._release_focus_file_lock()
            recorder._ensure_target_focused()
            recorder._release_focus_file_lock()

            self.assertEqual(recorder._focus_target_window.call_count, 1)
            focus_state.seek(0)
            state = json.loads(focus_state.read())
            self.assertEqual(state["target"], [123, 456])
            self.assertGreater(state["focused_at"], 0)

    def test_background_mana_sample_maps_screen_point_into_window_image(self):
        image = SimpleNamespace(
            width=100,
            height=80,
            getpixel=Mock(return_value=(10, 20, 30)),
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.automation_target_pid = 123
        recorder.automation_target_window_id = 456
        recorder._target_window_image = Mock(
            return_value=(image, {"X": 100, "Y": 200, "Width": 50, "Height": 40})
        )

        color = recorder._sample_pixel((110, 210))

        self.assertEqual(color, (10, 20, 30))
        image.getpixel.assert_called_once_with((20, 20))


if __name__ == "__main__":
    unittest.main()
