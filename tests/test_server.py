import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import Reply, ScriptedChat, ToolCall
from app.outcomes import OutcomeStore
from app.server import create_app
from app.speech import Speech, Transcript
from tests.conftest import DATA


class FakeSpeech:
    def __init__(self, heard="15 bhej do", language="hi-IN"):
        self.heard = heard
        self.language = language
        self.spoken = []

    async def transcribe(self, audio, filename="turn.wav", mime="audio/wav"):
        return Transcript(self.heard, self.language, 0.9)

    async def synthesize(self, text, language):
        self.spoken.append((text, language))
        return Speech(b"wav-bytes", language, False)


def script():
    return ScriptedChat([
        Reply(text="नमस्ते! क्या मेरी बात Friends Provisions से हो रही है?"),
        Reply(tool_calls=[ToolCall("1", "confirm_identity", {"is_right_shop": True}),
                          ToolCall("2", "add_to_order", {"sku": "P001", "quantity": 15, "unit": "piece",
                                                         "retailer_said": "500 ml ke 15", "source": "retailer_request"})]),
        Reply(text="जी, 15 pieces जोड़ दिए। और कुछ?"),
        Reply(tool_calls=[ToolCall("3", "read_back_order", {})]),
        Reply(text="Parachute 500 ml के 15 pieces, कुल 2640 रुपये। Confirm कर दूँ?"),
        Reply(tool_calls=[ToolCall("4", "confirm_order", {"retailer_said": "haan confirm"})]),
        Reply(tool_calls=[ToolCall("5", "end_call", {"outcome": "order_placed"})], text="धन्यवाद!"),
        Reply(text=""),
    ])


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_file=str(DATA), runs_dir=str(tmp_path / "runs"))
    speech = FakeSpeech()
    app = create_app(settings, chat_factory=script, speech=speech, store=OutcomeStore(tmp_path / "runs"))
    with TestClient(app) as test_client:
        test_client.speech = speech
        yield test_client


def test_static_and_reference_endpoints(client):
    assert client.get("/").status_code == 200
    config = client.get("/api/config").json()
    assert config["voice"] == "sarvam" and config["as_of"] == "2026-11-20"
    retailers = client.get("/api/retailers").json()
    assert len(retailers) == 100 and next(r for r in retailers if r["id"] == "R001")["do_not_call"]
    plan = client.get("/api/plan").json()
    assert len(plan["calls"]) == 97 and len(plan["skipped"]) == 3
    assert client.get("/api/retailers/R003/brief").json()["name"] == "Friends Provisions"
    assert len(client.get("/api/stock/D001").json()) == 20
    assert any("do-not-call" in f["title"] for f in client.get("/api/audit").json())


def test_do_not_call_retailer_is_refused(client):
    response = client.post("/api/calls", json={"retailer_id": "R001"})
    assert response.status_code == 409


def test_full_call_over_text_and_audio(client):
    start = client.post("/api/calls", json={"retailer_id": "R003"}).json()
    call_id = start["call_id"]
    assert start["reply"]["language"] == "hi-IN"
    assert base64.b64decode(start["reply"]["audio"]) == b"wav-bytes"

    turn = client.post(f"/api/calls/{call_id}/say", json={"text": "haan ji, 500 ml ke 15"}).json()
    assert turn["state"]["order"]["lines"][0]["quantity"] == 15

    turn = client.post(f"/api/calls/{call_id}/audio",
                       files={"file": ("turn.wav", b"RIFF....", "audio/wav")}).json()
    assert turn["heard"]["text"] == "15 bhej do" and turn["state"]["order"]["state"] == "read_back"

    turn = client.post(f"/api/calls/{call_id}/say", json={"text": "haan confirm", "speak": False}).json()
    assert turn["reply"]["audio"] is None
    assert turn["state"]["ended"] and turn["state"]["outcome"] == "order_placed"

    outcomes = client.get("/api/outcomes").json()
    assert outcomes["orders"][0]["order_value_inr"] == 2640
    assert outcomes["calls"][0]["outcome"] == "order_placed"
    assert client.post(f"/api/calls/{call_id}/say", json={"text": "hello"}).status_code == 409


def test_mock_oms_is_idempotent(client):
    payload = {"order_id": "SO-1", "retailer_id": "R003", "distributor_id": "D001", "order_value_inr": 176,
               "lines": [{"sku": "P001", "quantity": 1, "unit_price_inr": 176}],
               "confirmation": {"status": "confirmed"}}
    first = client.post("/api/oms/orders", json=payload, headers={"Idempotency-Key": "k1"})
    second = client.post("/api/oms/orders", json=payload, headers={"Idempotency-Key": "k1"})
    assert first.status_code == 201 and second.status_code == 200 and second.json()["duplicate"]
    unconfirmed = {**payload, "confirmation": {"status": "pending"}}
    assert client.post("/api/oms/orders", json=unconfirmed, headers={"Idempotency-Key": "k2"}).status_code == 422
    assert len(client.get("/api/oms/orders").json()) == 1


def test_no_model_configured(tmp_path):
    settings = Settings(data_file=str(DATA), runs_dir=str(tmp_path / "runs"))
    with TestClient(create_app(settings, speech=None, store=OutcomeStore(None))) as client:
        assert client.get("/api/config").json()["llm"]["ready"] is False
        assert client.post("/api/calls", json={"retailer_id": "R003"}).status_code == 503


def test_health_and_timings(client):
    health = client.get("/healthz").json()
    assert health["status"] == "ok" and health["retailers"] == 100 and health["speech"] == "sarvam"
    start = client.post("/api/calls", json={"retailer_id": "R003"}).json()
    assert set(start["timing"]) == {"stt_ms", "model_ms", "tts_ms"}
    turn = client.post(f"/api/calls/{start['call_id']}/audio", files={"file": ("t.wav", b"RIFF", "audio/wav")}).json()
    agent_turns = [t for t in turn["state"]["transcript"] if t["speaker"] == "agent"]
    assert all("timing" in t for t in agent_turns)
    assert client.get("/healthz").json()["live_calls"] == 1


def test_turn_limits(client):
    call_id = client.post("/api/calls", json={"retailer_id": "R003"}).json()["call_id"]
    assert client.post(f"/api/calls/{call_id}/say", json={"text": "x" * 501}).status_code == 413
    big = b"0" * (5 * 1024 * 1024 + 1)
    assert client.post(f"/api/calls/{call_id}/audio", files={"file": ("t.wav", big, "audio/wav")}).status_code == 413
