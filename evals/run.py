from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from app.agent import SalesAgent
from app.config import Settings
from app.data import Dataset
from app.inventory import StockLedger
from app.llm import ChatModel, LLMError
from app.oms import OrderGateway
from app.outcomes import OutcomeStore
from app.prompts import SCRIPTS
from app.session import CallSession, Context

from .scenarios import CASES, Case

OUT = Path(__file__).resolve().parent / "out"

RETAILER_PROMPT = """You are playing a shopkeeper on a phone call to test a sales agent. Stay in character the whole time.
Shop: {name}, {city}. Speak {language} the way shopkeepers do on the phone: short, informal, mixing in English product names. Write {language} in {script}. Keep every reply under 15 words.
What you want on this call: {goal}
Rules:
- Say only the words the shopkeeper says. No narration, no quotes, no notes.
- Answer the agent's questions from the goal above. If something is not covered, answer briefly and plausibly without ordering extra items.
- Once the agent says goodbye or the call is clearly over, reply with exactly <hangup>."""

JUDGE_PROMPT = """You review phone calls made by an AI sales agent to small shops in India. Score this call from 1 to 5 on each of:
naturalness (sounds like a good human salesman, short turns), language (matches the shopkeeper's language and switches), respect (no pressure, accepts no), accuracy (no invented facts, order read back before confirming).
Expected behaviour for this scenario: {expected}
Reply with JSON only: {{"naturalness": n, "language": n, "respect": n, "accuracy": n, "comment": "one sentence"}}"""


def garble(text: str) -> str:
    words = text.split()
    kept = [w if i % 2 == 0 else "..." for i, w in enumerate(words)]
    return " ".join(kept[: max(2, len(kept) // 2)])


async def run_case(case: Case, settings: Settings, dataset: Dataset, judge: bool) -> dict:
    store = OutcomeStore(None)
    ctx = Context(dataset, StockLedger(dataset), store, OrderGateway(store))
    session = CallSession(ctx, case.retailer_id)
    agent = SalesAgent(session, settings.chat(retries=6))
    retailer: ChatModel = settings.chat(retries=6)
    r = session.retailer
    retailer.reset(RETAILER_PROMPT.format(name=r.name, city=r.city, language=r.language,
                                          script=SCRIPTS.get(r.language, "its usual script"), goal=case.goal), [])
    started = time.monotonic()
    error = ""
    try:
        line = await agent.open()
        retailer.add_user(f"The phone rings. You pick up and hear: {line}")
        for turn in range(case.max_turns):
            said = (await retailer.step()).text.strip()
            if not said or "<hangup>" in said.lower():
                break
            unclear = turn in case.unclear_turns
            line = await agent.respond(garble(said) if unclear else said, unclear=unclear)
            if session.ended:
                break
            retailer.add_user(line or "...")
    except LLMError as exc:
        error = str(exc)
    session.finish()
    checks = []
    for check in case.checks:
        ok, detail = check(session, ctx)
        checks.append({"ok": ok, "detail": detail})
    result = {
        "id": case.id,
        "title": case.title,
        "retailer": f"{r.id} {r.name} ({r.language})",
        "passed": not error and all(c["ok"] for c in checks),
        "checks": checks,
        "error": error,
        "seconds": round(time.monotonic() - started, 1),
        "agent_turns": sum(1 for t in session.transcript if t["speaker"] == "agent"),
        "guard_stops": sum(1 for e in session.events if e["kind"] == "guard"),
        "transcript": session.transcript,
        "tools": [{"name": e["name"], "args": e["args"], "ok": e["ok"], "error": e["error"]}
                  for e in session.events if e["kind"] == "tool"],
    }
    if judge and not error:
        result["judge"] = await grade(case, settings, dataset, session)
    return result


async def grade(case: Case, settings: Settings, dataset: Dataset, session: CallSession) -> dict | None:
    expected = next((s.expected for s in dataset.scenarios if s.id == case.id), case.goal)
    chat = settings.chat(retries=6)
    chat.reset(JUDGE_PROMPT.format(expected=expected), [])
    chat.add_user("\n".join(f"{t['speaker']}: {t['text']}" for t in session.transcript))
    try:
        text = (await chat.step()).text
    except LLMError:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None


def report(results: list[dict], folder: Path, settings: Settings) -> str:
    passed = sum(r["passed"] for r in results)
    lines = [
        "# Conversation evals",
        "",
        f"{datetime.now():%d %b %Y %H:%M} · model `{settings.model_label}` · {passed}/{len(results)} passed",
        "",
        "| Case | Scenario | Retailer | Result | Checks | Turns | Guard stops |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        detail = "; ".join(c["detail"] for c in r["checks"]) if not r["error"] else f"error: {r['error'][:80]}"
        lines.append(f"| {r['id']} | {r['title']} | {r['retailer']} | {'pass' if r['passed'] else 'FAIL'} | "
                     f"{detail} | {r['agent_turns']} | {r['guard_stops']} |")
    judged = [r["judge"] for r in results if r.get("judge")]
    if judged:
        lines += ["", "Average judge scores (1 to 5):", ""]
        for key in ("naturalness", "language", "respect", "accuracy"):
            values = [j[key] for j in judged if isinstance(j.get(key), (int, float))]
            if values:
                lines.append(f"- {key}: {sum(values) / len(values):.1f}")
    lines += ["", f"Transcripts: `{folder.relative_to(Path.cwd()) if folder.is_relative_to(Path.cwd()) else folder}`"]
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run conversation evals against the live model")
    parser.add_argument("--only", help="Comma separated case ids, e.g. CS01,CS07")
    parser.add_argument("--tag", help="Run only cases with this tag, e.g. surprise or language")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--judge", action="store_true", help="Also score each call with the model as a judge")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.llm_ready:
        raise SystemExit("No model configured. Set GEMINI_API_KEY (or OpenAI settings) in .env first.")
    dataset = Dataset.load(settings.data_file)
    cases = CASES
    if args.only:
        wanted = {c.strip().upper() for c in args.only.split(",")}
        cases = [c for c in cases if c.id.upper() in wanted]
    if args.tag:
        cases = [c for c in cases if args.tag in c.tags]

    folder = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        for attempt in range(args.repeat):
            result = await run_case(case, settings, dataset, args.judge)
            result["attempt"] = attempt + 1
            results.append(result)
            (folder / f"{case.id}-{attempt + 1}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            mark = "pass" if result["passed"] else "FAIL"
            print(f"{case.id:6} {mark:4}  {case.title}  ({result['seconds']}s)")
            if not result["passed"]:
                for check in result["checks"]:
                    if not check["ok"]:
                        print(f"         - {check['detail']}")
                if result["error"]:
                    print(f"         - {result['error'][:200]}")

    text = report(results, folder, settings)
    (folder / "report.md").write_text(text, encoding="utf-8")
    (Path(__file__).resolve().parent / "report.md").write_text(text, encoding="utf-8")
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} passed. Report: evals/report.md")


if __name__ == "__main__":
    asyncio.run(main())
