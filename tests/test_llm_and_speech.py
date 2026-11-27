import asyncio
import base64
import json

import httpx
import pytest

from app.agent import SalesAgent
from app.llm import GeminiChat, LLMError, OpenAIChat, Reply, ScriptedChat, ToolCall
from app.speech import SarvamSpeech, SpeechError
from app.tools import TOOLS


def run(coro):
    return asyncio.run(coro)


def gemini_with(handler):
    return GeminiChat("key", "gemini-flash-latest", "low",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_gemini_request_shape_and_signature_round_trip():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request, body))
        if len(seen) == 1:
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"id": "c1", "name": "get_call_brief", "args": {}}, "thoughtSignature": "sig-1"}]}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
            {"text": "Namaste!"}]}}]})

    chat = gemini_with(handler)
    chat.reset("system text", TOOLS)
    chat.add_user("hello")
    reply = run(chat.step())
    assert reply.tool_calls[0].name == "get_call_brief" and reply.tool_calls[0].id == "c1"
    chat.add_tool_results([(reply.tool_calls[0], {"ok": True})])
    assert run(chat.step()).text == "Namaste!"

    request, body = seen[0]
    assert request.url.path.endswith("/models/gemini-flash-latest:generateContent")
    assert request.headers["x-goog-api-key"] == "key"
    assert body["systemInstruction"]["parts"][0]["text"] == "system text"
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
    declarations = {d["name"]: d for d in body["tools"][0]["functionDeclarations"]}
    assert "parameters" not in declarations["get_call_brief"]
    assert declarations["add_to_order"]["parameters"]["type"] == "object"

    _, second = seen[1]
    model_turn, tool_turn = second["contents"][1], second["contents"][2]
    assert model_turn["role"] == "model" and model_turn["parts"][0]["thoughtSignature"] == "sig-1"
    response = tool_turn["parts"][0]["functionResponse"]
    assert tool_turn["role"] == "user" and response == {"name": "get_call_brief", "response": {"ok": True}, "id": "c1"}


