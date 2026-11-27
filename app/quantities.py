from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100, "half": 0.5,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "chhah": 6, "chah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "dus": 10, "gyarah": 11,
    "gyara": 11, "barah": 12, "bara": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "pandra": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "bis": 20, "pachees": 25,
    "pachchis": 25, "tees": 30, "chalis": 40, "chaalis": 40, "pachas": 50, "pachaas": 50,
    "sau": 100, "dedh": 1.5, "derh": 1.5, "dhai": 2.5, "dhaai": 2.5, "aadha": 0.5, "adha": 0.5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "पन्द्रह": 15, "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "पच्चीस": 25,
    "तीस": 30, "चालीस": 40, "पचास": 50, "सौ": 100, "डेढ़": 1.5, "ढाई": 2.5, "आधा": 0.5,
    "onnu": 1, "ondru": 1, "rendu": 2, "moonu": 3, "naalu": 4, "anju": 5, "ainthu": 5, "aaru": 6,
    "ezhu": 7, "ettu": 8, "ombodhu": 9, "onbathu": 9, "pathu": 10, "iruvathu": 20, "nooru": 100,
    "ஒன்று": 1, "ஒண்ணு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "நான்கு": 4, "நாலு": 4,
    "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    "பதினைந்து": 15, "இருபது": 20, "நூறு": 100,
}

VERBS_BEFORE_DO = {"kar", "de", "bhej", "rakh", "likh", "daal", "dal", "bata", "bol", "le", "ho", "chala", "laga",
                   "कर", "दे", "भेज", "रख", "लिख", "डाल", "बता", "बोल", "ले", "हो", "लगा"}

DOZEN_WORDS = {"dozen", "dozens", "darjan", "darjen", "दर्जन", "டஜன்"}
BOX_WORDS = {
    "box", "boxes", "carton", "cartons", "case", "cases", "crate", "crates", "peti", "petti", "pety",
    "dabba", "dabbe", "dibba", "dibbe", "outer", "outers", "पेटी", "डिब्बा", "डिब्बे", "बॉक्स", "कार्टन",
    "பெட்டி", "பாக்ஸ்",
}
PIECE_WORDS = {
    "piece", "pieces", "pc", "pcs", "nag", "nug", "unit", "units", "bottle", "bottles", "pack", "packs",
    "packet", "packets", "sachet", "sachets", "jar", "jars", "pouch", "pouches", "पीस", "नग", "बोतल",
    "पैकेट", "பீஸ்", "பாட்டில்",
}
MEASURE_WORDS = {
    "ml": ("ml", 1), "l": ("ml", 1000), "litre": ("ml", 1000), "litres": ("ml", 1000),
    "liter": ("ml", 1000), "liters": ("ml", 1000), "ltr": ("ml", 1000), "lit": ("ml", 1000),
    "लीटर": ("ml", 1000), "லிட்டர்": ("ml", 1000), "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1),
    "gram": ("g", 1), "grams": ("g", 1), "ग्राम": ("g", 1), "kg": ("g", 1000), "kilo": ("g", 1000),
    "kgs": ("g", 1000), "किलो": ("g", 1000), "கிலோ": ("g", 1000),
}
VAGUE_WORDS = {
    "few", "some", "more", "extra", "little", "bit", "several", "lots", "plenty", "couple",
    "thoda", "thode", "thodi", "kuch", "zyada", "jyada", "jada", "adhik", "kam", "bahut",
    "konjam", "koncham", "jasti", "थोड़ा", "थोड़े", "थोड़ी", "कुछ", "ज़्यादा", "ज्यादा", "अधिक", "कम",
    "बहुत", "கொஞ்சம்", "அதிகம்",
}
SAME_AS_LAST = (
    "same as last", "same as before", "same quantity", "last time jitna", "pichhli baar jitna",
    "pichli baar jitna", "pehle jitna", "pahle jitna", "utna hi", "utne hi", "पिछली बार जितना",
    "पहले जितना", "उतना ही", "उतने ही",
)
RELATIVE = (
    "than last", "than before", "last time se", "pichhli baar se", "pichli baar se", "pehle se",
    "pahle se", "पिछली बार से", "पहले से",
)
UNCLEAR_MARKERS = ("[unclear]", "[inaudible]", "(unclear)", "(inaudible)", "??")

