# VoiceMode (Cross‑platform CLI)

Simple, robust voice dictation that works on Windows and macOS with only Python installed.

- Author: Thomas Rice <thomas@thomasrice.com>
- Website: https://www.thomasrice.com/
- Thomas Rice is co‑founder of Minotaur Capital — https://www.minotaurcapital.com/

Highlights:
- Global hotkey to start/stop recording (default: F8)
- Uses OpenAI speech‑to‑text (`gpt-4o-mini-transcribe` by default)
- Pastes the result into the active app automatically
- Cross‑platform start/stop sounds (uses packaged WAVs if present, else generated beeps)
- Resilient audio stream with auto‑restart on errors

## Quick Start

Prerequisites:
- Python 3.11+
- An OpenAI API key in the environment: `OPENAI_API_KEY=...`
  - Optionally create a `.env` file next to this README with `OPENAI_API_KEY=...` (auto-loaded)

Install dependencies (recommended):

```bash
python -m pip install -r requirements.txt
```

Run directly without installing the package:

```bash
python -m voiceapp --help
python -m voiceapp  # Interactive mode (default)
```

Or install as an editable package (recommended):

```bash
python -m pip install -e .
voicemode  # CLI entry point
```

Notes for macOS:
- Grant your terminal app “Accessibility” permission (System Settings → Privacy & Security → Accessibility) so global hotkeys and paste work.
- On macOS, paste uses `Command+V`; on Windows, `Ctrl+V`.
- If `keyboard` is unavailable, the app falls back to `pynput` automatically.

Helper launchers in this folder:
- `./voicemode` or `./voicemode.sh` (macOS/Linux)
- `voicemode.bat` (Windows)
- `voicemode.command` (macOS double‑click)

All launchers run `python -m voiceapp` from this folder.

## Options

```text
--hotkey       Global hotkey (default: F8)
--model        OpenAI model (default: gpt-4o-mini-transcribe)
--rate         Sample rate in Hz (default: 16000)
--device       Optional input device index (see --list-devices)
--no-sound     Disable start/stop sounds
--list-devices List audio input devices and exit
--push-to-talk Hold hotkey to record; release to transcribe
```

### Background server (Linux/macOS)

Optionally, you can run a small background server that responds to toggle commands. This is useful if you want to bind your desktop hotkey to `voicemode toggle` without keeping a foreground terminal open.

- Start server: `voicemode serve`
- Toggle listening: `voicemode toggle`
- Check status: `voicemode status`

Notes:
- The server uses a Unix domain socket under the app config directory and is supported on Linux and macOS.
- On Windows, use the default interactive mode instead.

## Sounds

If you’d like custom sounds, place WAV files at:
- `voiceapp/assets/start.wav`
- `voiceapp/assets/stop.wav`

They’ll be packaged when installed. If missing, the app generates short beeps.

## Setting your OpenAI key

VoiceMode looks for your key in this order:

- Environment variable `OPENAI_API_KEY` (preferred)
- `.env` file in this folder (auto‑loaded)
- Per‑user config file saved by the command below
- Simple text files named `openai.txt`, `openai.key`, or `OPENAI_API_KEY.txt` in this folder or in the app config folder

To save your key using the CLI:

```bash
voicemode config --set-openai-key sk-...
```

This writes to a per‑user config file:
- Windows: `%APPDATA%\VoiceApp\config.json`
- macOS: `~/Library/Application Support/VoiceApp/config.json`
- Linux: `~/.config/voiceapp/config.json`

Alternatively, create a `.env` file in this folder containing:

```
OPENAI_API_KEY=sk-...
```
