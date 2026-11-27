from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import dataclass, field

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _clean_schema(schema: dict) -> dict:
    allowed = {"type", "description", "enum", "properties", "required", "items"}
    cleaned = {k: v for k, v in schema.items() if k in allowed}
    if "properties" in cleaned:
        cleaned["properties"] = {name: _clean_schema(prop) for name, prop in cleaned["properties"].items()}
    if "items" in cleaned:
        cleaned["items"] = _clean_schema(cleaned["items"])
    return cleaned


class ChatModel:
    name = "base"

    def reset(self, system: str, tools: list[dict]) -> None:
        raise NotImplementedError

    def add_user(self, text: str) -> None:
        raise NotImplementedError

    async def step(self) -> Reply:
        raise NotImplementedError

    def add_tool_results(self, results: list[tuple[ToolCall, dict]]) -> None:
        raise NotImplementedError


async def _post(client: httpx.AsyncClient, url: str, payload: dict, headers: dict, retries: int = 2) -> dict:
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == retries:
                raise LLMError(f"Could not reach the model API: {exc}") from exc
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise LLMError(f"Model API returned {response.status_code}: {response.text[:400]}")
        await asyncio.sleep(delay)
        delay *= 2
    raise LLMError("Model API did not respond.")


class GeminiChat(ChatModel):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-flash-latest", thinking_level: str = "low",
                 base_url: str = GEMINI_URL, timeout: float = 30.0, client: httpx.AsyncClient | None = None,
                 retries: int = 2):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set.")
        self.api_key = api_key
        self.model = model
        self.thinking_level = thinking_level.strip().lower()
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.retries = retries
        self.system = ""
        self.declarations: list[dict] = []
        self.contents: list[dict] = []

    def reset(self, system: str, tools: list[dict]) -> None:
        self.system = system
        self.contents = []
        self.declarations = []
        for tool in tools:
            declaration = {"name": tool["name"], "description": tool["description"]}
            if tool.get("parameters", {}).get("properties"):
                declaration["parameters"] = _clean_schema(tool["parameters"])
            self.declarations.append(declaration)

    def add_user(self, text: str) -> None:
        self.contents.append({"role": "user", "parts": [{"text": text}]})

    def _payload(self) -> dict:
        payload = {"systemInstruction": {"parts": [{"text": self.system}]}, "contents": self.contents}
        if self.declarations:
            payload["tools"] = [{"functionDeclarations": self.declarations}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        if self.thinking_level and self.thinking_level != "off":
            payload["generationConfig"] = {"thinkingConfig": {"thinkingLevel": self.thinking_level}}
        return payload

    async def step(self) -> Reply:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        try:
            data = await _post(self.client, url, self._payload(), headers, self.retries)
        except LLMError as exc:
            if self.thinking_level and "thinking" in str(exc).lower():
                self.thinking_level = ""
                data = await _post(self.client, url, self._payload(), headers, self.retries)
            else:
                raise
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise LLMError(f"Model returned no answer: {json.dumps(feedback)[:300]}")
        content = candidates[0].get("content") or {}
        if not content.get("parts"):
            return Reply()
        content["role"] = "model"
        self.contents.append(content)
        reply = Reply()
        texts = []
        for part in content["parts"]:
            if "functionCall" in part:
                call = part["functionCall"]
                reply.tool_calls.append(ToolCall(call.get("id") or "", call.get("name", ""), call.get("args") or {}))
            elif part.get("text") and not part.get("thought"):
                texts.append(part["text"])
        reply.text = "".join(texts).strip()
        return reply

    def add_tool_results(self, results: list[tuple[ToolCall, dict]]) -> None:
        parts = []
        for call, result in results:
            response = {"name": call.name, "response": result}
            if call.id:
                response["id"] = call.id
            parts.append({"functionResponse": response})
        self.contents.append({"role": "user", "parts": parts})


class OpenAIChat(ChatModel):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1",
                 timeout: float = 30.0, temperature: float | None = None, client: httpx.AsyncClient | None = None,
                 retries: int = 2):
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set.")
        if not model:
            raise LLMError("OPENAI_MODEL is not set.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.retries = retries
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.messages: list[dict] = []
        self.tools: list[dict] = []

    def reset(self, system: str, tools: list[dict]) -> None:
        self.messages = [{"role": "system", "content": system}]
        self.tools = []
        for tool in tools:
            function = {"name": tool["name"], "description": tool["description"]}
            function["parameters"] = _clean_schema(tool.get("parameters") or {"type": "object", "properties": {}})
            self.tools.append({"type": "function", "function": function})

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    async def step(self) -> Reply:
        payload = {"model": self.model, "messages": self.messages}
        if self.tools:
            payload["tools"] = self.tools
            payload["tool_choice"] = "auto"
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = await _post(self.client, f"{self.base_url}/chat/completions", payload, headers, self.retries)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"Model returned no choices: {json.dumps(data)[:300]}")
        message = {k: v for k, v in (choices[0].get("message") or {}).items() if v is not None}
        message["role"] = "assistant"
        self.messages.append(message)
        reply = Reply(text=(message.get("content") or "").strip() if isinstance(message.get("content"), str) else "")
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": function.get("arguments")}
            if not isinstance(arguments, dict):
                arguments = {"_raw": arguments}
            reply.tool_calls.append(ToolCall(call.get("id") or uuid.uuid4().hex[:12], function.get("name", ""), arguments))
        return reply

    def add_tool_results(self, results: list[tuple[ToolCall, dict]]) -> None:
        for call, result in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})


class ScriptedChat(ChatModel):
    name = "scripted"

    def __init__(self, replies: list[Reply]):
        self.replies = list(replies)
        self.system = ""
        self.tools: list[dict] = []
        self.inbox: list[str] = []
        self.results: list[list[tuple[ToolCall, dict]]] = []

    def reset(self, system: str, tools: list[dict]) -> None:
        self.system = system
        self.tools = copy.deepcopy(tools)

    def add_user(self, text: str) -> None:
        self.inbox.append(text)

    async def step(self) -> Reply:
        if not self.replies:
            raise LLMError("Script ran out of replies.")
        return self.replies.pop(0)

    def add_tool_results(self, results: list[tuple[ToolCall, dict]]) -> None:
        self.results.append(results)
