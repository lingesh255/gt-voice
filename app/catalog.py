from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .data import Dataset, Product
from .quantities import measure_of, number_value, tokenize

SCRIPT_ALIASES = {
    "पैराशूट": "parachute", "पेराशूट": "parachute", "पैराशुट": "parachute", "सफोला": "saffola",
    "सफ़ोला": "saffola", "लिवॉन": "livon", "लिवोन": "livon", "लिवान": "livon", "सेट वेट": "set wet",
    "सेटवेट": "set wet", "निहार": "nihar", "नारियल": "coconut", "तेल": "oil", "शहद": "honey",
    "ओट्स": "oats", "गोल्ड": "gold", "एक्टिव": "active", "जेल": "gel", "सीरम": "serum", "क्रीम": "cream",
    "डियो": "deodorant", "पीनट": "peanut", "बटर": "butter", "मसाला": "masala", "चमेली": "jasmine",
    "एलोवेरा": "aloe", "बड़ा": "big", "बड़ी": "big", "छोटा": "small", "छोटी": "small",
    "பாராசூட்": "parachute", "பாராசூட்டு": "parachute", "சஃபோலா": "saffola", "சபோலா": "saffola",
    "லிவான்": "livon", "லிவோன்": "livon", "நிஹார்": "nihar", "தேங்காய்": "coconut", "எண்ணெய்": "oil",
    "தேன்": "honey", "ஓட்ஸ்": "oats", "கோல்டு": "gold", "சின்ன": "small", "பெரிய": "big",
}

WORD_ALIASES = {
    "nariyal": "coconut", "tel": "oil", "chameli": "jasmine", "shahad": "honey", "shehad": "honey",
    "deo": "deodorant", "deodrant": "deodorant", "jel": "gel", "advanced": "advansed", "aloevera": "aloe",
    "setwet": "set wet", "parashoot": "parachute", "parashute": "parachute", "safola": "saffola",
    "chhota": "small", "chota": "small", "chhoti": "small", "choti": "small", "chinna": "small",
    "bada": "big", "badi": "big", "large": "big", "bigger": "big", "smaller": "small", "oat": "oats",
    "creme": "cream",
}

BRANDS = {"parachute": "Parachute", "saffola": "Saffola", "livon": "Livon", "set wet": "Set Wet", "nihar": "Nihar"}


@dataclass(frozen=True)
class Feature:
    words: tuple[str, ...]
    needle: str
    field: str = "name"

    def matches(self, product: Product) -> bool:
        haystack = product.name if self.field == "name" else product.category
        return self.needle in haystack.lower()


FEATURES = (
    Feature(("coconut",), "coconut oil"),
    Feature(("jasmine",), "jasmine"),
    Feature(("aloe",), "aloe"),
    Feature(("advansed",), "advansed"),
    Feature(("gold",), "gold"),
    Feature(("active",), "active"),
    Feature(("oats",), "oats"),
    Feature(("masala",), "masala"),
    Feature(("honey",), "honey"),
    Feature(("peanut", "butter"), "peanut butter"),
    Feature(("serum",), "serum"),
    Feature(("gel",), "gel"),
    Feature(("deodorant",), "deodorant"),
    Feature(("cream",), "cream"),
    Feature(("naturals", "natural"), "naturals"),
    Feature(("oil",), "oil", field="category"),
)


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text or "")
    for native, latin in sorted(SCRIPT_ALIASES.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(native, f" {latin} ")
    tokens = [WORD_ALIASES.get(t, t) for t in tokenize(text)]
    joined = " ".join(tokens).replace("set wet", "setwet")
    return [("set wet" if t == "setwet" else t) for t in joined.split()]


@dataclass
class SearchResult:
    matches: list[Product]
    ambiguous: bool
    note: str = ""


SKU_PATTERN = re.compile(r"\bP\d{3}\b", re.IGNORECASE)
BOTTLE_WORDS = {"bottle", "bottles", "botal", "बोतल", "பாட்டில்"}


def search(dataset: Dataset, query: str) -> SearchResult:
    skus = [m.upper() for m in SKU_PATTERN.findall(query or "") if m.upper() in dataset.products]
    if skus:
        found = [dataset.products[s] for s in dict.fromkeys(skus)]
        return SearchResult(found, len(found) > 1, "" if len(found) == 1 else "More than one SKU given.")
    tokens = normalize(query)
    words = set(tokens)
    brands = {BRANDS[t] for t in tokens if t in BRANDS}
    features = [f for f in FEATURES if any(w in words for w in f.words)]
    size_word = bool(words & {"small", "big"})

    sizes: list[tuple[int, str]] = []
    bare: list[int] = []
    for i, token in enumerate(tokens):
        value = number_value(tokens, i)
        if value is None:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        measure = measure_of(nxt)
        if measure:
            sizes.append((round(value * measure[1]), measure[0]))
        elif value == int(value):
            bare.append(int(value))

    candidates = list(dataset.products.values())
    if brands:
        candidates = [p for p in candidates if p.brand in brands]
    for feature in features:
        narrowed = [p for p in candidates if feature.matches(p)]
        if narrowed:
            candidates = narrowed
    if words & BOTTLE_WORDS:
        candidates = [p for p in candidates if p.pack_measure == "ml"] or candidates

    note = ""
    if sizes:
        sized = [p for p in candidates if (p.pack_amount, p.pack_measure) in sizes]
        if sized:
            candidates = sized
        else:
            amount, measure = sizes[0]
            shown = f"{amount / 1000:g} {'litre' if measure == 'ml' else 'kg'}" if amount >= 1000 else f"{amount} {measure}"
            note = (f"No pack of {shown} exists, so that is probably a total quantity, not a pack size. "
                    f"Ask which pack they want.")
    elif bare:
        sized = [p for p in candidates if p.pack_amount in bare]
        if sized:
            candidates = sized

    if not brands and not features and not sizes and not bare:
        return SearchResult([], False, "No product recognised. Ask the retailer to name the brand and product.")

    candidates.sort(key=lambda p: p.sku)
    ambiguous = len(candidates) > 1 or (size_word and not sizes)
    if size_word and not sizes:
        note = (note + " " if note else "") + "'Small'/'big' is not a pack size. Confirm the exact pack."
    elif len(candidates) > 1 and not note:
        note = "More than one product matches. Ask which one before adding."
    return SearchResult(candidates, ambiguous, note.strip())
