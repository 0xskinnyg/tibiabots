import sys
import json
import tempfile
import threading
import time
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


for _key_name in ("f1", "f2", "f6", "f7", "f8", "f9", "f10", "f11"):
    setattr(DummyKey, _key_name, DummyKey(_key_name))

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

    def test_focused_selected_window_samples_actual_screen_pixel(self):
        original_image_grab = macro_recorder.ImageGrab
        original_quartz = macro_recorder.Quartz
        original_appkit = macro_recorder.AppKit
        screen_grab = Mock(return_value=FakeGrab([(21, 31, 41)]))
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowNumber="window_id",
            kCGWindowOwnerPID="pid",
            kCGWindowBounds="bounds",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {
                    "window_id": 456,
                    "pid": 123,
                    "bounds": {"X": 100, "Y": 200, "Width": 800, "Height": 600},
                }
            ],
        )
        frontmost_app = SimpleNamespace(processIdentifier=lambda: 123)
        fake_appkit = SimpleNamespace(
            NSWorkspace=SimpleNamespace(
                sharedWorkspace=lambda: SimpleNamespace(
                    frontmostApplication=lambda: frontmost_app
                )
            )
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.automation_target_pid = None
        recorder.automation_target_window_id = None
        recorder.background_target_var = SimpleNamespace(get=lambda: "Game")
        recorder.background_targets = {"Game": (123, 456)}
        recorder._target_window_image = Mock(side_effect=AssertionError("should use screen pixel"))
        macro_recorder.ImageGrab = SimpleNamespace(grab=screen_grab)
        macro_recorder.Quartz = fake_quartz
        macro_recorder.AppKit = fake_appkit
        try:
            sampled = recorder._sample_pixel((120, 230))
        finally:
            macro_recorder.ImageGrab = original_image_grab
            macro_recorder.Quartz = original_quartz
            macro_recorder.AppKit = original_appkit

        self.assertEqual(sampled, (21, 31, 41))
        screen_grab.assert_called_once_with(bbox=(120, 230, 121, 231))
        recorder._target_window_image.assert_not_called()

    def test_capture_uses_calibrated_mana_point_when_cursor_is_outside_selected_window(self):
        original_image_grab = macro_recorder.ImageGrab
        original_image = macro_recorder.Image
        original_messagebox = macro_recorder.messagebox
        errors = []
        statuses = []
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.profile = {
            "mana_point": [120, 230],
            "mana_color": None,
        }
        recorder.automation_target_pid = None
        recorder.automation_target_window_id = None
        recorder.mouse_controller = SimpleNamespace(position=(700, 800))
        recorder.background_target_var = SimpleNamespace(get=lambda: "Selected game")
        recorder.background_targets = {"Selected game": (123, 456)}
        recorder._target_process_is_frontmost = Mock(return_value=False)
        recorder.status = SimpleNamespace(set=statuses.append)
        recorder._update_profile_status = Mock()
        recorder._sample_pixel = Mock(return_value=(20, 30, 40))
        macro_recorder.ImageGrab = object()
        macro_recorder.Image = object()
        macro_recorder.messagebox = SimpleNamespace(showerror=lambda *args: errors.append(args))

        try:
            recorder._capture_mana_color()
        finally:
            macro_recorder.ImageGrab = original_image_grab
            macro_recorder.Image = original_image
            macro_recorder.messagebox = original_messagebox

        recorder._sample_pixel.assert_called_once_with([120, 230])
        self.assertEqual(recorder.profile["mana_color"], [20, 30, 40])
        self.assertIn("at the calibrated mana point", statuses[0])
        self.assertEqual(errors, [])
        recorder._update_profile_status.assert_called_once()


class MacroRecorderAutomationTests(unittest.TestCase):
    def test_start_automation_prepares_every_session_before_launching(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            f"session-{number}": macro_recorder.MacroSession(
                f"session-{number}",
                f"Session {number}",
                {"automation_running": False},
            )
            for number in (1, 2, 3)
        }
        recorder._session_context = threading.local()
        recorder._active_session_id = "session-2"
        recorder._coordinator_stop = threading.Event()
        recorder._coordinator_thread = None
        operations = []
        recorder._prepare_automation = Mock(
            side_effect=lambda: operations.append(
                ("prepare", recorder._session_context.session_id)
            )
            or True
        )
        recorder._launch_automation = Mock(
            side_effect=lambda: operations.append(
                ("launch", recorder._session_context.session_id)
            )
        )

        fake_thread = SimpleNamespace(start=Mock(), is_alive=Mock(return_value=False))
        with patch.object(
            macro_recorder.threading,
            "Thread",
            return_value=fake_thread,
        ) as thread_factory:
            recorder.start_automation()

        self.assertEqual(
            operations,
            [
                ("prepare", "session-1"),
                ("prepare", "session-2"),
                ("prepare", "session-3"),
                ("launch", "session-1"),
                ("launch", "session-2"),
                ("launch", "session-3"),
            ],
        )
        fake_thread.start.assert_called_once()
        thread_factory.assert_called_once_with(
            target=recorder._automation_coordinator,
            args=(tuple(recorder._sessions),),
            daemon=True,
        )

    def test_automation_coordinator_visits_sessions_in_order(self):
        session_ids = ("session-1", "session-2")
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            session_id: macro_recorder.MacroSession(
                session_id,
                session_id,
                {
                    "automation_running": True,
                    "background_running": False,
                    "automation_stop": threading.Event(),
                    "background_stop": threading.Event(),
                    "background_error": None,
                    "rune_error": None,
                },
            )
            for session_id in session_ids
        }
        recorder._session_context = threading.local()
        recorder._active_session_id = session_ids[0]
        recorder._coordinator_stop = threading.Event()
        recorder._schedule_in_session = Mock()
        recorder._run_in_session = macro_recorder.MacroRecorder._run_in_session.__get__(
            recorder, macro_recorder.MacroRecorder
        )
        visited = []

        def visit_session():
            visited.append(recorder._session_context.session_id)
            if len(visited) == len(session_ids):
                for state in recorder._sessions.values():
                    state.state["automation_stop"].set()
                    state.state["background_stop"].set()

        recorder._run_session_turn = visit_session

        recorder._automation_coordinator(session_ids)

        self.assertEqual(visited, ["session-1", "session-2"])
        self.assertTrue(all(not s.state["automation_running"] for s in recorder._sessions.values()))

    def test_session_turn_plays_one_recording_pass_then_yields(self):
        session_id = "session-1"
        session = macro_recorder.MacroSession(
            session_id,
            "Session 1",
            {
                "automation_running": False,
                "background_running": True,
                "background_stop": threading.Event(),
                "background_enabled": SimpleNamespace(get=lambda: True),
                "events": [
                    {"type": "move", "x": 1, "y": 2, "delay": 0},
                    {"type": "move", "x": 3, "y": 4, "delay": 0},
                ],
                "next_recording_at": 0.0,
                "recording_loop_interval": 0.0,
                "session_turn_active": False,
                "background_held_buttons": {DummyButton.left},
                "background_held_keys": set(),
            },
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {session_id: session}
        recorder._active_session_id = session_id
        recorder._session_context = threading.local()
        recorder._coordinator_stop = threading.Event()
        recorder._check_session_rune = Mock()
        recorder._perform_event = Mock()
        recorder.release_stuck_inputs = Mock()
        recorder._schedule_in_session = Mock()
        recorder._set_session_status = Mock()

        recorder._run_in_session(session_id, recorder._run_session_turn)

        self.assertEqual(recorder._perform_event.call_count, 2)
        self.assertEqual(recorder._check_session_rune.call_count, 3)
        self.assertGreater(session.state["next_recording_at"], 0)
        self.assertFalse(session.state["session_turn_active"])
        recorder.release_stuck_inputs.assert_called_once()
        recorder._schedule_in_session.assert_called_once()

    def test_session_turn_checks_due_timer_rune(self):
        session_id = "session-1"
        session = macro_recorder.MacroSession(
            session_id,
            "Session 1",
            {
                "automation_running": True,
                "automation_stop": threading.Event(),
                "automation_timed_runes": True,
                "next_rune_at": 0.0,
                "rune_interval": 60.0,
                "background_held_buttons": set(),
                "background_held_keys": set(),
            },
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {session_id: session}
        recorder._active_session_id = session_id
        recorder._session_context = threading.local()
        recorder._focus_target_if_due = Mock()
        recorder._rune_once = Mock(return_value=True)
        recorder._schedule_in_session = Mock()
        recorder.release_stuck_inputs = Mock()

        recorder._run_in_session(session_id, recorder._check_session_rune)

        recorder._rune_once.assert_called_once()
        self.assertGreater(session.state["next_rune_at"], time.monotonic())

    def test_start_all_runs_background_recording_for_each_enabled_session(self):
        sessions = {}
        for number in (1, 2):
            session_id = f"session-{number}"
            sessions[session_id] = macro_recorder.MacroSession(
                session_id,
                f"Session {number}",
                {
                    "automation_running": False,
                    "background_running": False,
                    "automation_stop": threading.Event(),
                    "background_pause": threading.Event(),
                    "background_stop": threading.Event(),
                    "status": SimpleNamespace(set=Mock()),
                    "automation_timed_runes": False,
                    "background_enabled": SimpleNamespace(get=lambda: True),
                    "events": [{"type": "move"}],
                },
            )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = sessions
        recorder._active_session_id = "session-2"
        recorder._session_context = threading.local()
        recorder._run_in_session = macro_recorder.MacroRecorder._run_in_session.__get__(
            recorder, macro_recorder.MacroRecorder
        )
        recorder._coordinator_stop = threading.Event()
        recorder._coordinator_thread = None
        fake_thread = SimpleNamespace(start=Mock(), is_alive=Mock(return_value=False))
        with patch.object(macro_recorder.threading, "Thread", return_value=fake_thread):
            for session_id in ("session-1", "session-2"):
                recorder._run_in_session(session_id, recorder._launch_automation)

        self.assertTrue(sessions["session-1"].state["background_running"])
        self.assertTrue(sessions["session-2"].state["background_running"])
        self.assertIsNone(recorder._coordinator_thread)

    def test_session_loop_waits_run_in_parallel(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._session_context = threading.local()
        recorder._sessions = {}
        waiting_barrier = threading.Barrier(2)

        class BarrierStop:
            def is_set(self):
                return False

            def wait(self, _timeout=None):
                waiting_barrier.wait(timeout=1)
                return False

        for session_id in ("session-1", "session-2"):
            recorder._sessions[session_id] = macro_recorder.MacroSession(
                session_id,
                session_id,
                {
                    "recording_loop_interval": 0.05,
                    "automation_stop": threading.Event(),
                    "background_stop": BarrierStop(),
                    "background_pause": threading.Event(),
                    "background_held_buttons": set(),
                    "background_held_keys": set(),
                },
            )

        completed = [threading.Event(), threading.Event()]
        threads = [
            threading.Thread(
                target=lambda index=index, session_id=session_id: recorder._run_in_session(
                    session_id,
                    lambda: (
                        recorder._wait_recording_loop_interval(),
                        completed[index].set(),
                    ),
                )
            )
            for index, session_id in enumerate(("session-1", "session-2"))
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(completed[0].wait(timeout=1))
        self.assertTrue(completed[1].wait(timeout=1))
        for thread in threads:
            thread.join(timeout=1)

        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_foreground_input_actions_still_take_exclusive_lock(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._session_context = threading.local()
        recorder.focus_input_mutex = threading.RLock()
        recorder.focus_input_owner = None
        recorder.focus_lock_held = False
        recorder._sessions = {
            session_id: macro_recorder.MacroSession(
                session_id,
                session_id,
                {
                    "automation_target_pid": None,
                    "automation_target_window_id": None,
                },
            )
            for session_id in ("session-1", "session-2")
        }
        first_has_lock = threading.Event()
        release_first = threading.Event()
        second_has_lock = threading.Event()

        def run_first():
            def action():
                recorder._begin_focused_input()
                first_has_lock.set()
                release_first.wait(timeout=1)
                recorder._release_focused_input()
            recorder._run_in_session("session-1", action)

        def run_second():
            def action():
                recorder._begin_focused_input()
                second_has_lock.set()
                recorder._release_focused_input()
            recorder._run_in_session("session-2", action)

        first = threading.Thread(target=run_first)
        second = threading.Thread(target=run_second)
        first.start()
        self.assertTrue(first_has_lock.wait(timeout=1))
        second.start()
        self.assertFalse(second_has_lock.wait(timeout=0.1))
        release_first.set()
        self.assertTrue(second_has_lock.wait(timeout=1))
        first.join(timeout=1)
        second.join(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

    def test_start_automation_does_not_launch_any_session_if_one_fails_validation(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            f"session-{number}": macro_recorder.MacroSession(
                f"session-{number}",
                f"Session {number}",
                {"automation_running": False},
            )
            for number in (1, 2)
        }
        recorder._session_context = threading.local()
        recorder._prepare_automation = Mock(side_effect=[True, False])
        recorder._launch_automation = Mock()

        recorder.start_automation()

        recorder._prepare_automation.assert_has_calls([unittest.mock.call(), unittest.mock.call()])
        recorder._launch_automation.assert_not_called()

    def test_stop_automation_signals_all_sessions_without_blocking_for_input_focus(self):
        stop_events = [threading.Event() for _ in range(3)]
        background_stop_events = [threading.Event() for _ in range(3)]
        pause_events = [threading.Event() for _ in range(3)]
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            f"session-{number}": macro_recorder.MacroSession(
                f"session-{number}",
                f"Session {number}",
                {
                    "automation_running": running,
                    "automation_stop": stop_events[number - 1],
                    "background_stop": background_stop_events[number - 1],
                    "background_pause": pause_events[number - 1],
                },
            )
            for number, running in ((1, True), (2, False), (3, True))
        }
        recorder._session_context = threading.local()
        recorder.release_stuck_inputs = Mock()

        recorder.stop_automation()

        self.assertTrue(stop_events[0].is_set())
        self.assertTrue(stop_events[1].is_set())
        self.assertTrue(stop_events[2].is_set())
        self.assertTrue(background_stop_events[0].is_set())
        self.assertTrue(background_stop_events[1].is_set())
        self.assertTrue(background_stop_events[2].is_set())
        self.assertTrue(pause_events[0].is_set())
        self.assertTrue(pause_events[1].is_set())
        self.assertTrue(pause_events[2].is_set())
        recorder.release_stuck_inputs.assert_not_called()

    def test_f11_signals_every_worker_before_scheduling_ui_stop(self):
        sessions = {
            f"session-{number}": macro_recorder.MacroSession(
                f"session-{number}",
                f"Session {number}",
                {
                    "automation_stop": threading.Event(),
                    "background_stop": threading.Event(),
                    "background_pause": threading.Event(),
                    "stop_playback": threading.Event(),
                },
            )
            for number in (1, 2)
        }
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = sessions
        recorder._active_session_id = "session-1"
        recorder._session_context = threading.local()
        recorder._coordinator_stop = threading.Event()
        recorder._schedule_in_session = Mock()

        recorder._on_press(DummyKey.f11)

        for session in sessions.values():
            self.assertTrue(session.state["automation_stop"].is_set())
            self.assertTrue(session.state["background_stop"].is_set())
            self.assertTrue(session.state["background_pause"].is_set())
            self.assertTrue(session.state["stop_playback"].is_set())
        recorder._schedule_in_session.assert_called_once_with(
            "session-1",
            recorder.stop,
        )

    def test_release_stuck_inputs_does_not_wait_for_window_focus(self):
        key = DummyKey("a")
        keyboard_controller = SimpleNamespace(release=Mock())
        mouse_controller = SimpleNamespace(release=Mock())
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.background_held_keys = {key}
        recorder.background_held_buttons = {DummyButton.left}
        recorder.background_button_positions = {DummyButton.left: (10, 20)}
        recorder.keyboard_controller = keyboard_controller
        recorder.mouse_controller = mouse_controller
        recorder.automation_target_pid = None
        recorder.automation_target_window_id = None
        recorder.focus_input_mutex = threading.RLock()
        recorder.focus_input_owner = None
        recorder.focus_lock_held = False
        recorder._focus_target_window = Mock()

        recorder.release_stuck_inputs()

        keyboard_controller.release.assert_called_with(key)
        mouse_controller.release.assert_any_call(DummyButton.left)
        self.assertFalse(recorder.background_held_keys)
        self.assertFalse(recorder.background_held_buttons)
        recorder._focus_target_window.assert_not_called()

    def test_new_session_gets_unique_numbered_name(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._next_session_number = 2
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession("session-1", "Session 1")
        }
        recorder._default_profile = {}
        recorder.session_selector = Mock()
        recorder._switch_session = Mock()

        make_variable = lambda value=None: SimpleNamespace(
            get=lambda: value,
            set=lambda _value: None,
        )
        with patch.object(macro_recorder.tk, "StringVar", side_effect=make_variable), patch.object(
            macro_recorder.tk, "BooleanVar", side_effect=make_variable
        ):
            recorder.add_session()

        self.assertIn("session-2", recorder._sessions)
        self.assertEqual(recorder._sessions["session-2"].name, "Session 2")
        recorder._switch_session.assert_called_once_with("session-2")

    def test_session_state_is_isolated_and_thread_context_selects_worker_session(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession(
                "session-1",
                "Session 1",
                {"events": ["server 1"], "profile": {"server": 1}},
            ),
            "session-2": macro_recorder.MacroSession(
                "session-2",
                "Session 2",
                {"events": ["server 2"], "profile": {"server": 2}},
            ),
        }
        recorder._active_session_id = "session-1"
        recorder._global_focus_interval = 10
        recorder._session_context = threading.local()

        self.assertEqual(recorder.events, ["server 1"])
        recorder._run_in_session("session-2", lambda: recorder.events.append("event 2"))
        self.assertEqual(recorder.events, ["server 1"])
        self.assertEqual(
            recorder._run_in_session("session-2", lambda: recorder.profile["server"]),
            2,
        )

    def test_hotkey_is_routed_to_active_session(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._active_session_id = "session-2"
        recorder._session_context = threading.local()
        recorder._schedule_in_session = Mock()

        recorder._on_press(DummyKey.f8)

        recorder._schedule_in_session.assert_called_once_with(
            "session-2",
            recorder.toggle_recording,
        )

    def test_rune_interval_accepts_positive_seconds_and_rejects_invalid_values(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.rune_interval_var = SimpleNamespace(get=lambda: "45.5")
        self.assertEqual(recorder._read_rune_interval(), 45.5)

        for value in ("", "0", "-1", "86401", "invalid"):
            with self.subTest(value=value):
                recorder.rune_interval_var = SimpleNamespace(get=lambda: value)
                with self.assertRaises(ValueError):
                    recorder._read_rune_interval()

    def test_timed_automation_casts_on_interval_without_reading_mana(self):
        statuses = []
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.automation_stop = threading.Event()
        recorder.rune_interval = 0.25
        recorder.background_pause = threading.Event()
        recorder.root = SimpleNamespace(after=lambda _delay, callback: callback())
        recorder.status = SimpleNamespace(set=statuses.append)
        recorder._active_session_id = "session-1"
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession(
                "session-1",
                "Session 1",
                {"status": recorder.status},
            )
        }
        recorder._session_context = threading.local()
        recorder._focus_target_if_due = Mock()
        recorder.release_stuck_inputs = Mock()
        recorder._rune_once = Mock(return_value=True)
        recorder._read_mana_percent = Mock(side_effect=AssertionError("mana should not be sampled"))

        with patch.object(recorder.automation_stop, "wait", side_effect=[False, True]) as wait:
            recorder._timed_automation_loop()

        self.assertEqual(wait.call_args_list, [unittest.mock.call(0.25), unittest.mock.call(0.25)])
        self.assertEqual(recorder._focus_target_if_due.call_count, 3)
        recorder._rune_once.assert_called_once()
        recorder._read_mana_percent.assert_not_called()
        self.assertEqual(statuses, ["Next rune in 0.25 seconds", "Rune made (timer mode)", "Next rune in 0.25 seconds"])
        self.assertFalse(recorder.background_pause.is_set())

    def test_timed_rune_wait_refocuses_during_long_interval(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.rune_interval = 1
        recorder.automation_stop = threading.Event()
        recorder._focus_target_if_due = Mock()

        with patch.object(recorder.automation_stop, "wait", side_effect=[False, False, False, True]) as wait:
            completed = recorder._wait_for_next_rune()

        self.assertFalse(completed)
        self.assertEqual(wait.call_args_list, [unittest.mock.call(0.25)] * 4)
        self.assertEqual(recorder._focus_target_if_due.call_count, 4)

    def test_recording_loop_interval_accepts_zero_and_positive_seconds(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.recording_loop_interval_var = SimpleNamespace(get=lambda: "2.5")
        self.assertEqual(recorder._read_recording_loop_interval(), 2.5)

        recorder.recording_loop_interval_var = SimpleNamespace(get=lambda: "0")
        self.assertEqual(recorder._read_recording_loop_interval(), 0)

    def test_recording_loop_interval_rejects_invalid_or_too_large_values(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        for value in ("", "abc", "-1", "3601"):
            with self.subTest(value=value):
                recorder.recording_loop_interval_var = SimpleNamespace(get=lambda: value)
                with self.assertRaises(ValueError):
                    recorder._read_recording_loop_interval()

    def test_recording_loop_wait_uses_configured_interval(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.recording_loop_interval = 0.1
        waits = []

        class RecordingStop:
            def is_set(self):
                return False

            def wait(self, duration):
                waits.append(duration)
                return False

        recorder.background_stop = RecordingStop()
        recorder.background_pause = threading.Event()
        recorder.background_held_buttons = set()
        recorder.background_held_keys = set()

        recorder._wait_recording_loop_interval()

        self.assertEqual(waits, [0.05, 0.05])

    def test_rune_failure_does_not_leave_fishing_loop_paused(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._active_session_id = "session-1"
        recorder._session_context = threading.local()
        recorder.automation_timed_runes = False
        recorder.automation_stop = threading.Event()
        recorder.background_stop = threading.Event()
        recorder.background_pause = threading.Event()
        recorder.background_pause.set()
        recorder.automation_running = True
        recorder.profile = {}
        recorder._focus_target_if_due = Mock()
        recorder._read_mana_percent = Mock(return_value=100)
        recorder.release_stuck_inputs = Mock()
        recorder._rune_once = Mock(return_value=False)
        statuses = []
        recorder.status = SimpleNamespace(set=statuses.append)
        session = macro_recorder.MacroSession(
            "session-1",
            "Session 1",
            {
                "automation_running": True,
                "automation_stop": recorder.automation_stop,
                "background_stop": recorder.background_stop,
                "background_pause": recorder.background_pause,
                "background_error": None,
                "status": recorder.status,
            },
        )
        recorder._sessions = {"session-1": session}
        recorder.root = SimpleNamespace(after=lambda _delay, callback: callback())
        recorder._run_in_session = macro_recorder.MacroRecorder._run_in_session.__get__(
            recorder, macro_recorder.MacroRecorder
        )

        recorder._automation_loop()

        self.assertTrue(recorder.automation_stop.is_set())
        self.assertFalse(recorder.background_stop.is_set())
        self.assertFalse(recorder.background_pause.is_set())
        self.assertIn("Rune automation stopped; fishing loop continues", statuses)

    def test_background_recording_error_stops_cleanly_and_reports_it(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._active_session_id = "session-1"
        recorder._session_context = threading.local()
        recorder.background_stop = threading.Event()
        recorder.automation_stop = threading.Event()
        recorder.background_pause = threading.Event()
        recorder.background_held_buttons = set()
        recorder.background_held_keys = set()
        recorder.events = [{"type": "move"}]
        recorder._perform_event = Mock(side_effect=RuntimeError("test recording failure"))
        statuses = []
        recorder.status = SimpleNamespace(set=statuses.append)
        recorder.root = SimpleNamespace(after=lambda _delay, callback: callback())
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession(
                "session-1",
                "Session 1",
                {
                    "status": recorder.status,
                    "automation_stop": recorder.automation_stop,
                    "background_stop": recorder.background_stop,
                    "background_pause": recorder.background_pause,
                    "background_error": None,
                },
            )
        }
        recorder._run_in_session = macro_recorder.MacroRecorder._run_in_session.__get__(
            recorder, macro_recorder.MacroRecorder
        )

        with patch.object(macro_recorder.messagebox, "showerror") as showerror:
            recorder._background_loop()

        self.assertTrue(recorder.background_stop.is_set())
        self.assertFalse(recorder.automation_stop.is_set())
        self.assertIn("recorded event 1/1 (move)", recorder.background_error)
        self.assertTrue(any("test recording failure" in status for status in statuses))
        recorder._set_session_status("session-1", "Waiting: mana 20%")
        self.assertEqual(statuses[-1], recorder.background_error)
        showerror.assert_called_once()
        self.assertIn("test recording failure", showerror.call_args.args[1])

    def test_mouse_wheel_scrolls_vertically_and_shift_wheel_horizontally(self):
        calls = []
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.ui_canvas = SimpleNamespace(
            xview_scroll=lambda amount, units: calls.append(("x", amount, units)),
            yview_scroll=lambda amount, units: calls.append(("y", amount, units)),
        )

        recorder._scroll_ui_with_wheel(SimpleNamespace(delta=120, state=0))
        recorder._scroll_ui_with_wheel(SimpleNamespace(delta=-120, state=1))

        self.assertEqual(calls, [("y", -1, "units"), ("x", 1, "units")])

    def test_restart_launches_current_python_and_script(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._build_restart_state = Mock(
            return_value={
                "version": 1,
                "active_session_id": "session-2",
                "sessions": [{"session_id": "session-2", "events": [{"type": "move"}]}],
            }
        )
        recorder.close = Mock()

        with patch.object(macro_recorder.messagebox, "askyesno", return_value=True):
            with patch.object(macro_recorder.subprocess, "Popen") as popen:
                with patch.object(macro_recorder.sys, "argv", ["macro_recorder.py", "--flag", "--restore-state", "old.json"]):
                    recorder.restart_program()

        command = popen.call_args.args[0]
        self.assertEqual(
            command[:3],
            [macro_recorder.sys.executable, "macro_recorder.py", "--flag"],
        )
        self.assertEqual(command[3], "--restore-state")
        snapshot_path = macro_recorder.Path(command[4])
        try:
            self.assertEqual(
                json.loads(snapshot_path.read_text(encoding="utf-8")),
                recorder._build_restart_state.return_value,
            )
        finally:
            snapshot_path.unlink(missing_ok=True)
        self.assertEqual(
            popen.call_args.kwargs,
            {"cwd": macro_recorder.Path.cwd(), "close_fds": True},
        )
        recorder.close.assert_called_once()

    def test_restart_snapshot_captures_every_session_and_selected_window(self):
        class FakeVariable:
            def __init__(self, value=None):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {}
        recorder._next_session_number = 1
        recorder._active_session_id = "session-2"
        recorder._session_context = threading.local()
        recorder._default_profile = {"bp1": {"first_slot": None}}
        recorder.background_targets = {"Target window": (123, 456)}
        recorder.global_focus_interval_var = FakeVariable("12")
        with patch.object(macro_recorder.tk, "StringVar", side_effect=FakeVariable), patch.object(
            macro_recorder.tk, "BooleanVar", side_effect=FakeVariable
        ):
            first = recorder._new_session("Server 1", "session-1")
            second = recorder._new_session("Server 2", "session-2")

        first.state["events"] = [{"type": "key_down", "key": {"value": "a"}}]
        first.state["profile"]["bp1"]["first_slot"] = (10, 20)
        second.state["events"] = [{"type": "move", "x": 2, "y": 3}]
        second.state["profile"]["bp1"]["first_slot"] = (30, 40)
        second.state["background_target_var"].set("Target window")

        snapshot = recorder._build_restart_state()

        self.assertEqual(snapshot["active_session_id"], "session-2")
        self.assertEqual(snapshot["global_focus_interval"], "12")
        self.assertEqual(len(snapshot["sessions"]), 2)
        self.assertEqual(snapshot["sessions"][0]["events"][0]["type"], "key_down")
        self.assertEqual(snapshot["sessions"][0]["profile"]["bp1"]["first_slot"], (10, 20))
        self.assertEqual(snapshot["sessions"][1]["settings"]["target"], [123, 456])

    def test_restart_restore_preserves_sessions_recordings_profiles_and_windows(self):
        class FakeVariable:
            def __init__(self, value=None):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession("session-1", "Session 1")
        }
        recorder._active_session_id = "session-1"
        recorder._next_session_number = 2
        recorder._session_context = threading.local()
        recorder._default_profile = {"bp1": {"first_slot": None}}
        recorder.background_targets = {"Game — Server 2 (PID 222, window 333)": (222, 333)}
        recorder.global_focus_interval_var = FakeVariable("15")
        recorder.session_selector = Mock()
        recorder._switch_session = Mock(
            side_effect=lambda session_id, **_kwargs: setattr(
                recorder, "_active_session_id", session_id
            )
        )
        recorder._update_profile_status = Mock()
        recorder._refresh_events = Mock()
        snapshot = {
            "version": 1,
            "active_session_id": "session-2",
            "global_focus_interval": "15",
            "sessions": [
                {
                    "session_id": "session-1",
                    "name": "Server 1",
                    "events": [{"type": "key_down", "key": {"value": "a"}}],
                    "profile": {"bp1": {"first_slot": [10, 20]}},
                    "backpack_names": ["bp1"],
                    "automation_backpack": 0,
                    "automation_slot": 9,
                    "settings": {"threshold": "72", "target": None},
                },
                {
                    "session_id": "session-2",
                    "name": "Server 2",
                    "events": [{"type": "move", "x": 40, "y": 50}],
                    "profile": {"bp1": {"first_slot": [30, 40]}},
                    "backpack_names": ["bp1"],
                    "automation_backpack": 0,
                    "automation_slot": 12,
                    "settings": {
                        "threshold": "88",
                        "target": [222, 333],
                        "timed_runes": True,
                        "rune_interval": "90",
                    },
                },
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as state_file:
            json.dump(snapshot, state_file)
            state_path = state_file.name

        try:
            with patch.object(macro_recorder.tk, "StringVar", side_effect=FakeVariable), patch.object(
                macro_recorder.tk, "BooleanVar", side_effect=FakeVariable
            ):
                recorder.restore_restart_state(state_path)
        finally:
            self.assertFalse(macro_recorder.Path(state_path).exists())

        self.assertEqual(recorder.active_session_id, "session-2")
        self.assertEqual(recorder.global_focus_interval_var.get(), "15")
        self.assertEqual(recorder._sessions["session-1"].name, "Server 1")
        self.assertEqual(recorder._sessions["session-1"].state["events"][0]["type"], "key_down")
        self.assertEqual(recorder._sessions["session-1"].state["automation_slot"], 9)
        self.assertEqual(recorder._sessions["session-2"].state["profile"]["bp1"]["first_slot"], [30, 40])
        self.assertEqual(recorder._sessions["session-2"].state["automation_slot"], 12)
        self.assertEqual(recorder._sessions["session-2"].state["threshold_var"].get(), "88")
        self.assertTrue(recorder._sessions["session-2"].state["timed_runes_var"].get())
        self.assertEqual(recorder._sessions["session-2"].state["rune_interval_var"].get(), "90")
        self.assertEqual(
            recorder._sessions["session-2"].state["background_target_var"].get(),
            "Game — Server 2 (PID 222, window 333)",
        )

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
        recorder._active_session_id = "session-1"
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession(
                "session-1",
                "Session 1",
                {"events": recorder.events},
            )
        }
        recorder._session_context = threading.local()
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
        recorder._sessions = {
            "session-1": macro_recorder.MacroSession("session-1", "Session 1")
        }
        recorder._active_session_id = "session-1"
        recorder._session_context = threading.local()
        recorder._coordinator_stop = threading.Event()
        recorder._coordinator_thread = None
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
        recorder.global_focus_interval_var = SimpleNamespace(get=lambda: "10")
        recorder.recording_loop_interval_var = SimpleNamespace(get=lambda: "0")
        recorder.timed_runes_var = SimpleNamespace(get=lambda: False)
        recorder.automation_slot = 7
        recorder.automation_backpack = 1
        recorder.automation_stop = threading.Event()
        recorder.background_stop = threading.Event()
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
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", None
        ):
            with patch.object(macro_recorder.subprocess, "run") as run:
                recorder._focus_target_window(123, 456)

        script = run.call_args.args[0][2]
        self.assertIn("unix id is 123", script)
        self.assertIn('name is "Game \\"One\\""', script)
        self.assertIn('perform action "AXRaise"', script)
        self.assertIn('set value of attribute "AXMain" of targetWindow to true', script)
        self.assertIn("set frontmost of targetProcess to true", script)

    def test_focus_failure_suggests_refreshing_and_selecting_game_window(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {"pid": 123, "window_id": 456, "name": "Game"}
            ],
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", None
        ):
            with patch.object(
                macro_recorder.subprocess,
                "run",
                side_effect=macro_recorder.subprocess.CalledProcessError(
                    1, ["osascript"], stderr="Target process did not become frontmost"
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Confirm the selected window is the game.*Accessibility permission",
                ):
                    recorder._focus_target_window(123, 456)

    def test_focus_uses_native_activation_and_verifies_frontmost_process(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {"pid": 123, "window_id": 456, "name": "Game"}
            ],
        )
        running_app = SimpleNamespace(
            activateWithOptions_=Mock(return_value=True)
        )
        frontmost_app = SimpleNamespace(processIdentifier=lambda: 123)
        fake_appkit = SimpleNamespace(
            NSRunningApplication=SimpleNamespace(
                runningApplicationWithProcessIdentifier_=Mock(return_value=running_app)
            ),
            NSApplicationActivateIgnoringOtherApps=1,
            NSWorkspace=SimpleNamespace(
                sharedWorkspace=lambda: SimpleNamespace(
                    frontmostApplication=lambda: frontmost_app
                )
            ),
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", fake_appkit
        ):
            with patch.object(macro_recorder.subprocess, "run") as run:
                recorder._focus_target_window(123, 456)

        running_app.activateWithOptions_.assert_called_once_with(1)
        fake_appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.assert_called_once_with(123)
        run.assert_called_once()

    def test_focus_fails_if_native_activation_does_not_front_target_process(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {"pid": 123, "window_id": 456, "name": "Game"}
            ],
        )
        running_app = SimpleNamespace(activateWithOptions_=Mock(return_value=False))
        fake_appkit = SimpleNamespace(
            NSRunningApplication=SimpleNamespace(
                runningApplicationWithProcessIdentifier_=Mock(return_value=running_app)
            ),
            NSApplicationActivateIgnoringOtherApps=1,
            NSWorkspace=SimpleNamespace(
                sharedWorkspace=lambda: SimpleNamespace(
                    frontmostApplication=lambda: SimpleNamespace(processIdentifier=lambda: 999)
                )
            ),
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", fake_appkit
        ), patch.object(macro_recorder.subprocess, "run"):
            with patch.object(macro_recorder.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "macOS did not activate window 456"):
                    recorder._focus_target_window(123, 456)

    def test_focus_target_with_empty_title_does_not_query_for_empty_name(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {"pid": 123, "window_id": 456, "name": ""}
            ],
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", None
        ):
            with patch.object(macro_recorder.subprocess, "run") as run:
                recorder._focus_target_window(123, 456)

        script = run.call_args.args[0][2]
        self.assertNotIn('whose name is ""', script)
        self.assertIn("count of windows of targetProcess", script)
        self.assertIn("set frontmost of targetProcess to true", script)

    def test_refresh_background_targets_omits_windows_without_titles(self):
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowNumber="window_id",
            kCGWindowLayer="layer",
            kCGWindowBounds="bounds",
            kCGWindowOwnerName="owner",
            kCGWindowName="name",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {
                    "pid": 123,
                    "window_id": 456,
                    "layer": 0,
                    "bounds": {"Width": 100, "Height": 100},
                    "owner": "CursorUIViewService",
                    "name": "Popover",
                },
                {
                    "pid": 124,
                    "window_id": 457,
                    "layer": 0,
                    "bounds": {"Width": 100, "Height": 100},
                    "owner": "Game",
                    "name": "Server",
                },
            ],
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.background_target_var = SimpleNamespace(get=lambda: "Foreground input", set=Mock())
        recorder.background_target_menu = SimpleNamespace(configure=Mock())
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder.os, "getpid", return_value=99
        ):
            recorder._refresh_background_targets()

        self.assertEqual(
            recorder.background_targets,
            {"Game — Server (PID 124, window 457)": (124, 457)},
        )

    def test_focus_is_reused_until_configured_interval_expires(self):
        with tempfile.TemporaryFile(mode="w+") as focus_state:
            recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
            recorder.automation_target_pid = 123
            recorder.automation_target_window_id = 456
            recorder._global_focus_interval = 10
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

    def test_focused_input_reserves_configured_session_turn(self):
        with tempfile.TemporaryFile(mode="w+") as focus_state:
            recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
            recorder.automation_target_pid = 123
            recorder.automation_target_window_id = 456
            recorder._global_focus_interval = 5
            recorder.last_focus_time = 0
            recorder.automation_stop = threading.Event()
            recorder.focus_input_mutex = threading.RLock()
            recorder.focus_input_owner = None
            recorder.focus_lock_held = False
            recorder.focus_lock_file = focus_state
            recorder._focus_target_window = Mock()

            recorder._begin_focused_input()
            recorder._release_focused_input()

            recorder._focus_target_window.assert_called_once_with(123, 456)
            focus_state.seek(0)
            state = json.loads(focus_state.read())
            self.assertEqual(state["target"], [123, 456])
            self.assertGreaterEqual(state["lease_until"] - state["focused_at"], 5)

    def test_background_mana_sample_maps_screen_point_into_window_image(self):
        image = SimpleNamespace(
            width=100,
            height=80,
            getpixel=Mock(return_value=(10, 20, 30)),
        )
        fake_quartz = SimpleNamespace(
            kCGWindowListOptionAll=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowNumber="window_id",
            kCGWindowOwnerPID="pid",
            kCGWindowBounds="bounds",
            CGWindowListCopyWindowInfo=lambda *_args: [
                {
                    "window_id": 456,
                    "pid": 123,
                    "bounds": {"X": 100, "Y": 200, "Width": 50, "Height": 40},
                }
            ],
        )
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.automation_target_pid = 123
        recorder.automation_target_window_id = 456
        recorder._target_window_image = Mock(
            return_value=(image, {"X": 100, "Y": 200, "Width": 50, "Height": 40})
        )
        with patch.object(macro_recorder, "Quartz", fake_quartz), patch.object(
            macro_recorder, "AppKit", None
        ):
            color = recorder._sample_pixel((110, 210))
        self.assertEqual(color, (10, 20, 30))
        image.getpixel.assert_called_once_with((20, 20))

    def test_mana_color_matching_uses_configured_per_channel_tolerance(self):
        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.profile = {
            "mana_point": (10, 20),
            "mana_color": [100, 150, 200],
            "mana_color_tolerance": 10,
        }
        recorder._sample_pixel = Mock(return_value=(110, 142, 205))

        self.assertEqual(recorder._read_mana_percent(), 100.0)
        recorder._sample_pixel.return_value = (111, 150, 200)
        self.assertEqual(recorder._read_mana_percent(), 0.0)


if __name__ == "__main__":
    unittest.main()
