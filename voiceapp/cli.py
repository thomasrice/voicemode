from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

try:
    from colorama import Fore, Style, init as colorama_init
except Exception:  # colorama not installed yet; degrade gracefully for --help
    class _Dummy:
        def __getattr__(self, _):
            return ""

    Fore = _Dummy()  # type: ignore
    Style = _Dummy()  # type: ignore

    def colorama_init():  # type: ignore
        return None


def _hint_install(missing: str):
    print(
        Fore.LIGHTRED_EX
        + f"Missing dependency '{missing}'.\nInstall dependencies with: python -m pip install -r requirements.txt"
        + Style.RESET_ALL
    )


def _list_devices() -> int:
    try:
        import sounddevice as sd
    except ModuleNotFoundError as e:
        _hint_install(e.name or "sounddevice")
        return 1

    try:
        devices = sd.query_devices()
    except Exception as e:
        print(Fore.LIGHTRED_EX + f"Could not query devices: {e}" + Style.RESET_ALL)
        return 1

    print("Index  Name                              InCh  OutCh  Default")
    for i, d in enumerate(devices):
        is_default = "*" if i == sd.default.device[0] else ""
        print(f"{i:>5}  {d['name'][:30]:<30}  {d['max_input_channels']:>4}  {d['max_output_channels']:>5}   {is_default}")
    return 0


def _paste_text(text: str):
    # Set clipboard
    try:
        import pyperclip
    except ModuleNotFoundError as e:
        _hint_install(e.name or "pyperclip")
        return

    pyperclip.copy(text)

    # Try keyboard library first (works well on Windows)
    try:
        import keyboard  # type: ignore

        from .utils import paste_keystroke

        mod, key = paste_keystroke()
        keyboard.send(f"{mod}+{key}")
        return
    except Exception:
        pass

    # Fallback to pynput (works on macOS with Accessibility permission)
    try:
        from pynput.keyboard import Controller, Key
        from .utils import paste_keystroke

        k = Controller()
        if paste_keystroke()[0] == "command":
            with k.pressed(Key.cmd):
                k.press("v")
                k.release("v")
        else:
            with k.pressed(Key.ctrl):
                k.press("v")
                k.release("v")
    except Exception as e:
        print(Fore.LIGHTRED_EX + f"Unable to send paste keystroke: {e}" + Style.RESET_ALL)


