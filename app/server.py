from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import SalesAgent
from .audit import findings
from .config import ROOT, Settings
from .data import Dataset
from .inventory import StockLedger
from .languages import browser_language
from .llm import ChatModel, LLMError
from .oms import MockDistributorOMS, OrderGateway
from .outcomes import OutcomeStore
from .planner import build_brief, plan_day
from .session import CallBlocked, CallSession, Context
from .speech import SarvamSpeech, SpeechError

WEB = ROOT / "web"
MAX_SESSIONS = 50


class StartCall(BaseModel):
    retailer_id: str
    speak: bool = True


class Say(BaseModel):
    text: str
    language: str = ""
    unclear: bool = False
    speak: bool = True


@dataclass
class Live:
    session: CallSession
    agent: SalesAgent
    lock: asyncio.Lock


def create_app(settings: Settings | None = None, chat_factory: Callable[[], ChatModel] | None = None,
               speech: SarvamSpeech | None = None, store: OutcomeStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    dataset = Dataset.load(settings.data_file)
    store = store if store is not None else OutcomeStore(settings.runs_dir)
    ledger = StockLedger(dataset)
    gateway = OrderGateway(store, settings.oms_webhook_url)
    ctx = Context(dataset, ledger, store, gateway)
    oms = MockDistributorOMS()
    voice = speech if speech is not None else settings.speech()
    make_chat = chat_factory or settings.chat
    llm_ready = chat_factory is not None or settings.llm_ready
    calls: OrderedDict[str, Live] = OrderedDict()

    app = FastAPI(title="GT Voice Agent")
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    def live(call_id: str) -> Live:
        if call_id not in calls:
            raise HTTPException(404, "No such call.")
        return calls[call_id]

    async def speak(text: str, language: str, wants_audio: bool = True) -> dict:
        out = {"text": text, "language": language, "browser_language": browser_language(language),
               "audio": None, "mime": None, "tts_fallback": False, "tts_error": None}
        if voice and text and wants_audio:
            try:
                result = await voice.synthesize(text, language)
                out.update(audio=base64.b64encode(result.audio).decode(), mime=result.mime,
                           tts_fallback=result.fallback)
            except SpeechError as exc:
                out["tts_error"] = str(exc)
        return out

    async def turn(item: Live, text: str, language: str, unclear: bool, wants_audio: bool,
                   heard: dict | None = None) -> dict:
        async with item.lock:
            try:
                reply = await item.agent.respond(text, language, unclear)
            except LLMError as exc:
                raise HTTPException(502, f"Model error: {exc}") from exc
            await gateway.deliver_pending()
            return {
                "heard": heard,
                "reply": await speak(reply, item.session.language, wants_audio),
                "state": item.session.snapshot(),
            }

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/config")
    async def config():
        return {
            "llm": {"provider": settings.llm_provider, "model": settings.model_label, "ready": llm_ready},
            "voice": "sarvam" if voice else "browser",
            "as_of": dataset.as_of.isoformat(),
            "webhook": bool(settings.oms_webhook_url),
            "products": {sku: p.label for sku, p in dataset.products.items()},
        }

    @app.get("/api/retailers")
    async def retailers():
        opted = store.opted_out()
        rows = []
        for r in sorted(dataset.retailers.values(), key=lambda x: x.id):
            prefs = dataset.prefs(r.id)
            rows.append({"id": r.id, "name": r.name, "city": r.city, "language": r.language,
                         "language_code": r.language_code, "segment": r.segment, "distributor_id": r.distributor_id,
                         "window": prefs.window, "do_not_call": prefs.do_not_call or r.id in opted})
        return rows

    @app.get("/api/retailers/{retailer_id}/brief")
    async def brief(retailer_id: str):
        try:
            return build_brief(dataset, ledger, retailer_id.upper(), None, store.opted_out()).as_dict()
        except Exception as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/plan")
    async def plan():
        queue, skipped = plan_day(dataset, ledger, None, store.opted_out())
        callbacks = [t for t in store.list("tasks") if t["kind"] == "callback" and t["status"] == "open"]

        def row(b):
            return {"retailer_id": b.retailer.id, "name": b.retailer.name, "city": b.retailer.city,
                    "language": b.retailer.language, "segment": b.retailer.segment, "window": b.window,
                    "priority": b.priority, "expected_value": b.expected_value, "lead": [i.product for i in b.lead],
                    "cross_sell": b.cross_sell.product if b.cross_sell else None, "skip_reason": b.skip_reason}

        return {"date": dataset.as_of.isoformat(), "calls": [row(b) for b in queue],
                "skipped": [row(b) for b in skipped], "callbacks": callbacks,
                "expected_value": sum(b.expected_value for b in queue)}

    @app.get("/api/audit")
    async def audit():
        return findings(dataset)

    @app.get("/api/stock/{distributor_id}")
    async def stock(distributor_id: str):
        return ledger.snapshot(distributor_id.upper())

    @app.post("/api/calls")
    async def start_call(body: StartCall):
        if not llm_ready:
            raise HTTPException(503, "No language model configured. Add GEMINI_API_KEY (or OpenAI settings) to .env.")
        try:
            session = CallSession(ctx, body.retailer_id.upper())
        except CallBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(404, str(exc)) from exc
        try:
            agent = SalesAgent(session, make_chat())
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from exc
        item = Live(session, agent, asyncio.Lock())
        calls[session.id] = item
        while len(calls) > MAX_SESSIONS:
            _, old = calls.popitem(last=False)
            old.session.finish()
        async with item.lock:
            try:
                text = await agent.open()
            except LLMError as exc:
                raise HTTPException(502, f"Model error: {exc}") from exc
        reply = await speak(text, session.language, body.speak)
        return {"call_id": session.id, "reply": reply, "state": session.snapshot()}

    @app.post("/api/calls/{call_id}/say")
    async def say(call_id: str, body: Say):
        item = live(call_id)
        if item.session.ended:
            raise HTTPException(409, "This call has ended.")
        return await turn(item, body.text, body.language, body.unclear, body.speak)

    @app.post("/api/calls/{call_id}/audio")
    async def audio(call_id: str, file: UploadFile = File(...)):
        item = live(call_id)
        if item.session.ended:
            raise HTTPException(409, "This call has ended.")
        if not voice:
            raise HTTPException(400, "Server speech is off (no SARVAM_API_KEY); the browser should send text.")
        data = await file.read()
        if not data:
            raise HTTPException(400, "Empty audio.")
        try:
            heard = await voice.transcribe(data, file.filename or "turn.wav", file.content_type or "audio/wav")
        except SpeechError as exc:
            raise HTTPException(502, str(exc)) from exc
        unclear = not heard.text or (heard.confidence is not None and heard.confidence < 0.3)
        return await turn(item, heard.text, heard.language, unclear, True,
                          {"text": heard.text, "language": heard.language, "confidence": heard.confidence})

    @app.post("/api/calls/{call_id}/resume")
    async def resume(call_id: str):
        item = live(call_id)
        async with item.lock:
            try:
                text = await item.agent.resume()
            except LLMError as exc:
                raise HTTPException(502, f"Model error: {exc}") from exc
            await gateway.deliver_pending()
        return {"heard": None, "reply": await speak(text, item.session.language), "state": item.session.snapshot()}

    @app.post("/api/calls/{call_id}/hangup")
    async def hangup(call_id: str):
        item = live(call_id)
        async with item.lock:
            item.session.finish()
        return {"state": item.session.snapshot()}

    @app.get("/api/calls/{call_id}")
    async def get_call(call_id: str):
        return live(call_id).session.snapshot()

    @app.get("/api/outcomes")
    async def outcomes():
        return {
            "orders": store.list("orders")[::-1],
            "tasks": store.list("tasks")[::-1],
            "insights": store.list("insights")[::-1],
            "opt_outs": store.list("opt_outs")[::-1],
            "calls": [{k: v for k, v in c.items() if k != "transcript"} for c in store.list("calls")[::-1]],
            "deliveries": [{"order_id": d.order_id, "status": d.status, "attempts": d.attempts, "detail": d.detail}
                           for d in gateway.outbox],
        }

    @app.post("/api/oms/orders")
    async def oms_receive(request: Request, idempotency_key: str = Header(default="")):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "Body must be JSON.") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "Body must be a JSON object.")
        status, body = oms.accept(payload, idempotency_key)
        return JSONResponse(body, status_code=status)

    @app.get("/api/oms/orders")
    async def oms_list():
        return oms.received[::-1]

    return app
