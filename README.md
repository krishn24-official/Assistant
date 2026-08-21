# Voice Assistant (Windows + Android) — "Alexa/Siri" style personal assistant

A voice-controlled assistant that opens apps, searches the web, and drafts
messages/emails — built as a small distributed system rather than a single
script, so it doubles as an AI-engineering portfolio piece.

## Architecture

```
 [Windows client]  --audio/text-->  [Brain: FastAPI service]  --tool schema-->  [LLM providers]
        |                                    |                                (Groq / Anthropic / Mistral,
        |                                    |                                 free tiers, auto-fallback)
   executes intent                     structured intent JSON
   (open app, search,                        |
   draft email...)                     [Style memory: SQLite]
                                        (per-contact message history
                                         used as few-shot examples)

 [Android client]  ---- same brain API over HTTP ----^
```

The **brain** is the only place "AI" happens: speech-to-text, intent parsing
via LLM tool-calling, and style personalization. Both clients are thin —
they just capture voice/text, call the brain, and execute whatever
structured action comes back. This is what lets one Python service power
both a Windows app and an Android app.

## Why this design (for interviews)

- **Tool-calling / structured output**: the LLM never free-types a response —
  it must return one of a fixed set of JSON actions (`open_app`, `web_search`,
  `draft_email`, `draft_message`, `set_reminder`). This is the same pattern
  production agent systems use.
- **Multi-provider LLM routing with fallback**: free tiers rate-limit fast.
  `llm_providers.py` tries providers in order (e.g. Groq → Mistral →
  Anthropic) and fails over automatically — a resilience pattern worth
  mentioning in interviews.
- **Personalization without training a model**: `memory.py` stores the
  user's actual sent messages per contact. When drafting a new message, the
  brain retrieves the last few examples for that contact and puts them in
  the prompt as few-shot style references ("here's how this user writes to
  their mom vs. their manager"). Cheap, fast, and effective for a v1.

## Roadmap for the ML personalization piece

**v1 (included here):** few-shot prompting from a SQLite history table —
simplest thing that could work, ships today.

**v2 (later):** embed each past message (e.g. with a small sentence-transformer
or the LLM provider's embedding endpoint), store in a vector index, and
retrieve the *most similar* past messages to the current draft context
instead of just the most recent — better style matching as history grows.

**v3 (later, optional):** fine-tune a small local adapter (e.g. LoRA on a
small open model) on the user's accumulated message corpus if you want a
genuinely custom "writes like you" model instead of prompting a general one.

## Setup

```bash
cd brain
pip install -r requirements.txt
cp .env.example .env   # fill in whichever free-tier API keys you have
uvicorn main:app --reload --port 8008
```

Then, on Windows:

```bash
cd windows_client
pip install -r requirements.txt
python hotkey_listener.py
```

Hold the hotkey (default `ctrl+alt+space`), speak a command, release —
it transcribes, sends it to the brain, executes the resulting action, and
speaks a confirmation back.

## Project layout

```
brain/
  main.py            FastAPI app: /intent, /draft-message, /remember, /style
  llm_providers.py    Unified interface over Groq / Anthropic / Mistral, with fallback
  schemas.py          Tool/action schema shared by all providers
  memory.py           SQLite-backed message history + style retrieval
  config.py           Env-based settings
  requirements.txt

windows_client/
  executor.py          Maps a structured intent -> a real Windows action
  hotkey_listener.py    Push-to-talk loop: record -> STT -> brain -> execute -> speak
  tts.py                Text-to-speech wrapper
  requirements.txt
```

## Android (next phase)

Kotlin app with an `AccessibilityService` + a mic button. It POSTs audio (or
text, if using on-device STT) to the same `brain` `/intent` endpoint over
your LAN or a small cloud deployment, then executes the returned action
locally via `Intent`s (open app, share sheet for SMS/Gmail) and the
Accessibility Service (filling compose fields). Not scaffolded yet —
happy to do this once the Windows side is working end to end.
