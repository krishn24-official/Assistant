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
import openwakeword
from openwakeword.model import Model

from executor import execute, RESOLVE_CLARIFICATION
from tts import speak
import tts

BRAIN_URL = os.getenv("BRAIN_URL", "http://localhost:8008")
HOTKEY = os.getenv("ASSISTANT_HOTKEY", "ctrl+alt+space")
SAMPLE_RATE = 16000

stt_model = None
oww_model = None
_global_stream = None

def _init_models_and_stream():
    global stt_model, oww_model, _global_stream
    
    print("Loading speech-to-text model (first run downloads it, one-time)...")
    stt_model = WhisperModel("small.en", device="cpu", compute_type="int8")

    print("Checking/Downloading wake-word model...")
    openwakeword.utils.download_models()
    oww_model = Model(wakeword_models=["hey_jarvis"])
    
    _global_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=CHUNK_SIZE,
        callback=_audio_callback
    )
    _global_stream.start()

_busy_lock = threading.Lock()
_buffer_lock = threading.Lock()
_is_recording = False
_last_print_time = 0
_pending_confirmation = None
_pending_clarification = None
_last_wake_word_time = 0

CHUNK_SIZE = 1280
MAX_CHUNKS = int(SAMPLE_RATE * 1.5 / CHUNK_SIZE)
_audio_buffer = collections.deque(maxlen=MAX_CHUNKS)
_record_frames = []

def _audio_callback(indata, frames, time_info, status):
    global _last_wake_word_time
    if status:
        print(f"[audio status] {status}")
        
    if oww_model is not None and not _is_recording and not _busy_lock.locked():
        if tts.is_speaking():
            pass  # Suppress inference so self-audio isn't picked up
        else:
            prediction = oww_model.predict(indata[:, 0])
            score = prediction.get("hey_jarvis", 0.0)
            
            if score > 0.5:
                now = time.time()
                if now - _last_wake_word_time > 2.0:
                    print(f"\n[wake word] Hey Jarvis detected (score: {score:.3f})")
                    _last_wake_word_time = now
                    if _busy_lock.acquire(blocking=False):
                        threading.Thread(target=_handle_wake_word_command, daemon=True).start()

    with _buffer_lock:
        if _is_recording:
            _record_frames.append(indata.copy())
        else:
            _audio_buffer.append(indata.copy())



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


def _record_until_silence(max_duration=12.0, silence_duration=1.5, threshold=50.0, include_prebuffer=False) -> np.ndarray:
    global _is_recording, _record_frames
    
    with _buffer_lock:
        if include_prebuffer:
            _record_frames = list(_audio_buffer)
        else:
            _record_frames = []
        _is_recording = True
        
    print(f"[wake word] recording... (stop on silence)")
    
    t_start = time.time()
    last_speech_time = t_start
    
    try:
        while True:
            time.sleep(0.1)
            now = time.time()
            if now - t_start > max_duration:
                print("[wake word] max duration reached")
                break
                
            with _buffer_lock:
                recent_frames_count = int(SAMPLE_RATE * 0.5 / CHUNK_SIZE)
                recent_frames = _record_frames[-recent_frames_count:]
                
            if recent_frames:
                recent_audio = np.concatenate(recent_frames, axis=0)
                if has_speech(recent_audio, threshold):
                    last_speech_time = now
            
            if now - last_speech_time > silence_duration:
                print("[wake word] silence detected")
                break
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
            "contact": result.get("contact"),
            "pending": result.get("pending")
        }
        msg = result["message"]
        print(msg)
        
        t0_speak = time.time()
        speak(msg)
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
    elif isinstance(result, dict) and result.get("needs_confirmation"):
        _pending_confirmation = result
        print(f"[debug] _pending_confirmation SET to {result.get('kind')} in _handle_result")
        _pending_confirmation["timestamp"] = time.time()
        
        if result.get("kind") == "app_suggestion":
            msg = f"Did you mean {result['suggested']}? Hold the hotkey and say yes or no."
        else:
            msg = result.get("message", "Say yes to confirm or no to cancel.")
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
    elif isinstance(result, dict) and result.get("needs_mail_walkthrough"):
        from executor import MAIL_WALKTHROUGH
        res = MAIL_WALKTHROUGH["announce"](result["index"])
        _handle_result(res)
        return
    else:
        print(result)
        
        t0_speak = time.time()
        speak(result if isinstance(result, str) else str(result))
        print(f"[timing] speak: {time.time() - t0_speak:.2f}s")


