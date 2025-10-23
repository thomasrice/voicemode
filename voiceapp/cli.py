from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from colorama import Fore, Style
    from colorama import init as colorama_init
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
        print(
            f"{i:>5}  {d['name'][:30]:<30}  {d['max_input_channels']:>4}  {d['max_output_channels']:>5}   {is_default}"
        )
    return 0


def _detect_needs_shift_paste_linux() -> bool:
    """Heuristically detect if focused window expects Ctrl+Shift+V (terminal).

    - Wayland/Hyprland: uses `hyprctl activewindow -j` class
    - X11: uses `xdotool getwindowfocus getwindowclassname`
    """
    try:
        if sys.platform != "linux":
            return False
        term_classes = {
            "alacritty",
            "io.alacritty.alacritty",
            "kitty",
            "foot",
            "footclient",
            "wezterm",
            "org.wezfurlong.wezterm",
            "org.kde.konsole",
            "konsole",
            "gnome-terminal",
            "gnome-terminal-server",
            "kgx",
            "org.gnome.console",
            "tilix",
            "xfce4-terminal",
            "xterm",
            "urxvt",
            "rxvt",
            "st-256color",
            "st",
            "eterm",
            "terminator",
            "ghostty",
            "com.mitchellh.ghostty",
            "io.elementary.terminal",
        }
        env = os.environ
        override = env.get("VOICEAPP_SHIFT_PASTE", "").strip().lower()
        if override in {"1", "true", "yes"}:
            return True
        if override in {"0", "false", "no"}:
            return False

        def _match_terminal(candidate: str | None) -> bool:
            return bool(candidate and candidate.strip().lower() in term_classes)

        if env.get("WAYLAND_DISPLAY"):
            hyprctl = shutil.which("hyprctl")
            if hyprctl:
                try:
                    r = subprocess.run(
                        [hyprctl, "activewindow", "-j"],
                        capture_output=True,
                        text=True,
                        timeout=0.25,
                    )
                    if r.returncode == 0 and r.stdout:
                        try:
                            info = json.loads(r.stdout)
                        except Exception:
                            info = {}
                        cls = (
                            info.get("class")
                            or info.get("initialClass")
                            or info.get("app")
                            or ""
                        )
                        if _match_terminal(cls):
                            return True
                except Exception:
                    pass

            swaymsg = shutil.which("swaymsg")
            if swaymsg:
                try:
                    r = subprocess.run(
                        [swaymsg, "-t", "get_tree"],
                        capture_output=True,
                        text=True,
                        timeout=0.4,
                    )
                    if r.returncode == 0 and r.stdout:
                        try:
                            tree = json.loads(r.stdout)
                        except Exception:
                            tree = None

                        def _find_focused(node: dict) -> Optional[dict]:
                            if node.get("focused"):
                                return node
                            for child in node.get("nodes", []) + node.get(
                                "floating_nodes", []
                            ):
                                res = _find_focused(child)
                                if res:
                                    return res
                            return None

                        focused = (
                            _find_focused(tree) if isinstance(tree, dict) else None
                        )
                        if focused:
                            cls = focused.get("app_id")
                            if not cls:
                                props = focused.get("window_properties") or {}
                                cls = props.get("class") or props.get("instance")
                            if _match_terminal(cls):
                                return True
                except Exception:
                    pass

            gdbus = shutil.which("gdbus")
            if gdbus:
                script = "const win = global.display.focus_window; win ? win.get_wm_class() : '';"
                try:
                    r = subprocess.run(
                        [
                            gdbus,
                            "call",
                            "--session",
                            "--dest",
                            "org.gnome.Shell",
                            "--object-path",
                            "/org/gnome/Shell",
                            "--method",
                            "org.gnome.Shell.Eval",
                            script,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=0.25,
                    )
                    if r.returncode == 0 and r.stdout:
                        output = r.stdout.strip()
                        converted = re.sub(r"\btrue\b", "True", output)
                        converted = re.sub(r"\bfalse\b", "False", converted)
                        try:
                            parsed = ast.literal_eval(converted)
                        except Exception:
                            parsed = None
                        if isinstance(parsed, tuple) and len(parsed) >= 2:
                            cls = (parsed[1] or "").strip()
                            if _match_terminal(cls):
                                return True
                except Exception:
                    pass
            return False
        # X11
        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                r = subprocess.run(
                    [xdotool, "getwindowfocus", "getwindowclassname"],
                    capture_output=True,
                    text=True,
                    timeout=0.25,
                )
                if r.returncode == 0:
                    cls = (r.stdout or "").strip().lower()
                    if cls in term_classes:
                        return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _paste_text(text: str):
    """Copy text to clipboard and simulate paste.

    - On Wayland (Hyprland/Sway/etc): prefer wl-copy + wtype
    - On X11: prefer xclip + xdotool
    - Fallback to pyperclip + keyboard/pynput
    """
    linux_use_shift = False
    clipboard_set = False
    keystroke_sent = False
    linux_wayland = False
    hyprctl_cmd: Optional[str] = None

    # Linux first: Wayland/X11 specific tools
    try:
        import platform as _platform

        system = _platform.system()
        env = os.environ
        if system == "Linux":
            linux_use_shift = _detect_needs_shift_paste_linux()

            linux_wayland = bool(env.get("WAYLAND_DISPLAY"))
            if linux_wayland:
                wl_copy = shutil.which("wl-copy") if "shutil" in globals() else None
                if wl_copy is None:
                    import shutil as _shutil

                    wl_copy = _shutil.which("wl-copy")
                wtype = shutil.which("wtype") if "shutil" in globals() else None
                if wtype is None:
                    import shutil as _shutil

                    wtype = _shutil.which("wtype")
                hyprctl_cmd = shutil.which("hyprctl") if "shutil" in globals() else None
                if wl_copy:
                    try:
                        subprocess.run(
                            [wl_copy, "-n"], input=text.encode("utf-8"), check=True
                        )
                        clipboard_set = True
                    except Exception:
                        pass
                    else:
                        if wtype:
                            try:
                                if linux_use_shift:
                                    subprocess.run(
                                        [
                                            wtype,
                                            "-M",
                                            "ctrl",
                                            "-M",
                                            "shift",
                                            "v",
                                            "-m",
                                            "shift",
                                            "-m",
                                            "ctrl",
                                        ],
                                        check=False,
                                    )
                                else:
                                    subprocess.run(
                                        [wtype, "-M", "ctrl", "v", "-m", "ctrl"],
                                        check=False,
                                    )
                                keystroke_sent = True
                            except Exception:
                                pass

                if clipboard_set and not keystroke_sent and hyprctl_cmd:
                    try:
                        from .utils import paste_keystroke

                        mod, key = paste_keystroke()
                        mods = mod.upper()
                        if linux_use_shift and "SHIFT" not in mods:
                            mods = f"{mods} SHIFT" if mods else "SHIFT"
                        dispatch_arg = f"sendshortcut {mods},{key},"
                        subprocess.run(
                            [hyprctl_cmd, "dispatch", dispatch_arg], check=False
                        )
                        keystroke_sent = True
                    except Exception:
                        pass

            if not keystroke_sent:
                xclip = shutil.which("xclip") if "shutil" in globals() else None
                if xclip is None:
                    import shutil as _shutil

                    xclip = _shutil.which("xclip")
                xdotool = shutil.which("xdotool") if "shutil" in globals() else None
                if xdotool is None:
                    import shutil as _shutil

                    xdotool = _shutil.which("xdotool")
                if xclip and not clipboard_set:
                    try:
                        subprocess.run(
                            [xclip, "-selection", "clipboard"],
                            input=text.encode("utf-8"),
                            check=True,
                        )
                        clipboard_set = True
                    except Exception:
                        pass

                if clipboard_set and xdotool and not keystroke_sent:
                    try:
                        if linux_use_shift:
                            subprocess.run(
                                [xdotool, "key", "ctrl+shift+v"], check=False
                            )
                        else:
                            subprocess.run([xdotool, "key", "ctrl+v"], check=False)
                        keystroke_sent = True
                    except Exception:
                        pass

            if keystroke_sent:
                return
    except Exception:
        pass

    # Cross-platform fallback using pyperclip and Python key synth
    try:
        import pyperclip
    except ModuleNotFoundError as e:
        _hint_install(e.name or "pyperclip")
        return

    if not clipboard_set:
        try:
            pyperclip.copy(text)
            clipboard_set = True
        except Exception:
            pass

    if linux_wayland and clipboard_set and not keystroke_sent and hyprctl_cmd:
        try:
            from .utils import paste_keystroke

            mod, key = paste_keystroke()
            mods = mod.upper()
            if linux_use_shift and "SHIFT" not in mods:
                mods = f"{mods} SHIFT" if mods else "SHIFT"
            dispatch_arg = f"sendshortcut {mods},{key},"
            subprocess.run([hyprctl_cmd, "dispatch", dispatch_arg], check=False)
            keystroke_sent = True
        except Exception:
            pass

    if keystroke_sent:
        return

    # Try keyboard library first (works well on Windows)
    try:
        import keyboard  # type: ignore

        from .utils import paste_keystroke

        mod, key = paste_keystroke()
        combo_parts = [mod]
        if linux_use_shift and mod == "ctrl":
            combo_parts.append("shift")
        combo_parts.append(key)
        keyboard.send("+".join(combo_parts))
        return
    except Exception:
        pass

    # Fallback to pynput (works on macOS with Accessibility permission)
    try:
        from pynput.keyboard import Controller, Key

        from .utils import paste_keystroke

        k = Controller()
        mod, key = paste_keystroke()
        if mod == "command":
            with k.pressed(Key.cmd):
                k.press("v")
                k.release("v")
        else:
            if linux_use_shift and mod == "ctrl":
                with k.pressed(Key.ctrl):
                    with k.pressed(Key.shift):
                        k.press(key)
                        k.release(key)
            else:
                with k.pressed(Key.ctrl):
                    k.press(key)
                    k.release(key)
    except Exception as e:
        print(
            Fore.LIGHTRED_EX + f"Unable to send paste keystroke: {e}" + Style.RESET_ALL
        )


def _check_macos_accessibility() -> bool:
    """Check if the current process has accessibility permissions on macOS."""
    try:
        import platform

        if platform.system() != "Darwin":
            return True  # Not macOS, no check needed

        # Try to import and use the macOS-specific accessibility check
        import os
        import subprocess

        # Get the parent process (Terminal/iTerm) that's running Python
        # We check if the terminal app has accessibility permissions
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=1,
        )

        if result.returncode == 0:
            # If we can query System Events, we have accessibility permissions
            return True
        else:
            # Permission denied or other error
            return False
    except Exception:
        # If anything fails, assume we don't have permissions (safe default)
        return False


