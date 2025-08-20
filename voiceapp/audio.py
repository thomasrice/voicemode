from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd


@dataclass
class AudioConfig:
    channels: int = 1
    sample_rate: int = 16000
    dtype: str = "float32"
    blocksize: int = 1024
    device: Optional[int] = None  # default device


class AudioRecorder:
    """Managed microphone recorder with robust restart on errors.

    Use start()/stop() to manage the background stream thread.
    Call begin_session() to start buffering frames; end_session() returns collected frames.
    """

    def __init__(self, config: AudioConfig):
        self.cfg = config
        self._stream: Optional[sd.InputStream] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._session_active = threading.Event()
        self._frames_lock = threading.Lock()
        self._frames: Optional[list[np.ndarray]] = None
        self._last_frame_time = 0.0

    def _callback(self, indata, frames, t, status):
        if status:
            # status may contain under/overruns; still accept audio
            pass
        if not self._session_active.is_set():
            return
        with self._frames_lock:
            if self._frames is not None:
                self._frames.append(indata.copy())
                self._last_frame_time = time.time()

    def _stream_loop(self):
        while not self._stop_event.is_set():
            try:
                kwargs = dict(
                    channels=self.cfg.channels,
                    samplerate=self.cfg.sample_rate,
                    dtype=self.cfg.dtype,
                    blocksize=self.cfg.blocksize,
                    callback=self._callback,
                )
                if self.cfg.device is not None:
                    kwargs["device"] = self.cfg.device
                with sd.InputStream(**kwargs):
                    # Periodic keepalive; also detect inactivity if desired
                    while not self._stop_event.is_set():
                        time.sleep(0.1)
                # If stream exits cleanly, loop to recreate
            except Exception:
                # Backoff then retry
                time.sleep(1.0)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def begin_session(self):
        with self._frames_lock:
            self._frames = []
        self._last_frame_time = time.time()
        self._session_active.set()

    def end_session(self) -> list[np.ndarray]:
        self._session_active.clear()
        with self._frames_lock:
            frames = self._frames or []
            self._frames = None
        return frames