def _process_audio(audio: np.ndarray, t_record: float):
    global _pending_confirmation, _pending_clarification
    if not has_speech(audio):
        print("Audio too quiet, ignoring.")
        return

    text = transcribe(audio)
    t_transcribe = time.time()
    print(f"[timing] transcribe: {t_transcribe - t_record:.2f}s")
    
    if not text:
        print("Didn't catch anything.")
        return

    import re
    # Strip wake word and common phonetic hallucinations from Faster Whisper (e.g. jargis, hrv's, a jar is)
    wake_pattern = r'^(?:hey|hi|hello|okay|ok)?\W*(?:jarvis|jervis|jargis|jarveez|hrv\'?s|hrb\'?s|harvis|travis|a jar is)\b\W*'
    text = re.sub(wake_pattern, '', text, flags=re.IGNORECASE).strip()
    
    if not text:
        print("Heard only wake word, ignoring.")
        return

    print(f"Heard: {text}")

    if _pending_clarification and (time.time() - _pending_clarification["timestamp"] < 30.0):
        t0_exec = time.time()
        resolver = RESOLVE_CLARIFICATION.get(_pending_clarification["action"])
        if resolver:
            if _pending_clarification["action"] == "email_address_retry":
                result = resolver(text, _pending_clarification.get("pending"))
            else:
                result = resolver(_pending_clarification.get("contact"), text)
        else:
            intent = {"action": _pending_clarification["action"], "params": {"contact": _pending_clarification["contact"], "intent": text}}
            result = execute(intent)
        _pending_clarification = None
        t_exec = time.time()
        print(f"[timing] execute (clarified): {t_exec - t0_exec:.2f}s")
        
        print(f"[debug] clarification resolver returned: {result}")
        _handle_result(result)
        return
    elif _pending_clarification:
        print("[debug] _pending_clarification EXPIRED, clearing it.")
        _pending_clarification = None

    timeout = 15.0
    if _pending_confirmation:
        if _pending_confirmation.get("kind") == "send_email":
            timeout = 25.0
        elif _pending_confirmation.get("kind") == "mail_walkthrough":
            timeout = 45.0
            
    print(f"[debug] _pending_confirmation CHECKED in _process_audio: is_set={bool(_pending_confirmation)}")
    
    if _pending_confirmation and (time.time() - _pending_confirmation["timestamp"] >= timeout):
        print(f"[debug] _pending_confirmation EXPIRED (timeout={timeout}s), clearing it.")
        _pending_confirmation = None

    if _pending_confirmation:
        print(f"[debug] _pending_confirmation ACTIVE and unexpired (kind: {_pending_confirmation.get('kind')})")
        t_lower = text.lower()
        kind = _pending_confirmation.get("kind")
        is_short = len(t_lower.split()) <= 5
        
        if kind == "mail_walkthrough":
            index = _pending_confirmation["index"]
            
            norm_text = text.lower().strip().rstrip(".!?")
            words = norm_text.split()
            
            is_cancel = len(words) <= 5 and (norm_text in ["cancel", "stop", "exit", "quit", "done", "that's it", "nothing", "nevermind", "never mind"] or any(w in words for w in ["cancel", "stop", "exit", "quit"]))
            is_skip = len(words) <= 5 and (norm_text in ["next", "skip", "no", "no thanks", "nope"] or any(w in words for w in ["next", "skip", "no", "nope"]))
            
            if is_cancel or is_skip:
                _pending_confirmation = None
                print(f"[debug] _pending_confirmation CLEARED in mail_walkthrough {'cancel' if is_cancel else 'skip'}")
                import executor
                next_index = index + 1
                if next_index < len(executor._last_unread_emails):
                    res = executor.MAIL_WALKTHROUGH["announce"](next_index)
                    _handle_result(res)
                else:
                    msg = "That's all your unread emails."
                    print(msg)
                    t0_speak = time.time()
                    speak(msg)
                    print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
                return
            else:
                t0_exec = time.time()
                import re
                instruction = re.sub(r'^(reply|saying|yes)\b[\s,.]*', '', text, flags=re.IGNORECASE).strip()
                
                import executor
                res = executor.MAIL_WALKTHROUGH["reply"](index, instruction)
                _pending_confirmation = None
                print("[debug] _pending_confirmation CLEARED in mail_walkthrough reply")
                t_exec = time.time()
                print(f"[timing] execute (reply): {t_exec - t0_exec:.2f}s")
                _handle_result(res)
                return

        elif is_short and any(w in t_lower for w in ["yes", "yeah", "yep", "correct"]):
            t0_exec = time.time()
            walkthrough_idx = _pending_confirmation.get("walkthrough_index") if kind == "send_email" else None
            
            if kind == "app_suggestion":
                intent = {
                    "action": _pending_confirmation["original_action"],
                    "params": _pending_confirmation["original_params"]
                }
                _pending_confirmation = None
                print("[debug] _pending_confirmation CLEARED in app_suggestion confirm")
                result = execute(intent)
            elif kind == "send_email":
                from executor import _send_email_confirmed
                result = _send_email_confirmed(
                    _pending_confirmation["to"], _pending_confirmation["subject"], _pending_confirmation["body"]
                )
                _pending_confirmation = None
                print("[debug] _pending_confirmation CLEARED in send_email confirm")
            t_exec = time.time()
            print(f"[timing] execute (confirmed): {t_exec - t0_exec:.2f}s")
            
            _handle_result(result)
            
            if walkthrough_idx is not None:
                import executor
                next_idx = walkthrough_idx + 1
                if next_idx < len(executor._last_unread_emails):
                    res = executor.MAIL_WALKTHROUGH["announce"](next_idx)
                    _handle_result(res)
                else:
                    msg = "That's all your unread emails."
                    print(msg)
                    t0_speak = time.time()
                    speak(msg)
                    print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
            return
        elif is_short and any(w in t_lower for w in ["no", "nope", "cancel", "never mind"]):
            walkthrough_idx = _pending_confirmation.get("walkthrough_index") if kind == "send_email" else None
            if kind == "send_email":
                msg = "Okay, I won't send it."
            else:
                speak("Okay, I won't do that.")
                _pending_confirmation = None
                print("[debug] _pending_confirmation CLEARED in generic confirmation 'no'")
            print(msg)
            
            t0_speak = time.time()
            speak(msg)
            print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
            
            if walkthrough_idx is not None:
                import executor
                next_idx = walkthrough_idx + 1
                if next_idx < len(executor._last_unread_emails):
                    res = executor.MAIL_WALKTHROUGH["announce"](next_idx)
                    _handle_result(res)
                else:
                    msg = "That's all your unread emails."
                    print(msg)
                    t0_speak = time.time()
                    speak(msg)
                    print(f"[timing] speak: {time.time() - t0_speak:.2f}s")
            return
        elif kind == "send_email":
            t0_exec = time.time()
            from executor import revise_email
            result = revise_email(
                _pending_confirmation["to"],
                _pending_confirmation["subject"],
                _pending_confirmation["body"],
                text,
                walkthrough_index=_pending_confirmation.get("walkthrough_index")
            )
            _pending_confirmation = None
            print("[debug] _pending_confirmation CLEARED before sending revised email result")
            t_exec = time.time()
            print(f"[timing] execute (edit): {t_exec - t0_exec:.2f}s")
            _handle_result(result)
            return
        else:
            _pending_confirmation = None
            print("[debug] _pending_confirmation CLEARED due to unknown kind")

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


