from __future__ import annotations

from .data import LANGUAGE_NAMES
from .languages import reply_language
from .llm import ChatModel, LLMError
from .prompts import CALL_CONNECTED, system_prompt
from .session import CallSession
from .tools import TOOLS

MAX_STEPS = 8


class SalesAgent:
    def __init__(self, session: CallSession, chat: ChatModel, max_steps: int = MAX_STEPS):
        self.session = session
        self.chat = chat
        self.max_steps = max_steps
        self.chat.reset(system_prompt(session), TOOLS)
        self.pending = False

    async def open(self) -> str:
        self.chat.add_user(CALL_CONNECTED)
        self.pending = True
        return await self._run()

    async def respond(self, text: str, heard_language: str = "", unclear: bool = False) -> str:
        if self.session.ended:
            return ""
        spoken = text.strip()
        self.session.add_turn("retailer", spoken or "[silence]", heard_language)
        notes = []
        if heard_language and heard_language != self.session.language:
            notes.append(f"speech detected as {LANGUAGE_NAMES.get(heard_language, heard_language)}")
        if unclear or not spoken:
            notes.append("unclear audio, transcript may be wrong")
        message = spoken or "..."
        if notes:
            message = f"{message} [{'; '.join(notes)}]"
        if unclear and "[unclear]" not in message:
            message = f"[unclear] {message}"
        self.chat.add_user(message)
        self.pending = True
        return await self._run()

    async def resume(self) -> str:
        if not self.pending:
            return ""
        return await self._run()

    async def _run(self) -> str:
        spoken: list[str] = []
        empty = 0
        for _ in range(self.max_steps):
            reply = await self.chat.step()
            if reply.text:
                spoken.append(reply.text)
            if not reply.tool_calls:
                if spoken or self.session.ended:
                    break
                empty += 1
                if empty > 1:
                    self.chat.add_user("[Phone system: the retailer is waiting. Say your next line.]")
                continue
            results = []
            for call in reply.tool_calls:
                results.append((call, self.session.call_tool(call.name, call.arguments)))
            self.chat.add_tool_results(results)
        else:
            if not spoken:
                self.pending = False
                raise LLMError("The model kept calling tools without answering.")
        self.pending = False
        text = spoken[-1] if spoken else ""
        if text:
            language = reply_language(text, self.session.language, self.session.retailer.language_code)
            self.session.add_turn("agent", text, language)
        return text
