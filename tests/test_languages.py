import pytest

from app.languages import reply_language, tts_language


@pytest.mark.parametrize("text,current,expected", [
    ("नमस्ते! क्या मेरी बात Friends Provisions से हो रही है?", "hi-IN", "hi-IN"),
    ("तो order: Set Wet Deodorant 14, Saffola Oats 1 kg 8, Set Wet Hair Gel 20, Livon Serum 50 ml 6, कुल 7,208 रुपये। Confirm कर दूँ?",
     "hi-IN", "hi-IN"),
    ("Sure, 15 pieces of Parachute 500 ml. Anything else?", "hi-IN", "en-IN"),
    ("Namaste ji, aapka order kitna hai?", "hi-IN", "hi-IN"),
    ("நன்றி! Parachute 200 ml 10 pieces மட்டும் இருக்கு, அனுப்பட்டுமா?", "ta-IN", "ta-IN"),
    ("हो, Parachute 500 ml चे 18 पाठवतो. बरोबर?", "mr-IN", "mr-IN"),
    ("হয়, Livon Serum 100 ml 7 টা পঠিয়াম নেকি?", "as-IN", "as-IN"),
])
def test_reply_language(text, current, expected):
    assert reply_language(text, current, current) == expected


def test_tts_fallbacks():
    assert tts_language("hi-IN") == ("hi-IN", False)
    assert tts_language("as-IN") == ("bn-IN", True)
    assert tts_language("xx-IN") == ("en-IN", True)
