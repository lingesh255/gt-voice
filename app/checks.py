from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Settings
from .data import DataError, Dataset
from .errors import explain_model_error, explain_speech_error
from .llm import LLMError
from .speech import SpeechError

OK, SKIP, FAIL = "ok", "skip", "fail"


@dataclass
class Check:
    name: str
    status: str
    message: str


def check_data(settings: Settings) -> Check:
    try:
        data = Dataset.load(settings.data_file)
    except DataError as exc:
        return Check("Data", FAIL, str(exc))
    return Check("Data", OK, f"{len(data.retailers)} retailers, {len(data.products)} SKUs, "
                             f"{data.purchase_rows} purchase lines, as of {data.as_of:%d %b %Y}")


async def check_model(settings: Settings, chat=None) -> Check:
    if not settings.llm_ready:
        return Check("Model", FAIL, "No key set. Add GEMINI_API_KEY to .env (free key at aistudio.google.com/apikey).")
    started = time.monotonic()
    try:
        chat = chat or settings.chat(retries=0)
        chat.reset("Reply with the single word OK.", [])
        chat.add_user("ping")
        reply = await chat.step()
    except LLMError as exc:
        return Check("Model", FAIL, explain_model_error(exc))
    took = round((time.monotonic() - started) * 1000)
    if not reply.text:
        return Check("Model", FAIL, f"{settings.model_label} answered with nothing. Try another GEMINI_MODEL.")
    return Check("Model", OK, f"{settings.model_label} answered in {took} ms")


async def check_speech(settings: Settings, speech=None) -> Check:
    speech = speech or settings.speech()
    if speech is None:
        return Check("Speech", SKIP, "SARVAM_API_KEY not set; the browser's own speech will be used (Chrome or Edge).")
    started = time.monotonic()
    try:
        audio = await speech.synthesize("नमस्ते", "hi-IN")
    except SpeechError as exc:
        return Check("Speech", FAIL, explain_speech_error(exc))
    took = round((time.monotonic() - started) * 1000)
    return Check("Speech", OK, f"Sarvam voice ready ({len(audio.audio) // 1024} KB sample in {took} ms)")


async def run_checks(settings: Settings, chat=None, speech=None) -> list[Check]:
    return [check_data(settings), await check_model(settings, chat), await check_speech(settings, speech)]