def run(
    hotkey: str,
    model: str,
    rate: int,
    device: Optional[int],
    no_sound: bool,
    push_to_talk: bool,
):
    colorama_init()  # colour support on Windows terminals
    mode_desc = "Push-to-talk" if push_to_talk else "Toggle"
    print(
        Fore.LIGHTBLUE_EX
        + f"VoiceMode ready ({mode_desc}). Hotkey {hotkey}. Ctrl+C to quit."
        + Style.RESET_ALL
    )

    # Import heavy modules lazily so --help works without installs
    try:
        from .audio import AudioConfig, AudioRecorder
        from .settings import resolve_openai_key
        from .sounds import play_start, play_stop
        from .transcribe import OpenAITranscriber
        from .utils import float_to_wav_bytes
    except ModuleNotFoundError as e:
        _hint_install(e.name or "a required package")
        return

    # Ensure an API key is available (env or config)
    key = resolve_openai_key()
    if not key:
        print(
            Fore.LIGHTRED_EX
            + "No OPENAI_API_KEY found. Set env var or run:\n  voicemode config --set-openai-key sk-...\n"
            + Style.RESET_ALL
        )
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
            print(
                Fore.LIGHTBLUE_EX
                + f"Listening… press {hotkey} again to stop."
                + Style.RESET_ALL
            )

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

    # Detect platform
    import platform

    is_macos = platform.system() == "Darwin"

    # On macOS, skip keyboard library entirely and use pynput
    # On other platforms, try keyboard library first
    if not is_macos:
        try:
            import keyboard  # type: ignore

            if push_to_talk:
                key = hotkey.lower()
                keyboard.on_press_key(key, lambda e: _start())
                keyboard.on_release_key(key, lambda e: _stop_and_transcribe())
            else:
                keyboard.add_hotkey(hotkey, toggle)
            keyboard_success = True
        except (ImportError, OSError, Exception):
            pass

    # If keyboard library didn't work, try pynput as a cross-platform fallback
    if not keyboard_success:
        try:
            from pynput import keyboard as pk

            # Map hotkey string to pynput key
            hotkey_upper = hotkey.upper()
            target_key = None

            # Handle function keys
            if hotkey_upper.startswith("F") and hotkey_upper[1:].isdigit():
                func_num = hotkey_upper[1:]
                target_key = getattr(pk.Key, f"f{func_num}", None)
            # Handle single letters/numbers
            elif len(hotkey_upper) == 1:
                target_key = hotkey_upper.lower()
            # Handle special keys
            else:
                key_map = {
                    "SPACE": pk.Key.space,
                    "ENTER": pk.Key.enter,
                    "TAB": pk.Key.tab,
                    "ESC": pk.Key.esc,
                    "ESCAPE": pk.Key.esc,
                }
                target_key = key_map.get(hotkey_upper)

            if not target_key:
                raise ValueError(f"Unsupported hotkey: {hotkey}")

            def on_press(key):
                try:
                    # Check if pressed key matches our target
                    if isinstance(target_key, str):
                        if hasattr(key, "char") and key.char == target_key:
                            if push_to_talk:
                                _start()
                            else:
                                toggle()
                    elif key == target_key:
                        if push_to_talk:
                            _start()
                        else:
                            toggle()
                except Exception:
                    pass

            def on_release(key):
                try:
                    # Check if released key matches our target
                    if isinstance(target_key, str):
                        if hasattr(key, "char") and key.char == target_key:
                            if push_to_talk:
                                _stop_and_transcribe()
                    elif key == target_key:
                        if push_to_talk:
                            _stop_and_transcribe()
                except Exception:
                    pass

            listener = pk.Listener(
                on_press=on_press, on_release=on_release if push_to_talk else None
            )
            listener.daemon = True
            listener.start()

            # Show platform-specific message
            if is_macos:
                # Only show warning if accessibility permissions are not granted
                if not _check_macos_accessibility():
                    print(
                        Fore.LIGHTYELLOW_EX
                        + "\n⚠️  macOS: Please ensure Terminal/iTerm has Accessibility permissions.\n"
                        + "   System Settings → Privacy & Security → Accessibility\n"
                        + "   Add and enable your terminal application.\n"
                        + Style.RESET_ALL
                    )
            else:
                print(
                    Fore.LIGHTYELLOW_EX + "Using pynput for hotkeys." + Style.RESET_ALL
                )
        except Exception as e:
            print(
                Fore.LIGHTRED_EX + f"Failed to register hotkey: {e}" + Style.RESET_ALL
            )
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


