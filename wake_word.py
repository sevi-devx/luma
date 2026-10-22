import difflib
import speech_recognition as sr
from audio_utils import DEBUG_AUDIO, save_debug_wav

recognizer = sr.Recognizer()

WAKE_WORDS = (
    "luma", "looma", "lumma", "loomah", "loma", "roma", 
    "lema", "lumo", "luba", "luna", "sevi", "lumaa"
)


def check_wake_word(pcm16: bytes) -> bool:
    """Speech recognition with fallback language options and fuzzy matching."""
    if len(pcm16) < 3200:  # Ignore clips under 100ms
        print("[Voice Detection]: Clip too short, ignored")
        return False

    if DEBUG_AUDIO:
        save_debug_wav(pcm16)

    try:
        audio = sr.AudioData(pcm16, 16000, 2)
        
        try:
            transcript = recognizer.recognize_google(audio, language="en-US").lower()
        except sr.UnknownValueError:
            transcript = recognizer.recognize_google(audio, language="fil-PH").lower()

        print(f"[Voice Detection]: Heard -> '{transcript}'")

        if any(w in transcript for w in WAKE_WORDS):
            return True

        words = transcript.split()
        for word in words:
            for wake_word in WAKE_WORDS:
                similarity = difflib.SequenceMatcher(None, word, wake_word).ratio()
                if similarity >= 0.70:
                    print(f"[Wake Word Fuzzy Match]: '{word}' ~ '{wake_word}' ({similarity:.2f})")
                    return True

        return False
    except sr.UnknownValueError:
        print("[Voice Detection]: Could not parse speech (Silence or ambient noise)")
        return False
    except Exception as e:
        print(f"[Wake word check error]: {e!r}")
        return False
