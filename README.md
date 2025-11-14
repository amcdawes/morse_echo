# morse-echo — Morse Code Trainer

![Screenshot](screenshot.png)

A small NiceGUI-based training app that plays single characters in Morse code and measures your reaction time. Built for quick practice sessions to improve Morse recognition speed.

Features
- Play a random character in Morse code and await a single-key response
- Tracks reaction time for correct answers and marks incorrect attempts
- Configurable session length and WPM (words per minute) speed
- Auto-replay after 1.5 seconds if no response
- Session-based training with Start/Stop controls
- SQLite database for persistent session history
- History tab with:
  - Recent sessions table showing date, WPM, duration, accuracy, and average response time
  - Character statistics grid with color-coded accuracy (green ≥90%, yellow ≥75%, red <75%)
  - Export to CSV functionality
  - Clear history option with confirmation dialog
- Response time sparkline chart during active sessions
- Bell sound at the end of a session
- Native app mode (optional, via `--native` flag)

Requirements
- Python 3.12+ (project uses 3.13 in the workspace)
- The project already depends on `nicegui` (see `pyproject.toml`). The app also uses `numpy` and `sounddevice` for audio.

If you use `uv` for package management (as in this project), add the runtime packages with:

```zsh
uv sync
```

Or with pip:

```zsh
python -m pip install nicegui numpy sounddevice pywebview
```

Note: `pywebview` is only needed if you want to run in native app mode.

How to run

**Browser mode (default):**
```zsh
uv run python main.py
```
Then open your browser to http://localhost:8080

**Native app mode:**
```zsh
uv run python main.py --native
```
Opens as a native window using pywebview (no browser required).

On AlmaLinux, you may need to set an environment variable:
```zsh
WEBKIT_DISABLE_COMPOSITING_MODE=1 uv run python main.py --native
```

**Debug mode:**
```zsh
uv run python main.py --debug
```
Prints additional logs to the console. Can be combined with `--native`.

Quick usage

**Starting a session:**
1. Adjust the "Session Length" (seconds) and "WPM" (words per minute) settings
2. Click "Start" to begin - there's a 1-second buffer before the first character plays
3. Listen to the Morse code character and type the letter you heard
4. If correct, your reaction time is recorded; if incorrect, the error is logged and you can try again
5. If you don't respond within 1.5 seconds, the character automatically replays
6. Click "Stop" to end the session early (a bell sound plays when finished)

**Viewing history:**
1. Switch to the "History" tab to see:
   - Recent sessions with accuracy, average response time, and other stats
   - Character-by-character statistics showing which letters need more practice
2. Click "Export CSV" to download your session data for external analysis
3. Click "Clear History" to reset all data (requires confirmation)

Notes and customization
- Audio is generated in-code using `numpy` and played with `sounddevice`. If your system has no audio devices or `sounddevice` fails, the app will still run but you won't hear prompts.
- The Morse timing use the PARIS formula according to WPM slider.
- The UI uses NiceGUI; it's straightforward to extend the interface (add charts, summaries, or export results).

Troubleshooting
- If the server fails to start, check the terminal logs. NiceGUI prints the address (e.g., http://localhost:8080).
- Run with `--debug` flag to print additional logs to the console.
- If audio playback fails: ensure your system has an audio output device and that `sounddevice` was installed successfully.
- If native mode doesn't work, ensure `pywebview` is installed and you have the required system dependencies (WebKit on Linux, WebView2 on Windows).
- Session history is stored in `~/.morse_echo.db` - if you encounter database errors, you can delete this file to start fresh.

License
- MIT

Authors
- N7LFO (Andy)
