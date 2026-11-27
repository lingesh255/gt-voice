from __future__ import annotations

SCRIPT_RANGES = (
    (0x0900, 0x097F, "deva"),
    (0x0980, 0x09FF, "beng"),
    (0x0A00, 0x0A7F, "pa-IN"),
    (0x0A80, 0x0AFF, "gu-IN"),
    (0x0B00, 0x0B7F, "od-IN"),
    (0x0B80, 0x0BFF, "ta-IN"),
    (0x0C00, 0x0C7F, "te-IN"),
    (0x0C80, 0x0CFF, "kn-IN"),
    (0x0D00, 0x0D7F, "ml-IN"),
)

HINGLISH = {"hai", "hain", "ka", "ke", "ki", "ko", "aap", "aapka", "aapke", "kya", "nahi", "haan", "ji", "main",
            "hum", "kitna", "kitne", "bhej", "dijiye", "karna", "kar", "bhai", "theek", "accha", "achha", "liye"}

TTS_LANGUAGES = {"hi-IN", "bn-IN", "ta-IN", "te-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "od-IN", "en-IN"}
TTS_FALLBACK = {"as-IN": "bn-IN"}

BROWSER_CODES = {"od-IN": "or-IN"}


def script_counts(text: str) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    latin = 0
    for char in text:
        code = ord(char)
        if char.isascii():
            if char.isalpha():
                latin += 1
            continue
        for low, high, name in SCRIPT_RANGES:
            if low <= code <= high:
                counts[name] = counts.get(name, 0) + 1
                break
    return counts, latin


def reply_language(text: str, current: str, home: str) -> str:
    counts, latin = script_counts(text)
    if counts:
        script, native = max(counts.items(), key=lambda kv: kv[1])
        if native >= 8 or native >= max(3, 0.2 * (native + latin)):
            if script == "deva":
                return "mr-IN" if "mr-IN" in (current, home) else "hi-IN"
            if script == "beng":
                return "as-IN" if "as-IN" in (current, home) else "bn-IN"
            return script
    words = {w.strip(".,!?").lower() for w in text.split()}
    if len(words & HINGLISH) >= 2:
        return "hi-IN"
    return "en-IN"


def tts_language(code: str) -> tuple[str, bool]:
    if code in TTS_LANGUAGES:
        return code, False
    if code in TTS_FALLBACK:
        return TTS_FALLBACK[code], True
    return "en-IN", True


def browser_language(code: str) -> str:
    return BROWSER_CODES.get(code, code)
