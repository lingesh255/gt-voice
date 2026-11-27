from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .llm import ChatModel, GeminiChat, LLMError, OpenAIChat
from .speech import SarvamSpeech

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    gemini_thinking: str = "low"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v4"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_speaker: str = "shubh"
    data_file: str = str(ROOT / "data" / "Marico_Synthetic_Data_Final_Excel_Data.xlsx")
    runs_dir: str = str(ROOT / "runs")
    oms_webhook_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        defaults = cls()
        return cls(
            llm_provider=env("LLM_PROVIDER", defaults.llm_provider).strip().lower(),
            gemini_api_key=env("GEMINI_API_KEY", "").strip(),
            gemini_model=env("GEMINI_MODEL", defaults.gemini_model).strip(),
            gemini_thinking=env("GEMINI_THINKING_LEVEL", defaults.gemini_thinking).strip(),
            openai_api_key=env("OPENAI_API_KEY", "").strip(),
            openai_base_url=env("OPENAI_BASE_URL", defaults.openai_base_url).strip(),
            openai_model=env("OPENAI_MODEL", "").strip(),
            sarvam_api_key=env("SARVAM_API_KEY", "").strip(),
            sarvam_stt_model=env("SARVAM_STT_MODEL", defaults.sarvam_stt_model).strip(),
            sarvam_tts_model=env("SARVAM_TTS_MODEL", defaults.sarvam_tts_model).strip(),
            sarvam_speaker=env("SARVAM_SPEAKER", defaults.sarvam_speaker).strip(),
            data_file=_path(env("DATA_FILE", defaults.data_file)),
            runs_dir=_path(env("RUNS_DIR", defaults.runs_dir)),
            oms_webhook_url=env("OMS_WEBHOOK_URL", "").strip(),
        )

    @property
    def model_label(self) -> str:
        return self.gemini_model if self.llm_provider == "gemini" else self.openai_model

    @property
    def llm_ready(self) -> bool:
        if self.llm_provider == "gemini":
            return bool(self.gemini_api_key)
        return bool(self.openai_api_key and self.openai_model)

    def chat(self, retries: int = 2) -> ChatModel:
        if self.llm_provider == "gemini":
            return GeminiChat(self.gemini_api_key, self.gemini_model, self.gemini_thinking, retries=retries)
        if self.llm_provider in ("openai", "openai_compatible", "groq"):
            return OpenAIChat(self.openai_api_key, self.openai_model, self.openai_base_url, retries=retries)
        raise LLMError(f"Unknown LLM_PROVIDER {self.llm_provider!r}; use gemini or openai.")

    def speech(self) -> SarvamSpeech | None:
        if not self.sarvam_api_key:
            return None
        return SarvamSpeech(self.sarvam_api_key, self.sarvam_stt_model, self.sarvam_tts_model, self.sarvam_speaker)


def _path(value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else ROOT / path)
