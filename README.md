# macOS Macro Recorder

A simple Python mouse and keyboard macro recorder. It captures actions globally, so the macro can be recorded in another app and replayed there.

It also includes a calibration-based rune assistant. Record only your fishing/eating actions, save that recording as JSON, then calibrate the backpack and hand coordinates in the app. The assistant pauses that background recording when mana reaches the configured threshold, drags the next blank rune into the left hand, and presses `F5`.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python macro_recorder.py
```

If `import tkinter` fails with Homebrew Python 3.13, install the matching Tk package first:

```bash
brew install python-tk@3.13
```

Then run the program again. Alternatively, use the official Python installer from python.org.

Use the global hotkeys:

- `F8`: start/stop recording
- `F9`: continuously replay the current recording
- `F10`: clear the recording
- `F11`: stop recording or replay, or click the **Stop** button

macOS may require enabling both **Input Monitoring** and **Accessibility** in **System Settings → Privacy & Security**. Add the exact Python executable running the app, not only Terminal. Find it with:

```bash
python -c "import sys; print(sys.executable)"
```

Add that path to both permission panels, then fully quit and reopen the recorder. Screen Recording permission may also be requested by macOS for some applications.

Playback also requires Accessibility permission because macOS blocks programs from controlling the mouse and keyboard until that permission is granted.

The rune assistant uses Pillow to sample one mana-bar pixel. Place the cursor over the pixel that represents your mana threshold and click `Set mana check point`. Then place the cursor over a filled mana pixel and click `Capture mana color`. When the check-point pixel matches that captured color, the assistant moves the next blank rune to the left hand and presses `F5`. It advances through every calibrated backpack, slots 2–20, then wraps back to the first backpack without skipping or revisiting a slot until mana drops below the threshold. Use **Add backpack** to add BP3, BP4, and so on.

This tool replays absolute mouse coordinates and keyboard events. Test macros carefully before using them with destructive actions.
