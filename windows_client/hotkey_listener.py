"""
Push-to-talk loop:
  hold hotkey -> record mic -> transcribe (faster-whisper) ->
  POST to brain /intent -> execute locally -> speak confirmation.

Push-to-talk is far more reliable than always-on wake-word detection for
a v1 - add Porcupine wake-word support later once this works end to end.
"""
import io
import os
import wave
import time
import threading
import traceback
import pythoncom
import collections

# Suppress HuggingFace cache symlink warnings on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import keyboard  # global hotkey listener
import numpy as np
import requests
import sounddevice as sd
from faster_whisper import WhisperModel

from executor import execute, RESOLVE_CLARIFICATION
from tts import speak

BRAIN_URL = os.getenv("BRAIN_URL", "http://localhost:8008")
HOTKEY = os.getenv("ASSISTANT_HOTKEY", "ctrl+alt+space")
SAMPLE_RATE = 16000

print("Loading speech-to-text model (first run downloads it, one-time)...")
stt_model = WhisperModel("small.en", device="cpu", compute_type="int8")

_busy_lock = threading.Lock()
_buffer_lock = threading.Lock()
_is_recording = False
_last_print_time = 0
_pending_confirmation = None
_pending_clarification = None

CHUNK_SIZE = 1024
MAX_CHUNKS = int(SAMPLE_RATE * 1.5 / CHUNK_SIZE)
_audio_buffer = collections.deque(maxlen=MAX_CHUNKS)
_record_frames = []

def _audio_callback(indata, frames, time_info, status):
    if status:
        print(f"[audio status] {status}")
    with _buffer_lock:
        if _is_recording:
            _record_frames.append(indata.copy())
        else:
            _audio_buffer.append(indata.copy())

_global_stream = sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="int16",
    blocksize=CHUNK_SIZE,
    callback=_audio_callback
)
_global_stream.start()

def record_while_held(hotkey: str) -> np.ndarray:
    global _is_recording, _record_frames
    
    with _buffer_lock:
        print(f"[debug] pre-buffer len: {len(_audio_buffer)}, record_frames len: {len(_record_frames)}")
        _record_frames = list(_audio_buffer)
        _is_recording = True
        
    print(f"[{hotkey}] held - recording... release to stop.")
    
    try:
        while keyboard.is_pressed(hotkey.split("+")[-1]):
            time.sleep(0.05)
    finally:
        with _buffer_lock:
            _is_recording = False
            captured = _record_frames
            _record_frames = []
            _audio_buffer.clear()
            
    if not captured:
        return np.array([], dtype=np.int16)
    return np.concatenate(captured, axis=0)

def has_speech(audio, threshold=50.0):
    if audio.size == 0:
        return False
    rms = np.sqrt(np.mean(audio.astype(np.float64) ** 2))
    print(f"[debug] audio rms: {rms:.2f}")
    return rms > threshold


def transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    buf.seek(0)

    segments, _ = stt_model.transcribe(buf, language="en")
    return " ".join(seg.text for seg in segments).strip()


def _handle_result(result):
    global _pending_clarification, _pending_confirmation
    if isinstance(result, dict) and result.get("needs_clarification"):
        _pending_clarification = {
            "timestamp": time.time(),
            "action": result["action"],
            "contact": result["contact"]
        }
        msg = result["message"]
        print(msg)
        
        t0_speak = time.time()
        speak(msg)
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
    elif isinstance(result, dict) and result.get("needs_confirmation"):
        _pending_confirmation = {
            "timestamp": time.time(),
            "original_action": result["original_action"],
            "original_params": result["original_params"]
        }
        msg = f"Did you mean {result['suggested']}? Hold the hotkey and say yes or no."
        print(msg)
        
        t0_speak = time.time()
        speak(msg)
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
    elif isinstance(result, dict) and result.get("needs_followup"):
        msg = result["message"]
        print(msg)
        
        t0_speak = time.time()
        speak(msg)
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
    else:
        print(result)
        
        t0_speak = time.time()
        speak(result if isinstance(result, str) else str(result))
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")


