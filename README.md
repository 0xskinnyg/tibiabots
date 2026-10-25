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
- `F11`: immediately stop recording, replay, and all session automation/recording loops

## Multiple macro sessions

Use **New session** to create a separate macro workspace for each game server. Each session has its own recording, profile, calibration, rune cursor, automation mode, and rune/recording timing settings. The **Global refocus interval** at the top is shared by every session. Select the intended **Active macro session** before using a hotkey; F1/F2/F6/F7 calibrations and F8-F11 controls apply to that session. Switching sessions stops an in-progress recording so the next mouse/key actions are not appended to the previous server's macro.

Use **Start all automation** beside the session selector to validate and start automation for every session using that session's own settings. If any session is not ready, none of the sessions are newly started. Automation visits sessions in order, starting with Session 1: it focuses a session, checks for a rune while playing one complete pass of that session's loaded recording, then moves to the next session. After the last session, it repeats the cycle. A session's **Pause between loops** delay only postpones that session's next recording pass; other sessions still get their turns. Rune checks use that session's mana-color calibration or timer interval. **Stop all automation** stops the coordinated worker for every session. Because one worker handles the sessions in sequence, mouse and keyboard actions cannot overlap or switch windows in the middle of another session's recording pass. Recording-loop errors stop only that session's recording pass and identify the event index; its rune checks and other sessions continue. Rune errors stop rune making for that session while its recording continues. The sessions share the desktop mouse and keyboard, so focusing a game window still changes the one globally focused macOS window.

Use **Restart to load code** after changing the program files. Restarting starts the current script again and restores all macro sessions, including unsaved recordings, profile/calibration edits, current rune positions, settings, and selected game windows that are still available. Automation itself is not restarted automatically; press **Start all automation** after restart when ready.

macOS may require enabling both **Input Monitoring** and **Accessibility** in **System Settings → Privacy & Security**. Add the exact Python executable running the app, not only Terminal. Find it with:

```bash
python -c "import sys; print(sys.executable)"
```

Add that path to both permission panels, then fully quit and reopen the recorder. Screen Recording permission may also be requested by macOS for some applications.

Playback also requires Accessibility permission because macOS blocks programs from controlling the mouse and keyboard until that permission is granted.

By default, the rune assistant uses Pillow to sample one mana-bar pixel. Place the cursor over the pixel that represents your mana threshold and press `F1` to set the mana check point. To capture the filled color, bring the selected game window to the front, place the cursor over a filled mana pixel, and press `F2`. When that game window is frontmost, the app samples the screen pixel directly; if the selected game is not frontmost (as when clicking the recorder's **Capture mana color** button), capture samples the previously calibrated mana point instead. Mana color matching uses the profile's `mana_color_tolerance` value per RGB channel. When the check-point pixel matches the captured color within tolerance, the assistant moves the next blank rune to the left hand and presses `F5`. It advances through every calibrated backpack, slots 2–20, then wraps back to the first backpack without skipping or revisiting a slot until mana drops below the threshold. Use **Add backpack** to add BP3, BP4, and so on.

If the mana pixel is unreliable, enable **Use timer instead of mana pixel** and enter a **Rune interval (sec)**. The assistant waits for that interval before the first rune, then waits the same amount after each rune before making the next one. Timer mode does not require mana point/color calibration. Choose an interval appropriate for your character's mana regeneration and rune spell; if the timing is too short, the character may try to cast before enough mana is available.

## Selecting and focusing a game window

On macOS, select each session's game window in **Window to focus** and use **Refresh windows** to update the list. Untitled system/helper windows and macOS `UIViewService` popovers are omitted because macOS cannot reliably raise them as game windows. Set the **Global refocus interval** at the top to control how often the coordinator brings the current session's selected window to the front. It is shared across sessions, not stored in individual settings. The app raises the selected window, waits briefly for macOS to complete activation, then sends normal foreground mouse and keyboard input. Calibrate coordinates while the target window is in its normal on-screen position; recorded and calibrated coordinates are absolute screen coordinates. The focus interval is checked throughout recorded event delays and between actions.

The coordinator focuses and runs one session's recording pass at a time, in session order. Since macOS can focus only one window at a time, game windows take turns being in front; this does not allow playing in another foreground window while automation is running.

macOS may request **Screen Recording** permission to read the selected window. Window activation uses macOS's native application activation API and System Events/Accessibility to raise the selected window; grant **Accessibility** to the Python executable running the recorder (and System Events/osascript if prompted). **Input Monitoring** and **Accessibility** permissions for Python may also be required for global hotkeys and input. Some system/helper windows cannot be activated as normal game windows; select the main game window from the refreshed list.

When **Loop loaded fishing/eating recording while waiting** is enabled, **Pause between loops (sec)** controls the delay after each full pass through that recording. Set it to `0` to loop again immediately. The countdown pauses while rune automation is handling a cast.

The window uses vertical and horizontal scrollbars when its contents do not fit. Use the mouse wheel to scroll vertically and Shift + mouse wheel to scroll horizontally.

This tool replays absolute mouse coordinates and keyboard events. Test macros carefully before using them with destructive actions.
