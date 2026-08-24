"""Simple offline TTS via pyttsx3. Swap for edge-tts if you want a
noticeably better voice and don't mind requiring internet."""
import pyttsx3
import multiprocessing

_current_process = None

def _speak_worker(text: str):
    engine = pyttsx3.init()
    engine.setProperty("rate", 185)
    engine.say(text)
    engine.runAndWait()

def speak(text: str) -> None:
    global _current_process
    if _current_process is not None and _current_process.is_alive():
        _current_process.terminate()

    _current_process = multiprocessing.Process(target=_speak_worker, args=(text,))
    _current_process.start()
    _current_process.join()

def is_speaking() -> bool:
    return _current_process is not None and _current_process.is_alive()

def stop_speaking():
    global _current_process
    if _current_process is not None and _current_process.is_alive():
        _current_process.terminate()
