import sys
import types
import unittest
from types import SimpleNamespace

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

        pixels = [
            (0, 0, 0), (0, 0, 0), (0, 0, 0),
            (0, 0, 0), (20, 30, 40), (0, 0, 0),
            (0, 0, 0), (0, 0, 0), (0, 0, 0),
        ]

        def fake_grab(bbox=None):
            return FakeGrab(pixels)

        macro_recorder.ImageGrab = fake_grab
        macro_recorder.mouse = SimpleNamespace(Controller=lambda: None)

        recorder = macro_recorder.MacroRecorder.__new__(macro_recorder.MacroRecorder)
        recorder.profile = {"mana_point": (10, 10), "mana_color": None}
        recorder.mouse_controller = SimpleNamespace(position=(10, 10))
        recorder.status = SimpleNamespace(set=lambda *args, **kwargs: None)
        recorder._update_profile_status = lambda: None

        recorder._capture_mana_color()

        self.assertEqual(tuple(recorder.profile["mana_color"]), (20, 30, 40))
        self.assertEqual(recorder._read_mana_percent(), 100.0)

        macro_recorder.ImageGrab = original_grab
        macro_recorder.mouse = original_mouse


if __name__ == "__main__":
    unittest.main()
