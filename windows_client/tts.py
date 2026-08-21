"""Simple offline TTS via pyttsx3. Swap for edge-tts if you want a
noticeably better voice and don't mind requiring internet."""
import pyttsx3

def speak(text: str) -> None:
    # Initialize locally so it works safely on background threads without hanging
    engine = pyttsx3.init()
    engine.setProperty("rate", 185)
    engine.say(text)
    engine.runAndWait()