def _handle_command_impl():
    try:
        t0 = time.time()
        audio = record_while_held(HOTKEY)
        t_record = time.time()
        print(f"[timing] record_while_held: {t_record - t0:.2f}s")
        
        _process_audio(audio, t_record)
    except Exception as e:
        print(f"Unexpected error in _handle_command_impl: {e}")
        traceback.print_exc()


def _handle_wake_word_command():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        t0 = time.time()
        audio = _record_until_silence()
        t_record = time.time()
        print(f"[timing] record_until_silence: {t_record - t0:.2f}s")
        
        _process_audio(audio, t_record)
    except Exception as e:
        print(f"Unexpected error in _handle_wake_word_command: {e}")
        traceback.print_exc()
    finally:
        _busy_lock.release()
        pythoncom.CoUninitialize()



def handle_command():
    global _last_print_time
    
    print(f"[debug] handle_command called, lock_held={_busy_lock.locked()}, is_speaking={tts.is_speaking()}")

    if _busy_lock.locked():
        if tts.is_speaking():
            print("[debug] branch: INTERRUPT")
            print("[debug] calling tts.stop_speaking()...")
            tts.stop_speaking()
            print("[debug] tts.stop_speaking() returned. Polling for lock...")
            
            # poll in a short loop (up to 0.5s, checking every 0.05s) for _busy_lock to become available
            t0 = time.time()
            while _busy_lock.locked() and (time.time() - t0) < 0.5:
                time.sleep(0.05)
            print(f"[debug] polling finished. lock freed={not _busy_lock.locked()}")
        else:
            print("[debug] branch: STILL_PROCESSING (locked, but not speaking)")
            
    if not _busy_lock.acquire(blocking=False):
        if _is_recording:
            # Silently ignore OS auto-repeat events while the user is intentionally holding the key
            return
            
        if time.time() - _last_print_time > 2.0:
            print("Still processing the previous command, ignoring this press.")
            _last_print_time = time.time()
        return
        
    print("[debug] branch: NORMAL_START")
        
    def _run_and_release():
        pythoncom.CoInitialize()
        try:
            _handle_command_impl()
        finally:
            _busy_lock.release()
            pythoncom.CoUninitialize()
            
    threading.Thread(target=_run_and_release, daemon=True).start()


