from __future__ import annotations

import io
import platform
import wave
from typing import Iterable

import numpy as np


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_windows() -> bool:
    return platform.system() == "Windows"


def paste_keystroke() -> tuple[str, ...]:
    """Return the modifier+key combo for paste appropriate to the OS."""
    return ("command", "v") if is_macos() else ("ctrl", "v")


def float_to_wav_bytes(frames: Iterable[np.ndarray], sample_rate: int) -> bytes:
    """Convert a sequence of float32 numpy blocks to a mono int16 WAV in memory.

    - Clips to [-1.0, 1.0]
    - Converts to PCM 16-bit
    - Writes a valid RIFF/WAVE file
    """
    if isinstance(frames, np.ndarray):
        audio = frames
    else:
        audio = np.concatenate(list(frames), axis=0) if frames else np.array([], dtype=np.float32)

    audio = np.asarray(audio, dtype=np.float32).flatten()
    if audio.size == 0:
        return b""

    i16 = np.clip(audio, -1.0, 1.0)
    i16 = (i16 * 32767.0).astype(np.int16)

    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())
    return bio.getvalue()

