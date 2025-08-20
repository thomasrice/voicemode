from __future__ import annotations

import os
import threading
from importlib import resources
from typing import Optional

import numpy as np
import sounddevice as sd


def _package_wav_path(name: str) -> Optional[str]:
    """Return filesystem path for packaged WAV if present."""
    try:
        # Prefer real file if packaged
        with resources.as_file(resources.files("voiceapp.assets").joinpath(f"{name}.wav")) as p:
            if p.exists():
                return str(p)
    except Exception:
        return None
    return None


def _play_file_async(path: str):
    def run():
        try:
            import wave as _wave

            with _wave.open(path, "rb") as wf:
                nch = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                fr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

            if sampwidth != 2:
                # Only 16-bit PCM is supported here; fall back to tone
                _play_tone_async(880.0, 0.08)
                return

            dtype = np.int16
            data = np.frombuffer(frames, dtype=dtype)
            if nch > 1:
                data = data.reshape(-1, nch)
                data = data.mean(axis=1).astype(np.int16)  # simple mono mixdown

            # Convert to float32 in [-1, 1]
            f32 = (data.astype(np.float32) / 32767.0).astype(np.float32)
            sd.play(f32, samplerate=fr, blocking=False)
        except Exception:
            _play_tone_async(880.0, 0.08)

    threading.Thread(target=run, daemon=True).start()


def _play_tone_async(freq_hz: float, seconds: float, volume: float = 0.25):
    def run():
        try:
            sr = 16000
            t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
            wave = (np.sin(2 * np.pi * freq_hz * t) * volume).astype(np.float32)
            sd.play(wave, samplerate=sr, blocking=False)
        except Exception:
            # As a last resort, ignore sound failures
            pass

    threading.Thread(target=run, daemon=True).start()


def play_start(no_sound: bool = False):
    if no_sound:
        return
    path = _package_wav_path("start")
    if path and os.path.exists(path):
        _play_file_async(path)
    else:
        _play_tone_async(1046.5, 0.09)  # C6 beep


def play_stop(no_sound: bool = False):
    if no_sound:
        return
    path = _package_wav_path("stop")
    if path and os.path.exists(path):
        _play_file_async(path)
    else:
        _play_tone_async(523.25, 0.09)  # C5 beep