# ----------------
# Server utilities
# ----------------


def _socket_path() -> Path:
    try:
        from .settings import config_dir

        return config_dir() / "voiceapp.sock"
    except Exception:
        return Path.home() / ".config" / "voiceapp" / "voiceapp.sock"


def _send_command(
    cmd: str, payload: Optional[dict] = None, timeout: float = 2.0
) -> dict:
    path = _socket_path()
    data = {"cmd": cmd}
    if payload:
        data.update(payload)
    req = (json.dumps(data) + "\n").encode("utf-8")
    resp: dict = {}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(req)
        # Read one line JSON
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
            if b.endswith(b"\n"):
                break
    if chunks:
        try:
            resp = json.loads(b"".join(chunks).decode("utf-8").strip())
        except Exception:
            resp = {}
    return resp


def _start_server_background(
    model: str, rate: int, device: Optional[int], no_sound: bool
) -> subprocess.Popen:
    # Spawn a detached process: python -m voiceapp serve ...
    args = [
        sys.executable,
        "-m",
        "voiceapp",
        "serve",
        "--model",
        model,
        "--rate",
        str(rate),
    ]
    if device is not None:
        args += ["--device", str(device)]
    if no_sound:
        args += ["--no-sound"]

    # Redirect server output to a log file
    log_dir = _socket_path().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "server.log"
    f_out = open(log_file, "a", buffering=1)
    # Ensure env carries API key etc.
    env = os.environ.copy()
    return subprocess.Popen(
        args,
        stdout=f_out,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        close_fds=True,
    )


