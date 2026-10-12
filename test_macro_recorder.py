import sys
import types
import unittest

pynput = types.ModuleType("pynput")
keyboard_mod = types.ModuleType("pynput.keyboard")
mouse_mod = types.ModuleType("pynput.mouse")

class DummyKey:
    name = "f1"

class DummyKeyCode:
    def __init__(self, char=None):
        self.char = char

class DummyButton:
    left = "left"
    right = "right"
    middle = "middle"

keyboard_mod.Key = DummyKey
keyboard_mod.KeyCode = DummyKeyCode
keyboard_mod.Controller = object
mouse_mod.Button = DummyButton
mouse_mod.Controller = object
mouse_mod.Listener = object
keyboard_mod.Listener = object
pynput.keyboard = keyboard_mod
pynput.mouse = mouse_mod
sys.modules["pynput"] = pynput
sys.modules["pynput.keyboard"] = keyboard_mod
sys.modules["pynput.mouse"] = mouse_mod

sys.modules.setdefault("PIL", types.ModuleType("PIL"))

from macro_recorder import MacroRecorder


class LogoutDetectionTests(unittest.TestCase):
    def test_color_match_uses_euclidean_distance(self):
        self.assertTrue(MacroRecorder._color_matches((10, 20, 30), (15, 25, 35), tolerance=20))
        self.assertFalse(MacroRecorder._color_matches((10, 20, 30), (40, 50, 60), tolerance=10))

    def test_color_missing_detects_mismatch(self):
        self.assertFalse(MacroRecorder._color_matches((10, 10, 10), (80, 80, 80), tolerance=15))

    def test_move_interpolation_slowed_down_across_large_distance(self):
        points = MacroRecorder._interpolate_move((0, 0), (100, 0), max_step=3)
        self.assertGreater(len(points), 1)
        self.assertEqual(points[-1], (100, 0))
        self.assertTrue(all(abs(points[i][0] - points[i - 1][0]) <= 3 for i in range(1, len(points))))


if __name__ == "__main__":
    unittest.main()
