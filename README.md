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

Use **Restart to load code** after changing the program files. Restarting starts the current script again and closes this instance; unsaved recordings and profile edits are discarded.

macOS may require enabling both **Input Monitoring** and **Accessibility** in **System Settings → Privacy & Security**. Add the exact Python executable running the app, not only Terminal. Find it with:

```bash
python -c "import sys; print(sys.executable)"
```

Add that path to both permission panels, then fully quit and reopen the recorder. Screen Recording permission may also be requested by macOS for some applications.

Playback also requires Accessibility permission because macOS blocks programs from controlling the mouse and keyboard until that permission is granted.

By default, the rune assistant uses Pillow to sample one mana-bar pixel. Place the cursor over the pixel that represents your mana threshold and click `Set mana check point`. Then place the cursor over a filled mana pixel and click `Capture mana color`. When the check-point pixel matches that captured color, the assistant moves the next blank rune to the left hand and presses `F5`. It advances through every calibrated backpack, slots 2–20, then wraps back to the first backpack without skipping or revisiting a slot until mana drops below the threshold. Use **Add backpack** to add BP3, BP4, and so on.

If the mana pixel is unreliable, enable **Use timer instead of mana pixel** and enter a **Rune interval (sec)**. The assistant waits for that interval before the first rune, then waits the same amount after each rune before making the next one. Timer mode does not require mana point/color calibration. Choose an interval appropriate for your character's mana regeneration and rune spell; if the timing is too short, the character may try to cast before enough mana is available.

## Selecting and focusing a game window

On macOS, select the game window in **Window to focus** and use **Refresh windows** to update the list. Set **Refocus every (sec)** to control how often the assistant brings that window to the front and makes it the main window while automation is running. The focus interval is checked throughout mana waits and timer waits, not only when it is time to make a rune. Calibrate the mana point/color and rune locations while that game window is visible. The assistant uses normal foreground mouse and keyboard input, so the selected window will receive those actions.

Multiple recorder instances coordinate input so they do not switch windows in the middle of a held click or key. Each instance focuses its selected window before sending actions and at its configured interval. Since macOS can focus only one window at a time, the game windows will take turns being in front; this does not allow playing in another foreground window while automation is running.

macOS may request **Screen Recording** permission to read the selected window, and **Accessibility** permission for `osascript`/System Events to focus windows. **Input Monitoring** and **Accessibility** permissions for Python may also be required for global hotkeys and input.

When **Loop loaded fishing/eating recording while waiting** is enabled, **Pause between loops (sec)** controls the delay after each full pass through that recording. Set it to `0` to loop again immediately. The countdown pauses while rune automation is handling a cast.

The window uses vertical and horizontal scrollbars when its contents do not fit. Use the mouse wheel to scroll vertically and Shift + mouse wheel to scroll horizontally.

This tool replays absolute mouse coordinates and keyboard events. Test macros carefully before using them with destructive actions.