SPLIT = re.compile(r"[\s,!?;:()\[\]{}\"'/\\\-–—।|+*=&]+")
NUMBER_TOKEN = re.compile(r"^\d+(?:\.\d+)?$")


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", (text or "").lower())
    text = re.sub(r"(\d)([^\d\s.,])", r"\1 \2", text)
    text = re.sub(r"([^\d\s.,])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    tokens = []
    for raw in SPLIT.split(text):
        token = raw.strip(".")
        if token:
            tokens.append(token)
    return tokens


def number_value(tokens: list[str], i: int) -> float | None:
    token = tokens[i]
    if NUMBER_TOKEN.match(token):
        try:
            return float(token)
        except ValueError:
            return None
    if token in ("do", "दो") and i > 0 and tokens[i - 1] in VERBS_BEFORE_DO:
        return None
    return NUMBER_WORDS.get(token)


def measure_of(token: str) -> tuple[str, int] | None:
    return MEASURE_WORDS.get(token)


@dataclass
class QuantityReading:
    counts: list[int] = field(default_factory=list)
    box: bool = False
    vague: bool = False
    vague_word: str = ""
    same_as_last: bool = False
    relative: bool = False
    unclear: bool = False
    unconvertible: bool = False
    volume: bool = False


def read_quantity(text: str, pack: tuple[int, str] | None = None,
                  catalog_packs: set[tuple[int, str]] | None = None,
                  last_qty: int | None = None) -> QuantityReading:
    reading = QuantityReading()
    lowered = (text or "").lower()
    reading.unclear = any(marker in lowered for marker in UNCLEAR_MARKERS)
    reading.same_as_last = any(phrase in lowered for phrase in SAME_AS_LAST)
    reading.relative = any(phrase in lowered for phrase in RELATIVE)
    catalog_packs = catalog_packs or set()

    tokens = tokenize(text)
    i = 0
    while i < len(tokens):
        value = number_value(tokens, i)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if value is not None:
            if nxt in DOZEN_WORDS:
                _add(reading.counts, value * 12)
                i += 2
                continue
            if nxt in BOX_WORDS:
                reading.box = True
                i += 2
                continue
            measure = measure_of(nxt)
            if measure:
                unit, factor = measure
                amount = round(value * factor)
                if (amount, unit) not in catalog_packs and pack and pack[1] == unit:
                    reading.volume = True
                    if amount % pack[0] == 0:
                        _add(reading.counts, amount // pack[0])
                    else:
                        reading.unconvertible = True
                i += 2
                continue
            if value >= 1 and nxt not in {"baar", "bar", "time", "times", "din", "days", "day", "baje", "bje"}:
                _add(reading.counts, value)
            i += 2 if nxt in PIECE_WORDS else 1
            continue
        token = tokens[i]
        if token in DOZEN_WORDS:
            _add(reading.counts, 12)
        elif token in BOX_WORDS:
            reading.box = True
        elif token in VAGUE_WORDS and not reading.vague:
            reading.vague = True
            reading.vague_word = token
        i += 1

    if reading.relative and not reading.same_as_last:
        reading.vague = True
        reading.vague_word = reading.vague_word or "relative to last time"
    if reading.same_as_last and last_qty and not reading.counts:
        reading.counts.append(last_qty)
    return reading


def _add(counts: list[int], value: float) -> None:
    if value <= 0 or abs(value - round(value)) > 1e-9:
        return
    number = int(round(value))
    if number not in counts:
        counts.append(number)


UNIT_ALIASES = {
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "unit": "piece", "units": "piece",
    "pack": "piece", "packs": "piece", "packet": "piece", "packets": "piece", "bottle": "piece",
    "bottles": "piece", "sachet": "piece", "sachets": "piece", "jar": "piece", "jars": "piece",
    "pouch": "piece", "pouches": "piece", "1-litre pack": "piece", "5-litre pack": "piece",
    "dozen": "dozen", "dozens": "dozen",
    "litre": "litre", "litres": "litre", "liter": "litre", "liters": "litre", "l": "litre", "ltr": "litre",
    "ml": "ml", "kg": "kg", "kgs": "kg", "kilo": "kg", "g": "g", "gm": "g", "gram": "g", "grams": "g",
    "box": "box", "boxes": "box", "carton": "box", "cartons": "box", "case": "box", "cases": "box",
    "crate": "box", "crates": "box", "peti": "box", "dabba": "box", "outer": "box",
}


class UnitError(ValueError):
    pass


def to_pieces(quantity: float, unit: str, pack: tuple[int, str]) -> int:
    kind = UNIT_ALIASES.get((unit or "piece").strip().lower())
    if kind is None:
        raise UnitError(f"Unknown unit {unit!r}. Use piece, dozen, litre, ml, kg or g.")
    if kind == "box":
        raise UnitError("Box/carton sizes are not in the product master, so boxes cannot be converted. "
                        "Ask the retailer how many pieces they want.")
    if quantity is None or quantity <= 0:
        raise UnitError("Quantity must be more than zero.")
    if kind == "piece":
        total = quantity
    elif kind == "dozen":
        total = quantity * 12
    else:
        measure, factor = {"litre": ("ml", 1000), "ml": ("ml", 1), "kg": ("g", 1000), "g": ("g", 1)}[kind]
        if measure != pack[1]:
            raise UnitError(f"This product is sold by {pack[1]}, not {unit}.")
        amount = quantity * factor
        if abs(amount / pack[0] - round(amount / pack[0])) > 1e-9:
            raise UnitError(f"{quantity:g} {unit} is not a whole number of {pack[0]} {pack[1]} packs.")
        total = amount / pack[0]
    if abs(total - round(total)) > 1e-9:
        raise UnitError("That works out to a part of a piece. Ask for a whole number of pieces.")
    return int(round(total))


def is_volume_unit(unit: str) -> bool:
    return UNIT_ALIASES.get((unit or "").strip().lower()) in {"litre", "ml", "kg", "g"}


AFFIRMATIVE = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "confirm", "confirmed", "done", "sure", "correct", "right",
    "haan", "han", "haa", "ha", "ji", "jee", "theek", "thik", "bilkul", "sahi", "pakka", "chalega",
    "हाँ", "हां", "हा", "जी", "ठीक", "बिल्कुल", "सही", "पक्का", "कन्फर्म", "ओके", "चलेगा",
    "sari", "seri", "aamaa", "aama", "ama", "சரி", "ஆமா", "ஆமாம்", "ஓகே",
    "avunu", "sare", "అవును", "సరే", "houdu", "sari", "ಹೌದು", "ಸರಿ", "athe", "sheri", "ശരി", "അതെ",
    "ho", "hoy", "हो", "होय", "હા", "હાં", "બરાબર", "হ্যাঁ", "হ্যা", "ঠিক", "আচ্ছা", "হয়", "ହଁ", "ଠିକ",
}
NEGATIVE = {
    "no", "nope", "not", "dont", "don't", "wait", "stop", "cancel", "change", "hold",
    "nahi", "nahin", "mat", "ruko", "ruk", "badlo", "galat",
    "नहीं", "नही", "मत", "रुको", "रुकिए", "गलत", "बदलो",
    "illa", "venda", "வேண்டாம்", "இல்லை", "வேணாம்", "కాదు", "వద్దు", "ಬೇಡ", "ಇಲ್ಲ", "വേണ്ട", "ഇല്ല",
    "नको", "नाही", "ના", "નહીં", "না", "ନା", "নহয়",
}


def is_explicit_yes(text: str) -> bool:
    tokens = tokenize(text)
    if not tokens or any(token in NEGATIVE for token in tokens):
        return False
    joined = " ".join(tokens)
    return any(token in AFFIRMATIVE for token in tokens) or "kar do" in joined or "kar dijiye" in joined