_seen_message_ids = set()

def _mail_poll_loop():
    global _seen_message_ids
    import time as _time
    first_run = True
    while True:
        _time.sleep(60)
        try:
            resp = requests.get(f"{BRAIN_URL}/gmail/unread", timeout=20)
            resp.raise_for_status()
            emails = resp.json().get("emails", [])
        except Exception:
            continue  # network hiccup, just try again next cycle

        current_ids = {e["id"] for e in emails}
        if first_run:
            _seen_message_ids = current_ids
            first_run = False
            continue

        new_ones = [e for e in emails if e["id"] not in _seen_message_ids]
        _seen_message_ids = current_ids

        if new_ones and not tts.is_speaking() and not _busy_lock.locked() and _pending_confirmation is None and _pending_clarification is None:
            for e in new_ones[:3]:
                sender = e["from"].split("<")[0].strip()
                msg = f"New email from {sender}: {e['subject']}."
                print(f"[auto-announce] {msg}")
                tts.speak(msg)


def main():
    _init_models_and_stream()
    threading.Thread(target=_mail_poll_loop, daemon=True).start()
    print(f"Ready. Hold {HOTKEY} and speak a command. Ctrl+C to quit.")
    keyboard.add_hotkey(HOTKEY, handle_command, suppress=False, trigger_on_release=False)
    keyboard.wait()


if __name__ == "__main__":
    main()