def _ensure_server(
    model: str, rate: int, device: Optional[int], no_sound: bool, wait_secs: float = 2.5
) -> bool:
    path = _socket_path()
    # If socket exists but not connectable, remove it and restart
    try:
        resp = _send_command("ping", timeout=0.5)
        if resp.get("ok"):
            return True
    except Exception:
        pass

    # Start background server
    proc = _start_server_background(
        model=model, rate=rate, device=device, no_sound=no_sound
    )
    # Wait for socket to become ready
    t0 = time.time()
    while time.time() - t0 < wait_secs:
        try:
            resp = _send_command("ping", timeout=0.5)
            if resp.get("ok"):
                return True
        except Exception:
            time.sleep(0.1)
    # Server didn't start quickly; leave process running but report failure
    try:
        proc.poll()
    except Exception:
        pass
    return False


def serve(model: str, rate: int, device: Optional[int], no_sound: bool):
    """Run a simple Unix-socket server to handle toggle requests globally."""
    colorama_init()
    try:
        from .audio import AudioConfig, AudioRecorder
        from .settings import resolve_openai_key
        from .sounds import play_start, play_stop
        from .transcribe import OpenAITranscriber
        from .utils import float_to_wav_bytes
    except ModuleNotFoundError as e:
        _hint_install(e.name or "a required package")
        return 1

    key = resolve_openai_key()
    if not key:
        print(
            Fore.LIGHTRED_EX
            + "No OPENAI_API_KEY found. Set env var or run:\n  voicemode config --set-openai-key sk-...\n"
            + Style.RESET_ALL
        )
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key

    cfg = AudioConfig(sample_rate=rate, device=device)
    recorder = AudioRecorder(cfg)
    recorder.start()
    transcriber = OpenAITranscriber(model=model)
    listening = {"active": False}

    # Notification helpers (Linux only)
    notif = {"id": None}

    def _notify_linux(
        message: str, persistent: bool = False, expire_ms: Optional[int] = None
    ):
        try:
            if sys.platform != "linux":
                return
            ns = shutil.which("notify-send")
            if not ns:
                return
            # Prepare args
            args = [ns, "--app-name", "VoiceMode"]
            if persistent and expire_ms is None:
                args += ["-t", "0"]
            elif expire_ms is not None:
                args += ["-t", str(int(expire_ms))]
            if notif["id"] is None:
                # First notification: ask for ID so we can replace later
                args += ["--print-id", message]
                out = subprocess.run(
                    args, capture_output=True, text=True
                ).stdout.strip()
                if out.isdigit():
                    notif["id"] = int(out)
            else:
                # Replace existing
                args += ["-r", str(notif["id"]), message]
                subprocess.run(args, check=False)
        except Exception:
            pass

    def _notify_listening():
        _notify_linux("Listening…", persistent=True)

    def _notify_transcribing():
        _notify_linux("Transcribing…", persistent=True)

    def _notify_complete():
        _notify_linux("Transcription complete", expire_ms=2000)
        # Reset ID so next session creates a new persistent notification
        notif["id"] = None

    def _start():
        if listening["active"]:
            return "already"
        listening["active"] = True
        play_start(no_sound)
        recorder.begin_session()
        _notify_listening()
        return "started"

    def _stop_and_transcribe():
        if not listening["active"]:
            return "not_active"
        listening["active"] = False
        play_stop(no_sound)
        _notify_transcribing()
        frames = recorder.end_session()
        wav_bytes = float_to_wav_bytes(frames, rate)
        if not wav_bytes:
            print(Fore.LIGHTRED_EX + "No audio captured." + Style.RESET_ALL)
            _notify_complete()
            return "no_audio"
        try:
            text = transcriber.transcribe_wav_bytes(wav_bytes)
            if text:
                _paste_text(text)
                print(Fore.LIGHTBLUE_EX + f"Typed: {text}" + Style.RESET_ALL)
                _notify_complete()
                return "transcribed"
            else:
                print(Fore.LIGHTRED_EX + "(Empty transcription)" + Style.RESET_ALL)
                _notify_complete()
                return "empty"
        except Exception as e:
            print(Fore.LIGHTRED_EX + f"Transcription error: {e}" + Style.RESET_ALL)
            _notify_complete()
            return "error"

    # Prepare socket
    sock_path = _socket_path()
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sock_path.exists():
            sock_path.unlink()
    except Exception:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        srv.listen(5)
    except Exception as e:
        print(Fore.LIGHTRED_EX + f"Server bind/listen failed: {e}" + Style.RESET_ALL)
        try:
            srv.close()
        except Exception:
            pass
        return 1

    print(
        Fore.LIGHTBLUE_EX
        + f"VoiceApp server listening at {sock_path}"
        + Style.RESET_ALL
    )

    running = True
    try:
        while running:
            conn, _ = srv.accept()
            with conn:
                shutdown_requested = False
                try:
                    buf = b""
                    while True:
                        b = conn.recv(4096)
                        if not b:
                            break
                        buf += b
                        if b"\n" in b:
                            break
                    cmd = {}
                    try:
                        cmd = json.loads(buf.decode("utf-8").strip())
                    except Exception:
                        pass
                    action = (cmd.get("cmd") or "").lower()
                    status = "unknown"
                    if action in {"ping", "status"}:
                        status = "listening" if listening["active"] else "idle"
                        resp = {"ok": True, "status": status}
                    elif action == "toggle":
                        if listening["active"]:
                            status = _stop_and_transcribe()
                        else:
                            status = _start()
                        resp = {
                            "ok": True,
                            "result": status,
                            "listening": listening["active"],
                        }
                    elif action == "start":
                        status = _start()
                        resp = {
                            "ok": True,
                            "result": status,
                            "listening": listening["active"],
                        }
                    elif action == "stop":
                        status = _stop_and_transcribe()
                        resp = {
                            "ok": True,
                            "result": status,
                            "listening": listening["active"],
                        }
                    elif action in {"shutdown", "stop-server", "quit"}:
                        if listening["active"]:
                            status = _stop_and_transcribe()
                        else:
                            status = "stopped"
                        resp = {
                            "ok": True,
                            "result": status,
                            "listening": listening["active"],
                        }
                        shutdown_requested = True
                    else:
                        resp = {"ok": False, "error": "unknown_command"}
                except Exception as e:
                    resp = {"ok": False, "error": str(e)}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                except Exception:
                    pass
                if shutdown_requested:
                    running = False
                    print(
                        Fore.LIGHTBLUE_EX
                        + "VoiceApp server shutdown requested. Exiting…"
                        + Style.RESET_ALL
                    )
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.close()
        except Exception:
            pass
        try:
            if sock_path.exists():
                sock_path.unlink()
        except Exception:
            pass
    return 0


