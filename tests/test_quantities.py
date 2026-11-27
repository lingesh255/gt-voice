import pytest

from app.quantities import UnitError, is_explicit_yes, read_quantity, to_pieces

PACKS = {(500, "ml"), (200, "ml"), (1000, "ml"), (5000, "ml"), (500, "g"), (1000, "g"), (40, "g"), (100, "ml"),
         (50, "ml"), (250, "g"), (150, "ml"), (100, "g"), (340, "g")}


def counts(text, pack=(500, "ml"), last=None):
    return read_quantity(text, pack, PACKS, last).counts


@pytest.mark.parametrize("text,expected", [
    ("500 ml ke 15 de dijiye", [15]),
    ("15 pieces", [15]),
    ("pandrah bhej do", [15]),
    ("पंद्रह भेज दो", [15]),
    ("ek darjan", [12]),
    ("do darjan bhejo", [24]),
    ("aadha darjan", [6]),
    ("dedh darjan", [18]),
    ("dozen", [12]),
    ("10 nahi 15 kar do", [10, 15]),
    ("pathu venum", [10]),
    ("பத்து வேண்டும்", [10]),
    ("१५ भेज दो", [15]),
])
def test_counts(text, expected):
    assert counts(text) == expected


def test_kar_do_is_not_two():
    assert counts("15 kar do") == [15]


def test_pack_size_mentions_are_not_counts():
    assert counts("500 ml ke 15 aur 200 ml ke 10") == [15, 10]


def test_litres_convert_to_packs():
    assert counts("Saffola Gold 10 litre bhej dena", pack=(1000, "ml")) == [10]
    assert counts("Saffola Gold 10 litre bhej dena", pack=(5000, "ml")) == [2]
    assert read_quantity("10 litre", (1000, "ml"), PACKS).volume


def test_unconvertible_volume():
    assert read_quantity("750 ml de do", (500, "ml"), PACKS).unconvertible


def test_boxes_are_flagged():
    assert read_quantity("2 box bhej do", (500, "ml"), PACKS).box
    assert read_quantity("do peti", (500, "ml"), PACKS).box
    assert read_quantity("10 boxes please", (500, "ml"), PACKS).box


def test_vague_and_relative():
    reading = read_quantity("send a few more than last time", (500, "ml"), PACKS, 12)
    assert reading.vague and not reading.counts
    reading = read_quantity("thoda zyada bhejna", (500, "ml"), PACKS)
    assert reading.vague and not reading.counts


def test_same_as_last_time_uses_history():
    assert counts("same as last time", last=12) == [12]
    assert counts("pichhli baar jitna", last=7) == [7]


def test_unclear_marker():
    assert read_quantity("[unclear] parachute ...", (500, "ml"), PACKS).unclear


def test_time_words_are_not_quantities():
    assert counts("5 baje ke baad call karna") == []


def test_to_pieces():
    assert to_pieces(15, "piece", (500, "ml")) == 15
    assert to_pieces(2, "dozen", (500, "ml")) == 24
    assert to_pieces(10, "litre", (1000, "ml")) == 10
    assert to_pieces(10, "litre", (5000, "ml")) == 2
    assert to_pieces(2, "kg", (500, "g")) == 4
    with pytest.raises(UnitError):
        to_pieces(2, "box", (500, "ml"))
    with pytest.raises(UnitError):
        to_pieces(3, "litre", (5000, "ml"))
    with pytest.raises(UnitError):
        to_pieces(1, "kg", (500, "ml"))
    with pytest.raises(UnitError):
        to_pieces(0, "piece", (500, "ml"))
    with pytest.raises(UnitError):
        to_pieces(1, "bundle", (500, "ml"))


@pytest.mark.parametrize("text", ["haan", "Haan, confirm kar dijiye", "yes please", "ok done", "हाँ भेज दो",
                                  "சரி", "ha bhai kar do", "theek hai", "ho", "બરાબર"])
def test_explicit_yes(text):
    assert is_explicit_yes(text)


@pytest.mark.parametrize("text", ["nahi", "ruko, 500 ml 20 karo", "no wait", "हाँ, लेकिन नहीं", "", "hmm",
                                  "change the quantity", "theek nahi hai"])
def test_not_a_yes(text):
    assert not is_explicit_yes(text)
