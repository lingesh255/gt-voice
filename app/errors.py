from __future__ import annotations


def explain_model_error(error: Exception, setting: str = "GEMINI_API_KEY") -> str:
    text = str(error)
    lowered = text.lower()
    if "api key not valid" in lowered or "api_key_invalid" in lowered or " 401" in text or " 403" in text:
        return f"The model provider rejected the API key. Check {setting} in .env."
    if " 429" in text or "quota" in lowered or "resource_exhausted" in lowered:
        return "The model's rate limit or quota is used up. Wait a minute and try again, or use a paid key."
    if " 404" in text:
        return "The model name was not found. Check GEMINI_MODEL in .env (gemini-flash-latest works)."
    if "could not reach" in lowered or "timed out" in lowered:
        return "Can't reach the model API. Check the internet connection or proxy."
    if "kept calling tools" in lowered:
        return "The model got stuck calling tools. Press Retry, or type the retailer's last line again."
    return f"Model error: {text[:300]}"


def explain_speech_error(error: Exception) -> str:
    text = str(error)
    if " 401" in text or " 403" in text:
        return "Sarvam rejected the API key. Check SARVAM_API_KEY in .env."
    if " 429" in text:
        return "Sarvam's rate limit or credit is used up. Check the Sarvam dashboard."
    if "request failed" in text.lower():
        return "Can't reach Sarvam. Check the internet connection."
    return f"Speech error: {text[:300]}"