def test_gemini_retries_without_thinking_when_unsupported():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "generationConfig" in body:
            return httpx.Response(400, json={"error": {"message": "Thinking level is not supported for this model."}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    chat = gemini_with(handler)
    chat.reset("s", TOOLS)
    chat.add_user("hi")
    assert run(chat.step()).text == "ok"
    assert len(calls) == 2 and "generationConfig" not in calls[1]


def test_gemini_skips_thought_text_and_empty_content():
    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": "Ji, bataiye."}]}}]})

    chat = gemini_with(handler)
    chat.reset("s", TOOLS)
    chat.add_user("hi")
    assert run(chat.step()).text == "Ji, bataiye."

    empty = gemini_with(lambda r: httpx.Response(200, json={"candidates": [{"finishReason": "OTHER"}]}))
    empty.reset("s", TOOLS)
    empty.add_user("hi")
    assert run(empty.step()).text == "" and len(empty.contents) == 1


def test_gemini_errors_are_reported():
    chat = gemini_with(lambda r: httpx.Response(403, json={"error": {"message": "API key not valid"}}))
    chat.reset("s", TOOLS)
    chat.add_user("hi")
    with pytest.raises(LLMError, match="403"):
        run(chat.step())


def test_openai_round_trip_keeps_extra_fields():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "t1", "type": "function", "extra_content": {"google": {"thought_signature": "x"}},
                                "function": {"name": "check_stock", "arguments": "{\"sku\": \"P001\"}"}}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Stock hai."}}]})

    chat = OpenAIChat("key", "some-model", "https://example.test/v1",
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    chat.reset("system", TOOLS)
    chat.add_user("hi")
    reply = run(chat.step())
    assert reply.tool_calls[0].arguments == {"sku": "P001"}
    chat.add_tool_results([(reply.tool_calls[0], {"ok": True, "available_units": 5})])
    assert run(chat.step()).text == "Stock hai."
    assert "temperature" not in seen[0]
    assert seen[1]["messages"][2]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "x"
    assert seen[1]["messages"][3] == {"role": "tool", "tool_call_id": "t1",
                                      "content": json.dumps({"ok": True, "available_units": 5})}


def test_agent_runs_tools_and_tags_language(call):
    session = call("R003", confirmed=False)
    chat = ScriptedChat([
        Reply(text="नमस्ते! मैं Marico की तरफ़ से बोल रहा हूँ। क्या मेरी बात Friends Provisions से हो रही है?"),
        Reply(tool_calls=[ToolCall("1", "confirm_identity", {"is_right_shop": True}),
                          ToolCall("2", "get_call_brief", {})]),
        Reply(text="Thank you! How is your stock of Set Wet deodorant?"),
    ])
    agent = SalesAgent(session, chat)
    opening = run(agent.open())
    assert opening.startswith("नमस्ते") and session.language == "hi-IN"
    assert "Friends Provisions" in chat.system and len(chat.tools) == len(TOOLS)
    answer = run(agent.respond("Yes, this is Friends Provisions", "en-IN"))
    assert answer.startswith("Thank you") and session.language == "en-IN"
    assert session.identity is True and session.brief_shared
    assert [r[0].name for r in chat.results[0]] == ["confirm_identity", "get_call_brief"]
    assert "speech detected as English" in chat.inbox[-1]


def test_agent_marks_unclear_audio(call):
    session = call("R003")
    chat = ScriptedChat([Reply(text="Maaf kijiye, phir se boliye?")])
    agent = SalesAgent(session, chat)
    run(agent.respond("", "hi-IN", unclear=True))
    assert chat.inbox[-1].startswith("[unclear]")


def test_agent_can_resume_after_model_error(call):
    session = call("R003")
    chat = ScriptedChat([])
    agent = SalesAgent(session, chat)
    with pytest.raises(LLMError):
        run(agent.respond("haan bolo"))
    chat.replies.append(Reply(text="Ji, bataiye."))
    assert run(agent.resume()) == "Ji, bataiye."
    assert run(agent.resume()) == ""


def test_agent_stops_tool_loops(call):
    session = call("R003")
    chat = ScriptedChat([Reply(tool_calls=[ToolCall(str(i), "check_stock", {"sku": "P001"})]) for i in range(20)])
    agent = SalesAgent(session, chat, max_steps=4)
    with pytest.raises(LLMError):
        run(agent.respond("stock?"))


def test_sarvam_requests_match_the_api():
    seen = []
    wav = b"RIFF0000WAVEfmt "

    def handler(request):
        seen.append(request)
        if request.url.path == "/speech-to-text":
            return httpx.Response(200, json={"request_id": "r", "transcript": "पंद्रह भेज दो",
                                             "language_code": "hi-IN", "language_probability": 0.93})
        return httpx.Response(200, json={"request_id": "r", "audios": [base64.b64encode(b"audio").decode()]})

    speech = SarvamSpeech("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    heard = run(speech.transcribe(wav))
    assert heard.text == "पंद्रह भेज दो" and heard.language == "hi-IN" and heard.confidence == 0.93
    stt = seen[0]
    assert stt.headers["api-subscription-key"] == "k"
    form = stt.content.decode("latin-1")
    for field in ('name="model"', "saaras:v4", 'name="mode"', "transcribe", 'name="language_code"', "unknown",
                  'name="file"; filename="turn.wav"'):
        assert field in form

    out = run(speech.synthesize("Namaste", "as-IN"))
    body = json.loads(seen[1].content)
    assert out.audio == b"audio" and out.fallback and out.language == "bn-IN"
    assert body["language_code"] == "bn-IN" and body["model"] == "bulbul:v3" and body["speaker"] == "shubh"


def test_sarvam_errors_raise():
    speech = SarvamSpeech("k", client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={"error": {"message": "rate limited"}}))))
    with pytest.raises(SpeechError):
        run(speech.synthesize("hi", "hi-IN"))
