from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from .languages import tts_language

SARVAM_URL = "https://api.sarvam.ai"


class SpeechError(Exception):
    pass


@dataclass
class Transcript:
    text: str
    language: str
    confidence: float | None


@dataclass
class Speech:
    audio: bytes
    language: str
    fallback: bool
    mime: str = "audio/wav"


class SarvamSpeech:
    def __init__(self, api_key: str, stt_model: str = "saaras:v4", tts_model: str = "bulbul:v3",
                 speaker: str = "shubh", pace: float = 1.0, base_url: str = SARVAM_URL, timeout: float = 20.0,
                 client: httpx.AsyncClient | None = None):
        if not api_key:
            raise SpeechError("SARVAM_API_KEY is not set.")
        self.api_key = api_key
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.speaker = speaker
        self.pace = pace
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def transcribe(self, audio: bytes, filename: str = "turn.wav", mime: str = "audio/wav") -> Transcript:
        try:
            response = await self.client.post(
                f"{self.base_url}/speech-to-text",
                headers={"api-subscription-key": self.api_key},
                files={"file": (filename, audio, mime)},
                data={"model": self.stt_model, "mode": "transcribe", "language_code": "unknown"},
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Speech-to-text request failed: {exc}") from exc
        if response.status_code != 200:
            raise SpeechError(f"Speech-to-text returned {response.status_code}: {response.text[:300]}")
        data = response.json()
        return Transcript(
            text=(data.get("transcript") or "").strip(),
            language=data.get("language_code") or "",
            confidence=data.get("language_probability"),
        )

    async def synthesize(self, text: str, language: str) -> Speech:
        code, fallback = tts_language(language)
        payload = {
            "text": text[:2500],
            "language_code": code,
            "target_language_code": code,
            "model": self.tts_model,
            "speaker": self.speaker,
            "pace": self.pace,
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/text-to-speech",
                headers={"api-subscription-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Text-to-speech request failed: {exc}") from exc
        if response.status_code != 200:
            raise SpeechError(f"Text-to-speech returned {response.status_code}: {response.text[:300]}")
        audios = response.json().get("audios") or []
        if not audios:
            raise SpeechError("Text-to-speech returned no audio.")
        return Speech(base64.b64decode("".join(audios)), code, fallback)