def run(hotkey: str, model: str, rate: int, device: Optional[int], no_sound: bool, push_to_talk: bool):
    colorama_init()  # colour support on Windows terminals
    mode_desc = "Push-to-talk" if push_to_talk else "Toggle"
    print(Fore.LIGHTBLUE_EX + f"VoiceMode ready ({mode_desc}). Hotkey {hotkey}. Ctrl+C to quit." + Style.RESET_ALL)

    # Import heavy modules lazily so --help works without installs
    try:
        from .audio import AudioConfig, AudioRecorder
        from .transcribe import OpenAITranscriber
        from .sounds import play_start, play_stop
        from .utils import float_to_wav_bytes
        from .settings import resolve_openai_key
    except ModuleNotFoundError as e:
        _hint_install(e.name or "a required package")
        return

    # Ensure an API key is available (env or config)
    key = resolve_openai_key()
    if not key:
        print(Fore.LIGHTRED_EX + "No OPENAI_API_KEY found. Set env var or run:\n  voicemode config --set-openai-key sk-...\n" + Style.RESET_ALL)
        return
    # If env var isn't set but config has a key, set it for this process
    import os as _os
    if not _os.environ.get("OPENAI_API_KEY"):
        _os.environ["OPENAI_API_KEY"] = key

    cfg = AudioConfig(sample_rate=rate, device=device)
    recorder = AudioRecorder(cfg)
    recorder.start()

    transcriber = OpenAITranscriber(model=model)
    listening = {"active": False}

    def _start():
        if listening["active"]:
            return
        listening["active"] = True
        play_start(no_sound)
        recorder.begin_session()
        if not push_to_talk:
            print(Fore.LIGHTBLUE_EX + f"Listening… press {hotkey} again to stop." + Style.RESET_ALL)

    def _stop_and_transcribe():
        if not listening["active"]:
            return
        listening["active"] = False
        play_stop(no_sound)
        print("Transcribing…")
        frames = recorder.end_session()
        wav_bytes = float_to_wav_bytes(frames, rate)
        if not wav_bytes:
            print(Fore.LIGHTRED_EX + "No audio captured." + Style.RESET_ALL)
            return
        try:
            text = transcriber.transcribe_wav_bytes(wav_bytes)
            if text:
                _paste_text(text)
                print(Fore.LIGHTBLUE_EX + f"Typed: {text}" + Style.RESET_ALL)
            else:
                print(Fore.LIGHTRED_EX + "(Empty transcription)" + Style.RESET_ALL)
        except Exception as e:
            print(Fore.LIGHTRED_EX + f"Transcription error: {e}" + Style.RESET_ALL)

    def toggle():
        if listening["active"]:
            _stop_and_transcribe()
        else:
            _start()

    # Hotkey registration
    listener = None
    keyboard_success = False
    
    # Try keyboard library first (works best on Windows/Linux)
    try:
        import keyboard  # type: ignore
        
        # Test if we have permissions before setting up hotkeys
        try:
            keyboard.is_pressed("esc")  # Test call to check permissions
        except OSError as e:
            if "Must be run as administrator" in str(e) or "Error 13" in str(e):
                raise PermissionError("Keyboard library requires admin privileges")
        
        if push_to_talk:
            key = hotkey.lower()
            keyboard.on_press_key(key, lambda e: _start())
            keyboard.on_release_key(key, lambda e: _stop_and_transcribe())
        else:
            keyboard.add_hotkey(hotkey, toggle)
        keyboard_success = True
    except (ImportError, PermissionError, Exception):
        pass
    
    # If keyboard library didn't work, try pynput as a cross-platform fallback
    if not keyboard_success:
        try:
            from pynput import keyboard as pk
            import platform

            def on_press(key):
                try:
                    if key == pk.Key.f8 and hotkey.upper() == "F8":
                        if push_to_talk:
                            _start()
                        else:
                            toggle()
                except Exception:
                    pass

            def on_release(key):
                try:
                    if key == pk.Key.f8 and hotkey.upper() == "F8":
                        if push_to_talk:
                            _stop_and_transcribe()
                except Exception:
                    pass

            listener = pk.Listener(on_press=on_press, on_release=on_release if push_to_talk else None)
            listener.daemon = True
            listener.start()
            
            # Show platform-specific message
            if platform.system() == "Darwin":  # macOS
                print(
                    Fore.LIGHTYELLOW_EX
                    + "\n⚠️  macOS detected: VoiceMode needs Accessibility permissions to detect hotkeys.\n"
                    + "   Please grant permission in:\n"
                    + "   System Settings → Privacy & Security → Accessibility → Add your Terminal app\n"
                    + Style.RESET_ALL
                )
            else:
                print(
                    Fore.LIGHTYELLOW_EX
                    + "Using pynput for hotkeys."
                    + Style.RESET_ALL
                )
        except Exception as e:
            print(Fore.LIGHTRED_EX + f"Failed to register hotkey: {e}" + Style.RESET_ALL)
            print("Fallback: press Enter to toggle; type 'quit' to exit.")
            while True:
                s = input()
                if s.strip().lower() in {"quit", "exit"}:
                    return
                toggle()
            return

    try:
        # Keep the main thread alive
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if keyboard_success:
            try:
                import keyboard  # type: ignore
                keyboard.remove_all_hotkeys()
            except Exception:
                pass
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None):
    # Load environment variables from a local .env if present
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="voicemode", description="VoiceMode – voice dictation with OpenAI STT")
    parser.add_argument("--hotkey", default="F8", help="Global hotkey to toggle listening (default: F8)")
    parser.add_argument("--model", default="gpt-4o-mini-transcribe", help="OpenAI model to use")
    parser.add_argument("--rate", type=int, default=16000, help="Sample rate (Hz), default 16000")
    parser.add_argument("--device", type=int, default=None, help="Input device index (see --list-devices)")
    parser.add_argument("--no-sound", action="store_true", help="Disable start/stop sounds")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--push-to-talk", action="store_true", help="Hold hotkey to record; release to transcribe")

    sub = parser.add_subparsers(dest="cmd")
    cfg = sub.add_parser("config", help="Configure settings")
    cfg.add_argument("--set-openai-key", dest="set_openai_key", help="Set and save your OpenAI API key")

    args = parser.parse_args(argv)

    if args.list_devices:
        return _list_devices()

    if args.cmd == "config":
        if args.set_openai_key:
            from .settings import load_settings, save_settings

            data = load_settings()
            data["OPENAI_API_KEY"] = args.set_openai_key
            path = save_settings(data)
            print(Fore.LIGHTBLUE_EX + f"Saved OPENAI_API_KEY to {path}" + Style.RESET_ALL)
            return 0
        parser.parse_args(["config", "--help"])  # show help
        return 0

    run(args.hotkey, args.model, args.rate, args.device, args.no_sound, args.push_to_talk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