def main(argv: Optional[list[str]] = None):
    # Load environment variables from a local .env if present
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    DEFAULT_MODEL = "gpt-4o-transcribe"

    parser = argparse.ArgumentParser(
        prog="voicemode", description="VoiceMode – voice dictation with OpenAI STT"
    )

    # Interactive options at top level so running without subcommand behaves like before
    parser.add_argument(
        "--hotkey", default="F8", help="Global hotkey to toggle listening (default: F8)"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model to use")
    parser.add_argument(
        "--rate", type=int, default=16000, help="Sample rate (Hz), default 16000"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Input device index (see --list-devices)",
    )
    parser.add_argument(
        "--no-sound", action="store_true", help="Disable start/stop sounds"
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices and exit"
    )
    parser.add_argument(
        "--push-to-talk",
        action="store_true",
        help="Hold hotkey to record; release to transcribe",
    )

    sub = parser.add_subparsers(dest="cmd")

    # Server mode (default)
    ps = sub.add_parser("serve", help="Run background server (Unix socket)")
    ps.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model to use")
    ps.add_argument(
        "--rate", type=int, default=16000, help="Sample rate (Hz), default 16000"
    )
    ps.add_argument(
        "--device",
        type=int,
        default=None,
        help="Input device index (see --list-devices)",
    )
    ps.add_argument("--no-sound", action="store_true", help="Disable start/stop sounds")

    # Client toggle
    pt = sub.add_parser("toggle", help="Toggle listening (start/stop+transcribe)")
    pt.add_argument("--model", default=DEFAULT_MODEL, help="Model if starting server")
    pt.add_argument("--rate", type=int, default=16000, help="Rate if starting server")
    pt.add_argument(
        "--device", type=int, default=None, help="Device if starting server"
    )
    pt.add_argument(
        "--no-sound", action="store_true", help="No sound for server if starting"
    )

    # Status
    sub.add_parser("status", help="Show server status")

    # Stop background server
    sub.add_parser("stop", help="Stop background server if running")

    # Legacy interactive mode (kept for completeness)
    pi = sub.add_parser("interactive", help="Run interactive hotkey mode in foreground")
    pi.add_argument(
        "--hotkey", default="F8", help="Global hotkey to toggle listening (default: F8)"
    )
    pi.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model to use")
    pi.add_argument(
        "--rate", type=int, default=16000, help="Sample rate (Hz), default 16000"
    )
    pi.add_argument(
        "--device",
        type=int,
        default=None,
        help="Input device index (see --list-devices)",
    )
    pi.add_argument("--no-sound", action="store_true", help="Disable start/stop sounds")
    pi.add_argument(
        "--list-devices", action="store_true", help="List audio devices and exit"
    )
    pi.add_argument(
        "--push-to-talk",
        action="store_true",
        help="Hold hotkey to record; release to transcribe",
    )

    # Config
    cfg = sub.add_parser("config", help="Configure settings")
    cfg.add_argument(
        "--set-openai-key",
        dest="set_openai_key",
        help="Set and save your OpenAI API key",
    )

    args = parser.parse_args(argv)

    # No subcommand: run interactive mode (legacy default behavior)
    if not args.cmd:
        if args.list_devices:
            return _list_devices()
        return run(
            args.hotkey,
            args.model,
            args.rate,
            args.device,
            args.no_sound,
            args.push_to_talk,
        )

    if args.cmd == "config":
        if getattr(args, "set_openai_key", None):
            from .settings import load_settings, save_settings

            data = load_settings()
            data["OPENAI_API_KEY"] = args.set_openai_key
            path = save_settings(data)
            print(
                Fore.LIGHTBLUE_EX + f"Saved OPENAI_API_KEY to {path}" + Style.RESET_ALL
            )
            return 0
        parser.parse_args(["config", "--help"])  # show help
        return 0

    if args.cmd == "serve":
        # Only supported on Linux/macOS (Unix domain sockets)
        if sys.platform.startswith("win"):
            print(
                Fore.LIGHTRED_EX
                + "'serve' is only supported on Linux/macOS"
                + Style.RESET_ALL
            )
            return 2
        return serve(
            model=args.model, rate=args.rate, device=args.device, no_sound=args.no_sound
        )

    if args.cmd == "toggle":
        if sys.platform.startswith("win"):
            print(
                Fore.LIGHTRED_EX
                + "'toggle' is only supported on Linux/macOS"
                + Style.RESET_ALL
            )
            return 2
        # Ensure server, then send toggle
        ok = _ensure_server(
            model=args.model, rate=args.rate, device=args.device, no_sound=args.no_sound
        )
        if not ok:
            print(Fore.LIGHTRED_EX + "Server not available" + Style.RESET_ALL)
            return 1
        try:
            resp = _send_command("toggle", timeout=5.0)
            if not resp.get("ok"):
                print(Fore.LIGHTRED_EX + f"Toggle failed: {resp}" + Style.RESET_ALL)
                return 1
            state = resp.get("listening")
            if state:
                print(
                    Fore.LIGHTBLUE_EX
                    + "Listening… (toggle again to stop)"
                    + Style.RESET_ALL
                )
            else:
                print(Fore.LIGHTBLUE_EX + "Stopped and transcribed." + Style.RESET_ALL)
            return 0
        except Exception as e:
            print(Fore.LIGHTRED_EX + f"Toggle error: {e}" + Style.RESET_ALL)
            return 1

    if args.cmd == "status":
        if sys.platform.startswith("win"):
            print(
                Fore.LIGHTRED_EX
                + "'status' is only supported on Linux/macOS"
                + Style.RESET_ALL
            )
            return 2
        try:
            resp = _send_command("status", timeout=1.5)
            if resp.get("ok"):
                print(
                    Fore.LIGHTBLUE_EX
                    + f"Server: {resp.get('status')}"
                    + Style.RESET_ALL
                )
                return 0
        except Exception:
            pass
        print(Fore.LIGHTRED_EX + "Server not running" + Style.RESET_ALL)
        return 1

    if args.cmd == "stop":
        if sys.platform.startswith("win"):
            print(
                Fore.LIGHTRED_EX
                + "'stop' is only supported on Linux/macOS"
                + Style.RESET_ALL
            )
            return 2
        try:
            resp = _send_command("shutdown", timeout=1.5)
            if resp.get("ok"):
                print(
                    Fore.LIGHTBLUE_EX
                    + "Server stopped"
                    + Style.RESET_ALL
                )
                return 0
            print(Fore.LIGHTRED_EX + f"Stop failed: {resp}" + Style.RESET_ALL)
            return 1
        except Exception:
            print(Fore.LIGHTRED_EX + "Server not running" + Style.RESET_ALL)
            return 1

    if args.cmd == "interactive":
        if args.list_devices:
            return _list_devices()
        return run(
            args.hotkey,
            args.model,
            args.rate,
            args.device,
            args.no_sound,
            args.push_to_talk,
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
