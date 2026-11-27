import asyncio

from app.checks import FAIL, OK, SKIP, check_data, check_model, check_speech
from app.config import Settings
from app.errors import explain_model_error, explain_speech_error
from app.llm import LLMError, Reply, ScriptedChat
from app.speech import Speech, SpeechError
from tests.conftest import DATA


class FailingChat(ScriptedChat):
    def __init__(self, error):
        super().__init__([])
        self.error = error

    async def step(self):
        raise self.error


class Voice:
    def __init__(self, error=None):
        self.error = error

    async def synthesize(self, text, language):
        if self.error:
            raise self.error
        return Speech(b"x" * 4096, language, False)


def settings(**extra):
    return Settings(data_file=str(DATA), **extra)


def test_data_check():
    assert check_data(settings()).status == OK
    assert check_data(Settings(data_file="missing.xlsx")).status == FAIL


def test_model_check():
    assert asyncio.run(check_model(settings())).status == FAIL
    ready = settings(gemini_api_key="k")
    assert asyncio.run(check_model(ready, ScriptedChat([Reply(text="OK")]))).status == OK
    bad = FailingChat(LLMError('Model API returned 400: {"error": {"message": "API key not valid."}}'))
    result = asyncio.run(check_model(ready, bad))
    assert result.status == FAIL and "GEMINI_API_KEY" in result.message


def test_speech_check():
    assert asyncio.run(check_speech(settings())).status == SKIP
    assert asyncio.run(check_speech(settings(), Voice())).status == OK
    result = asyncio.run(check_speech(settings(), Voice(SpeechError("Text-to-speech returned 403: forbidden"))))
    assert result.status == FAIL and "SARVAM_API_KEY" in result.message


def test_error_messages_are_plain():
    assert "quota" in explain_model_error(LLMError("Model API returned 429: RESOURCE_EXHAUSTED"))
    assert "GEMINI_MODEL" in explain_model_error(LLMError("Model API returned 404: not found"))
    assert "internet" in explain_model_error(LLMError("Could not reach the model API: timed out"))
    assert "credit" in explain_speech_error(SpeechError("Speech-to-text returned 429: slow down"))