def _handle_command_impl():
    global _pending_confirmation, _pending_clarification
    try:
        t0 = time.time()
        audio = record_while_held(HOTKEY)
        t_record = time.time()
        print(f"[timing] record_while_held: {t_record - t0:.2f}s")
        
        if not has_speech(audio):
            print("Audio too quiet, ignoring.")
            return

        text = transcribe(audio)
        t_transcribe = time.time()
        print(f"[timing] transcribe: {t_transcribe - t_record:.2f}s")
        
        if not text:
            print("Didn't catch anything.")
            return

        print(f"Heard: {text}")
        
        if _pending_clarification and (time.time() - _pending_clarification["timestamp"] < 30.0):
            t0_exec = time.time()
            resolver = RESOLVE_CLARIFICATION.get(_pending_clarification["action"])
            if resolver:
                result = resolver(_pending_clarification["contact"], text)
            else:
                intent = {"action": _pending_clarification["action"], "params": {"contact": _pending_clarification["contact"], "intent": text}}
                result = execute(intent)
            _pending_clarification = None
            t_exec = time.time()
            print(f"[timing] execute (clarified): {t_exec - t0_exec:.2f}s")
            
            _handle_result(result)
            return
        elif _pending_clarification:
            _pending_clarification = None

        if _pending_confirmation and (time.time() - _pending_confirmation["timestamp"] < 10.0):
            t_lower = text.lower()
            if any(w in t_lower for w in ["yes", "yeah", "yep", "correct"]):
                intent = {
                    "action": _pending_confirmation["original_action"],
                    "params": _pending_confirmation["original_params"]
                }
                _pending_confirmation = None
                
                t0_exec = time.time()
                result = execute(intent)
                t_exec = time.time()
                print(f"[timing] execute (confirmed): {t_exec - t0_exec:.2f}s")
                
                _handle_result(result)
                return
            elif any(w in t_lower for w in ["no", "nope", "cancel"]):
                _pending_confirmation = None
                print("Okay, cancelled.")
                
                t0_speak = time.time()
                speak("Okay, cancelled.")
                print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
                return
            else:
                _pending_confirmation = None

        try:
            t0_brain = time.time()
            resp = requests.post(f"{BRAIN_URL}/intent", json={"text": text}, timeout=25)
            resp.raise_for_status()
            intent = resp.json()
            t_brain = time.time()
            print(f"[timing] brain API: {t_brain - t0_brain:.2f}s")
        except requests.RequestException as e:
            speak("Sorry, I couldn't reach the brain service.")
            print(f"Error calling brain: {e}")
            return

        print(f"Intent: {intent}")
        
        t0_exec = time.time()
        result = execute(intent)
        t_exec = time.time()
        print(f"[timing] execute: {t_exec - t0_exec:.2f}s")
        
        _handle_result(result)
    except Exception as e:
        print(f"Unexpected error in _handle_command_impl: {e}")
        traceback.print_exc()


def handle_command():
    global _last_print_time
    if not _busy_lock.acquire(blocking=False):
        if _is_recording:
            # Silently ignore OS auto-repeat events while the user is intentionally holding the key
            return
            
        if time.time() - _last_print_time > 2.0:
            print("Still processing the previous command, ignoring this press.")
            _last_print_time = time.time()
        return
        
    def _run_and_release():
        pythoncom.CoInitialize()
        try:
            _handle_command_impl()
        finally:
            _busy_lock.release()
            pythoncom.CoUninitialize()
            
    threading.Thread(target=_run_and_release, daemon=True).start()


def main():
    print(f"Ready. Hold {HOTKEY} and speak a command. Ctrl+C to quit.")
    keyboard.add_hotkey(HOTKEY, handle_command, suppress=False, trigger_on_release=False)
    keyboard.wait()


if __name__ == "__main__":
    main()
