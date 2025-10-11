from __future__ import annotations

import time
from typing import Optional

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-transcribe"


class OpenAITranscriber:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: OpenAI | None = None,
        max_retries: int = 2,
    ):
        # The client is created lazily to avoid requiring an API key in unit tests
        self.client: OpenAI | None = client
        self.model = model
        self.max_retries = max(0, int(max_retries))

    def transcribe_wav_bytes(
        self, wav_bytes: bytes, timeout: Optional[float] = 60.0
    ) -> str:
        if not wav_bytes:
            return ""
        # The OpenAI SDK accepts (filename, bytes, mimetype)
        content = ("speech.wav", wav_bytes, "audio/wav")
        # Retry transient failures
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                client = self.client or OpenAI()
                resp = client.audio.transcriptions.create(
                    model=self.model, file=content, timeout=timeout
                )
                text = getattr(resp, "text", "")
                return (text or "").strip()
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2.0
                else:
                    raise
        return ""
